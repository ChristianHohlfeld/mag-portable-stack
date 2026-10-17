// Native, ownership-checked libvirt lifecycle using the existing warm golden
// image.
#include "common.hpp"
#include <grp.h>
#include <iomanip>
#include <regex>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
using namespace mag;
static fs::path root = env("MAGBOT_ROOT", "/var/lib/libvirt/images/magbot");
static std::string valid(const std::string &n) {
  if (!std::regex_match(n, std::regex("[a-z][a-z0-9-]{0,31}")) ||
      n.back() == '-')
    throw std::runtime_error("Invalid bot ID");
  return n;
}
static std::string domain(const std::string &n) { return "magbot-" + valid(n); }
static fs::path dir(const std::string &n) {
  auto d = root / "bots" / valid(n);
  if (fs::is_symlink(root) || fs::is_symlink(root / "bots") ||
      fs::is_symlink(d) || fs::is_symlink(d / "metadata.json"))
    throw std::runtime_error("Unsafe registry symlink");
  return d;
}
static std::string virsh(const std::vector<std::string> &a, int t = 30) {
  std::vector<std::string> v{"/usr/bin/virsh", "-c", "qemu:///system"};
  v.insert(v.end(), a.begin(), a.end());
  return checked(v, t);
}
static bool exists(const std::string &n) {
  auto r =
      run({"/usr/bin/virsh", "-c", "qemu:///system", "dominfo", domain(n)}, 10);
  return r.code == 0;
}
static Val marker(const std::string &n) {
  auto v = json(read(dir(n) / "metadata.json"));
  if (str(v, "id") != n || str(v, "domain") != domain(n))
    throw std::runtime_error("Registry ownership mismatch");
  return v;
}
static std::vector<Val> registry() {
  std::vector<Val> rows;
  if (!fs::exists(root / "bots"))
    return rows;
  for (auto &e : fs::directory_iterator(root / "bots")) {
    if (fs::is_symlink(e))
      throw std::runtime_error("Unsafe registry entry");
    if (fs::exists(e.path() / "metadata.json"))
      rows.push_back(marker(e.path().filename()));
  }
  std::sort(rows.begin(), rows.end(), [](const Val &a, const Val &b) {
    return str(a, "id") < str(b, "id");
  });
  return rows;
}
static Val cfg() {
  Val v = Val::object();
  for (auto p : std::vector<std::pair<std::string, double>>{
           {"max_bots", 4},
           {"max_total_vcpus", 8},
           {"max_total_ram_gib", 24},
           {"min_host_available_ram_gib", 64},
           {"min_host_free_disk_gib", 100},
           {"max_host_load_per_cpu", .75},
           {"default_vcpus", 2},
           {"default_ram_gib", 4},
           {"default_disk_gib", 20},
           {"max_bot_vcpus", 4},
           {"max_bot_ram_gib", 8},
           {"max_bot_disk_gib", 32},
           {"disk_bytes_sec", 40000000},
           {"disk_iops_sec", 400}})
    v[p.first] = p.second;
  v["network"] = "default";
  v["base_url"] = "";
  fs::path p = env("MAGBOT_CONFIG", "/etc/magbot/config.json");
  if (fs::exists(p)) {
    auto loaded = json(read(p));
    for (auto &[k, val] : *loaded.obj) {
      if (!v.find(k))
        throw std::runtime_error("Unknown VM configuration: " + k);
      v[k] = val;
    }
  }
  for (auto &[k, val] : *v.obj)
    if (k != "network" && k != "base_url" &&
        (val.type != Val::NUM || val.asNum() <= 0))
      throw std::runtime_error("Invalid VM configuration: " + k);
  if (!std::regex_match(str(v, "network"), std::regex("[A-Za-z0-9_.-]+")))
    throw std::runtime_error("Invalid network");
  return v;
}
static Val qga(const std::string &n, Val command, int timeout = 10) {
  auto v = json(virsh({"qemu-agent-command", domain(n), mj::dump(command),
                       "--timeout", std::to_string(timeout)},
                      timeout + 5));
  if (v.find("error"))
    throw std::runtime_error(mj::dump(*v.find("error")));
  if (!v.find("return"))
    throw std::runtime_error("Invalid guest agent reply");
  return *v.find("return");
}
static volatile sig_atomic_t payload_cancelled = 0;
static Result guest(const std::string &n, const std::vector<std::string> &argv,
                    int seconds = 120) {
  Val cmd = Val::object();
  cmd["execute"] = "guest-exec";
  Val args = Val::object();
  args["path"] = argv.at(0);
  args["capture-output"] = true;
  Val av = Val::array();
  for (size_t i = 1; i < argv.size(); ++i)
    av.push(argv[i]);
  args["arg"] = av;
  cmd["arguments"] = args;
  long pid = num(qga(n, cmd), "pid");
  auto end = std::chrono::steady_clock::now() + std::chrono::seconds(seconds);
  while (std::chrono::steady_clock::now() < end) {
    if (cancelable && payload_cancelled) {
      // The command runs in its own setsid process group inside the VM.
      Val sig = Val::object(), arg = Val::object(), av = Val::array();
      sig["execute"] = "guest-exec";
      arg["path"] = "/bin/kill";
      av.push("-TERM"); av.push("--"); av.push("-" + std::to_string(pid));
      arg["arg"] = av; sig["arguments"] = arg;
      try { qga(n, sig, 3); } catch (...) {}
      throw std::runtime_error("Abgebrochen (Stop)");
    }
    Val q = Val::object();
    q["execute"] = "guest-exec-status";
    Val a = Val::object();
    a["pid"] = pid;
    q["arguments"] = a;
    auto st = qga(n, q);
    auto exited = st.find("exited");
    if (exited && exited->asBool())
      return {(int)num(st, "exitcode", 1),
              unbase64(str(st, "out-data")) + unbase64(str(st, "err-data"))};
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  throw std::runtime_error("Guest command timed out");
}

// Large guest payload transport. qemu-guest-agent receives 24 KiB chunks, never
// an enormous argv/QGA JSON argument. The stage is created by guest user bot
// with mode 0600 and removed after dispatch. No host shell access is exposed.
static std::string base64(const std::string &in);

static void payload_signal(int) { payload_cancelled = 1; }

static std::string from_stdin(size_t limit = 32u * 1024 * 1024) {
  std::string data;
  char b[16384];
  while (std::cin) {
    std::cin.read(b, sizeof b);
    auto n = std::cin.gcount();
    if (n <= 0) break;
    if (data.size() + (size_t)n > limit)
      throw std::runtime_error("Guest input exceeds 32 MiB; split into append_file calls");
    data.append(b, (size_t)n);
  }
  if (std::cin.bad()) throw std::runtime_error("Failed to read guest payload from stdin");
  return data;
}

static void stage_payload(const std::string &n, const std::string &file,
                          const std::string &data) {
  Val cmd = Val::object(), args = Val::object();
  cmd["execute"] = "guest-file-open";
  args["path"] = file; args["mode"] = "w";
  cmd["arguments"] = args;
  Val opened = qga(n, cmd);
  long handle = opened.type == Val::NUM ? (long)opened.asNum() : num(opened, "handle", -1);
  if (handle < 0) throw std::runtime_error("Guest file-open returned no handle");
  try {
    size_t at = 0;
    while (at < data.size()) {
      if (payload_cancelled) throw std::runtime_error("Abgebrochen (Stop)");
      const size_t count = std::min<size_t>(24576, data.size() - at);
      cmd = Val::object(); args = Val::object();
      cmd["execute"] = "guest-file-write";
      args["handle"] = handle;
      args["buf-b64"] = base64(data.substr(at, count));
      cmd["arguments"] = args;
      long written = num(qga(n, cmd), "count", -1);
      if (written <= 0 || (size_t)written > count)
        throw std::runtime_error("Guest file-write incomplete");
      at += (size_t)written;
    }
    cmd = Val::object(); args = Val::object();
    cmd["execute"] = "guest-file-flush"; args["handle"] = handle; cmd["arguments"] = args;
    qga(n, cmd);
  } catch (...) {
    cmd = Val::object(); args = Val::object();
    cmd["execute"] = "guest-file-close"; args["handle"] = handle; cmd["arguments"] = args;
    try { qga(n, cmd); } catch (...) {}
    throw;
  }
  cmd = Val::object(); args = Val::object();
  cmd["execute"] = "guest-file-close"; args["handle"] = handle; cmd["arguments"] = args;
  qga(n, cmd);
}

// Executed as unprivileged bot. Its HOME is /home/bot; only the private VM
// home can be written. A temp sibling + fsync + replace makes append atomic.
static const char *ATOMIC_FILE_PY = R"MAGPY(import os,sys,tempfile,shutil,stat
src,path,mode=sys.argv[1:]
home=os.path.realpath(os.path.expanduser("~"))
if not path: raise ValueError("empty target")
if not os.path.isabs(path): path=os.path.join(home,path)
path=os.path.realpath(path)
if path==home or not path.startswith(home+os.sep): raise ValueError("target outside bot home")
parent=os.path.dirname(path)
os.makedirs(parent,mode=0o700,exist_ok=True)
fd,tmp=tempfile.mkstemp(prefix=".mag-write-",dir=parent)
try:
    with os.fdopen(fd,"wb") as output:
        if mode=="--append" and os.path.exists(path):
            with open(path,"rb") as old: shutil.copyfileobj(old,output,1024*1024)
        with open(src,"rb") as data: shutil.copyfileobj(data,output,1024*1024)
        output.flush()
        os.fsync(output.fileno())
    if os.path.exists(path): os.chmod(tmp,stat.S_IMODE(os.stat(path).st_mode))
    os.replace(tmp,path)
    dfd=os.open(parent,os.O_DIRECTORY|os.O_RDONLY)
    try: os.fsync(dfd)
    finally: os.close(dfd)
    print("saved",path,os.path.getsize(path),"bytes")
finally:
    if os.path.exists(tmp): os.unlink(tmp)
)MAGPY";

static Result guest_payload(const std::string &n, const std::string &data,
                            const std::string &target = "",
                            const std::string &mode = "", int seconds = 120) {
  payload_cancelled = 0;
  struct sigaction sa{}, old{};
  sa.sa_handler = payload_signal;
  sigemptyset(&sa.sa_mask);
  sigaction(SIGTERM, &sa, &old);
  std::string staged;
  try {
    auto mk = guest(n, {"/usr/sbin/runuser", "-u", "bot", "--", "/usr/bin/mktemp",
                        "/home/bot/.mag-input-XXXXXXXX"}, 15);
    if (mk.code || !std::regex_match(trim(mk.out),
                      std::regex("/home/bot/\\.mag-input-[A-Za-z0-9]+")))
      throw std::runtime_error("Cannot create secure guest staging file");
    staged = trim(mk.out);
    stage_payload(n, staged, data);
    if (payload_cancelled) throw std::runtime_error("Abgebrochen (Stop)");
    Result result;
    if (mode.empty()) {
      const std::string log = "/home/bot/.mag-shell/" + randomhex() + ".log";
      // Full output stays in the guest; send a bounded preview and a clear
      // pointer. This avoids QGA capture-output and model-context overflow.
      const std::string shell = R"MAGSH(
cd /home/bot || exit 1
export PLAYWRIGHT_BROWSERS_PATH=/opt/magbot/browsers
umask 077
mkdir -p /home/bot/.mag-shell
bash "$1" > "$2" 2>&1
rc=$?
bytes=$(wc -c < "$2")
printf '[Full output: %s (%s bytes)]\n' "$2" "$bytes"
head -c 32000 "$2"
if [ "$bytes" -gt 32000 ]; then printf '\n[preview truncated; full log in guest file]\n'; fi
exit "$rc"
)MAGSH";
      result = guest(n, {"/usr/bin/setsid", "/usr/sbin/runuser", "-u", "bot",
                         "--", "/bin/bash", "-c", shell, "mag-shell", staged, log},
                     seconds, true);
    } else {
      result = guest(n, {"/usr/bin/setsid", "/usr/sbin/runuser", "-u", "bot",
                         "--", "/usr/bin/python3", "-c", ATOMIC_FILE_PY,
                         staged, target, mode}, seconds, true);
    }
    try { guest(n, {"/bin/rm", "-f", "--", staged}, 10); } catch (...) {}
    sigaction(SIGTERM, &old, nullptr);
    return result;
  } catch (...) {
    if (!staged.empty()) {
      try { guest(n, {"/bin/rm", "-f", "--", staged}, 10); } catch (...) {}
    }
    sigaction(SIGTERM, &old, nullptr);
    throw;
  }
}

static std::string state(const std::string &n) {
  try {
    auto s = trim(virsh({"domstate", domain(n)}, 10));
    if (s != "running")
      return "stopped";
    auto r = guest(n, {"/usr/bin/test", "-f", "/var/lib/magbot/ready"}, 8);
    return r.code == 0 ? "ready" : "booting";
  } catch (...) {
    return "booting";
  }
}
static void admission(const Val &c, const std::vector<Val> &rows, int cpu,
                      int ram, int disk) {
  if (cpu < 1 || ram < 2 || disk < 12)
    throw std::runtime_error("Minimum VM size: 1 CPU, 2 GiB RAM, 12 GiB disk");
  if (cpu > num(c, "max_bot_vcpus") || ram > num(c, "max_bot_ram_gib") ||
      disk > num(c, "max_bot_disk_gib"))
    throw std::runtime_error("Per-VM resource limit");
  long cpus = cpu, rams = ram, disks = disk;
  for (auto &r : rows) {
    cpus += num(r, "vcpus");
    rams += num(r, "ram_gib");
    disks += num(r, "disk_gib");
  }
  if ((long)rows.size() >= num(c, "max_bots") ||
      cpus > num(c, "max_total_vcpus") || rams > num(c, "max_total_ram_gib"))
    throw std::runtime_error("VM admission budget exceeded");
  unsigned long long available = 0;
  std::istringstream mem(read("/proc/meminfo"));
  std::string line;
  while (std::getline(mem, line))
    if (line.rfind("MemAvailable:", 0) == 0)
      available = std::stoull(line.substr(13)) * 1024ULL;
  constexpr uint64_t G = 1024ULL * 1024 * 1024;
  if (available < (ram + num(c, "min_host_available_ram_gib")) * G)
    throw std::runtime_error("Host memory reserve");
  struct statvfs st {};
  if (statvfs(root.c_str(), &st))
    throw std::runtime_error("Disk check failed");
  if ((uint64_t)st.f_bavail * st.f_frsize <
      (disks + num(c, "min_host_free_disk_gib")) * G)
    throw std::runtime_error("Host disk reserve");
  double load;
  getloadavg(&load, 1);
  if (load >
      c.find("max_host_load_per_cpu")->asNum() * sysconf(_SC_NPROCESSORS_ONLN))
    throw std::runtime_error("Host load too high");
}
static std::vector<std::string>
gitea_args(const std::vector<std::string> &args) {
  std::vector<std::string> command{"/usr/sbin/runuser",
                                   "-u",
                                   "git",
                                   "--",
                                   "/usr/local/bin/gitea",
                                   "--config",
                                   "/etc/gitea/app.ini",
                                   "admin",
                                   "user"};
  command.insert(command.end(), args.begin(), args.end());
  return command;
}
static void prepare_browser(const std::string &n);
static void provision_git(const std::string &n) {
  auto record = marker(n);
  if (!str(record, "git_user").empty())
    return;
  auto username = domain(n), password = randomhex() + randomhex();
  auto created = run(
      gitea_args({"create", "--username", username, "--email",
                  username + "@mag.local", "--user-type", "User", "--password",
                  password, "--admin", "--must-change-password=false"}),
      30);
  if (created.code)
    throw std::runtime_error("Gitea bot account creation failed");
  // Register ownership before provisioning: retry/teardown can revoke the
  // account.
  record["git_user"] = username;
  write(dir(n) / "metadata.json", mj::dump(record));
  auto end = std::chrono::steady_clock::now() + std::chrono::seconds(90);
  while (state(n) != "ready") {
    if (std::chrono::steady_clock::now() > end)
      throw std::runtime_error("Guest not ready for Git provisioning");
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
  }
  std::string credential =
      "http://" + username + ":" + password + "@192.168.122.1:3001\n";
  std::string script =
      "umask 077; printf %s " + quote(credential) +
      " > /home/bot/.git-credentials; git config --global credential.helper "
      "store; git config --global user.name " +
      quote(username) + "; git config --global user.email " +
      quote(username + "@mag.local") + "; printf %s " +
      quote("Gitea: http://192.168.122.1:3001 . Your dedicated account is an "
            "administrator. Use git clone/push and Gitea API with your "
            "credential. Never share credentials in chat.\n") +
      " > /home/bot/GIT_ACCESS.md";
  auto result = guest(
      n, {"/usr/sbin/runuser", "-u", "bot", "--", "/bin/bash", "-lc", script},
      30);
  if (result.code)
    throw std::runtime_error("Guest Git configuration failed");
}
static void teardown(const std::string &n) {
  auto d = dir(n);
  if (!fs::exists(d)) {
    if (exists(n))
      throw std::runtime_error("Orphan VM without ownership marker");
    return;
  }
  auto owned = marker(n);
  if (exists(n)) {
    auto s = trim(virsh({"domstate", domain(n)}));
    if (s == "running" || s == "paused" || s == "idle")
      virsh({"destroy", domain(n)});
    auto r = run({"/usr/bin/virsh", "-c", "qemu:///system", "undefine",
                  domain(n), "--nvram"});
    if (r.code && exists(n))
      virsh({"undefine", domain(n)});
    if (exists(n))
      throw std::runtime_error("VM remains defined; refusing disk deletion");
  }
  auto git_user = str(owned, "git_user");
  if (!git_user.empty()) {
    if (git_user != domain(n))
      throw std::runtime_error("Gitea ownership mismatch");
    auto result =
        run(gitea_args({"delete", "--username", git_user, "--purge"}), 30);
    if (result.code)
      throw std::runtime_error(
          "Could not revoke bot Git account; registry retained");
  }
  fs::remove_all(d);
}
static void prepare_browser(const std::string &n) {
  const std::string script = R"SH(set -e
cat > /etc/apparmor.d/mag-chromium <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>
profile mag-chromium /opt/magbot/browsers/chromium-*/chrome-linux/chrome flags=(unconfined) {
 userns,
}
profile mag-google-chrome /opt/google/chrome/chrome flags=(unconfined) {
 userns,
}
PROFILE
# Real Google Chrome (stable) from Google's signed apt repo instead of the
# Playwright "Chrome for Testing" build (userAgentData brand, auto updates).
if [ ! -x /opt/google/chrome/chrome ]; then
  [ -r /etc/profile.d/mag-audit-proxy.sh ] && . /etc/profile.d/mag-audit-proxy.sh
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor --yes -o /etc/apt/keyrings/google-chrome.gpg
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
  apt-get -o Acquire::Retries=2 update -qq -o Dir::Etc::sourcelist=sources.list.d/google-chrome.list -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq google-chrome-stable fonts-liberation
fi
locale -a | grep -qi de_DE.utf8 || { locale-gen de_DE.UTF-8 >/dev/null 2>&1 || true; }
apparmor_parser -r /etc/apparmor.d/mag-chromium
)SH";
  auto result = guest(n, {"/bin/bash", "-lc", script}, 900);
  if (result.code)
    throw std::runtime_error("Browser namespace profile failed");
}
// Central web audit (ops/audit): every guest reaches the web only through the
// logging proxy on the VM gateway and trusts its CA. Inputs are installed by
// ops/audit/install-audit.sh into /etc/mag-audit/vm/; without them (audit not
// installed) the guest stays unchanged. Idempotent: a checksum of CA, script
// and proxy URL is stored in the guest and identical runs return early.
static std::string base64(const std::string &in) {
  static const char *abc =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  size_t i = 0;
  for (; i + 2 < in.size(); i += 3) {
    unsigned v = ((unsigned char)in[i] << 16) |
                 ((unsigned char)in[i + 1] << 8) | (unsigned char)in[i + 2];
    out += abc[(v >> 18) & 63];
    out += abc[(v >> 12) & 63];
    out += abc[(v >> 6) & 63];
    out += abc[v & 63];
  }
  if (i < in.size()) {
    unsigned v = (unsigned char)in[i] << 16;
    if (i + 1 < in.size())
      v |= (unsigned char)in[i + 1] << 8;
    out += abc[(v >> 18) & 63];
    out += abc[(v >> 12) & 63];
    out += i + 1 < in.size() ? abc[(v >> 6) & 63] : '=';
    out += '=';
  }
  return out;
}
static std::string audit_proxy(const std::string &n) {
  const fs::path vmdir = env("MAG_AUDIT_VM_DIR", "/etc/mag-audit/vm");
  if (!fs::exists(vmdir / "mag-audit-ca.crt") ||
      !fs::exists(vmdir / "vm-proxy-setup.sh") ||
      !fs::exists(vmdir / "proxy.env"))
    return "skipped (central audit not installed)";
  std::string proxy;
  std::istringstream pe(read(vmdir / "proxy.env"));
  for (std::string line; std::getline(pe, line);)
    if (line.rfind("MAG_PROXY=", 0) == 0)
      proxy = trim(line.substr(10));
  if (!std::regex_match(proxy, std::regex("http://[0-9.]+:[0-9]{2,5}")))
    throw std::runtime_error("Invalid audit proxy in proxy.env");
  const std::string wrapper = R"SH(set -e
sum=$(printf '%s\n%s\n%s' "$1" "$2" "$3" | sha256sum | cut -d' ' -f1)
if [ "$(cat /var/lib/magbot/audit-proxy.sum 2>/dev/null)" = "$sum" ]; then
  echo "audit proxy unchanged"; exit 0
fi
t=$(mktemp -d)
printf %s "$1" | base64 -d > "$t/ca.crt"
printf %s "$2" | base64 -d > "$t/setup.sh"
bash "$t/setup.sh" "$3" "$t/ca.crt"
rm -rf "$t"
install -d -m 0755 /var/lib/magbot
printf %s "$sum" > /var/lib/magbot/audit-proxy.sum
)SH";
  auto r = guest(n,
                 {"/bin/bash", "-c", wrapper, "mag-audit-proxy",
                  base64(read(vmdir / "mag-audit-ca.crt")),
                  base64(read(vmdir / "vm-proxy-setup.sh")), proxy},
                 600);
  if (r.code)
    throw std::runtime_error("Audit proxy setup failed: " +
                             r.out.substr(r.out.size() > 800
                                              ? r.out.size() - 800
                                              : 0));
  return trim(r.out);
}
static void spawn(const std::string &n, int cpu, int ram, int disk,
                  const Val &c) {
  auto d = dir(n);
  if (fs::exists(d) || exists(n))
    throw std::runtime_error("Bot already exists");
  auto rows = registry();
  std::istringstream domains(virsh({"list", "--all", "--name"}));
  std::string name;
  while (std::getline(domains, name)) {
    if (name.rfind("magbot-", 0) == 0 &&
        !std::any_of(rows.begin(), rows.end(),
                     [&](const Val &r) { return str(r, "domain") == name; }))
      throw std::runtime_error("Orphan domain: " + name);
  }
  admission(c, rows, cpu, ram, disk);
  auto golden = root / "base/golden.qcow2";
  if (!fs::is_regular_file(golden) || fs::is_symlink(golden) ||
      fs::file_size(golden) < 100 * 1024 * 1024)
    throw std::runtime_error("Warm golden image required");
  if ((fs::status(golden).permissions() & fs::perms::owner_write) !=
      fs::perms::none)
    throw std::runtime_error("Golden image must be read-only");
  auto network = str(c, "network");
  auto info = virsh({"net-info", network});
  if (!std::regex_search(info, std::regex("Active:\\s+yes")))
    virsh({"net-start", network});
  virsh({"net-autostart", network});
  fs::create_directories(d);
  chmod(d.c_str(), 0711);
  Val r = Val::object();
  r["id"] = n;
  r["domain"] = domain(n);
  r["vcpus"] = cpu;
  r["ram_gib"] = ram;
  r["disk_gib"] = disk;
  r["created_at"] = (long)time(nullptr);
  r["state"] = "provisioning";
  write(d / "metadata.json", mj::dump(r));
  try {
    checked({"qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b",
             golden.string(), (d / "root.qcow2").string(),
             std::to_string(disk) + "G"});
    auto g = getgrnam("kvm");
    if (!g || chown((d / "root.qcow2").c_str(), 0, g->gr_gid) ||
        chmod((d / "root.qcow2").c_str(), 0660))
      throw std::runtime_error("Disk permissions failed");
    // Golden contains the browser and guest agent; cloud-init only refreshes
    // identity.
    write(d / "user-data",
          "#cloud-config\nhostname: " + domain(n) +
              "\nssh_deletekeys: true\nssh_genkeytypes: [rsa, ecdsa, "
              "ed25519]\nusers:\n  - name: bot\n    shell: /bin/bash\n    "
              "lock_passwd: true\n    sudo: false\nruncmd:\n  - [systemctl, "
              "enable, --now, qemu-guest-agent]\n  - [sh, -c, 'mkdir -p "
              "/var/lib/magbot && touch /var/lib/magbot/ready']\n");
    write(d / "meta-data", "instance-id: " + domain(n) + "-" + randomhex() +
                               "\nlocal-hostname: " + domain(n) + "\n");
    write(d / "network-config",
          "version: 2\nethernets:\n  default:\n    match:\n      name: ens*\n  "
          "  dhcp4: true\n    dhcp6: false\n    dhcp-identifier: mac\n");
    checked({"cloud-localds", "--network-config",
             (d / "network-config").string(), (d / "seed.iso").string(),
             (d / "user-data").string(), (d / "meta-data").string()});
    chmod((d / "seed.iso").c_str(), 0644);
    checked({"virt-install",
             "--connect",
             "qemu:///system",
             "--virt-type",
             "kvm",
             "--name",
             domain(n),
             "--memory",
             std::to_string(ram * 1024),
             "--vcpus",
             std::to_string(cpu),
             "--cpu",
             "host-passthrough",
             "--import",
             "--noautoconsole",
             "--osinfo",
             "detect=off,require=off",
             "--graphics",
             "none",
             "--network",
             "network=" + network + ",model=virtio",
             "--disk",
             "path=" + (d / "root.qcow2").string() + ",format=qcow2,bus=virtio",
             "--disk",
             "path=" + (d / "seed.iso").string() + ",device=cdrom,readonly=on",
             "--channel",
             "unix,target.type=virtio,target.name=org.qemu.guest_agent.0",
             "--boot",
             "hd",
             "--check",
             "all=on"},
            180);
    virsh({"schedinfo", domain(n), "--set", "vcpu_period=100000", "--set",
           "vcpu_quota=" + std::to_string(cpu * 100000), "--live", "--config"});
    virsh({"blkdeviotune", domain(n), "vda", "--total-bytes-sec",
           std::to_string(num(c, "disk_bytes_sec")), "--total-iops-sec",
           std::to_string(num(c, "disk_iops_sec")), "--live", "--config"});
    virsh({"autostart", domain(n)});
    r["state"] = "booting";
    write(d / "metadata.json", mj::dump(r));
    provision_git(n);
    prepare_browser(n);
    try {
      std::cerr << "audit-proxy: " << audit_proxy(n) << '\n';
    } catch (const std::exception &e) {
      // The bot stays usable; its web access is blocked until the proxy
      // setup succeeds (sudo magbot audit-proxy <id>).
      std::cerr << "WARNING: " << e.what() << '\n';
    }
  } catch (...) {
    teardown(n);
    throw;
  }
  std::cout << "Created " << domain(n) << " from golden image\n";
}
int main(int argc, char **argv) {
  try {
    setenv("LC_ALL", "C", 1);
    if (geteuid() != 0)
      throw std::runtime_error("Run with sudo");
    if (argc < 2)
      throw std::runtime_error(
          "Usage: magbot status|inspect|exec|spawn|despawn|doctor|audit-proxy "
          "[ID]");
    auto c = cfg();
    std::string action = argv[1], n = argc > 2 ? argv[2] : "";
    if (!n.empty())
      valid(n);
    fs::create_directories(root / "bots");
    chmod(root.c_str(), 0711);
    chmod((root / "bots").c_str(), 0711);
    if (action == "status") {
      for (auto &r : registry()) {
        auto id = str(r, "id");
        if (!n.empty() && id != n)
          continue;
        std::cout << std::left << std::setw(33) << id << std::setw(12)
                  << state(id) << num(r, "vcpus") << " vCPU  "
                  << num(r, "ram_gib") << " GiB RAM  " << num(r, "disk_gib")
                  << " GiB disk\n";
      }
      return 0;
    }
    if (action == "doctor") {
      if (!fs::exists("/dev/kvm"))
        throw std::runtime_error("KVM missing");
      std::cout << "KVM present, " << registry().size() << " registered bots\n";
      return 0;
    }
    if (n.empty())
      throw std::runtime_error("Bot ID required");
    if (action == "file-write") {
      marker(n);
      if (state(n) != "ready") throw std::runtime_error("Guest not ready");
      if (argc != 5 || (std::string(argv[4]) != "--write" &&
                        std::string(argv[4]) != "--append"))
        throw std::runtime_error("Usage: magbot file-write ID PATH --write|--append");
      std::string path = argv[3];
      if (path.empty() || path.size() > 4096)
        throw std::runtime_error("Invalid guest file path");
      auto r = guest_payload(n, from_stdin(), path, argv[4], 180);
      std::cout << r.out;
      return r.code;
    }
    if (action == "exec" && argc == 4 && std::string(argv[3]) == "--stdin") {
      marker(n);
      if (state(n) != "ready") throw std::runtime_error("Guest not ready");
      auto r = guest_payload(n, from_stdin(), "", "", 600);
      std::cout << r.out;
      return r.code;
    }
    if (action == "exec") {
      marker(n);
      if (state(n) != "ready")
        throw std::runtime_error("Guest not ready");
      std::string script = "cd /home/bot && export "
                           "PLAYWRIGHT_BROWSERS_PATH=/opt/magbot/browsers && ";
      int begin = 3;
      if (argc > 3 && std::string(argv[3]) == "--")
        ++begin;
      if (begin == argc)
        throw std::runtime_error("Guest command required");
      for (int i = begin; i < argc; ++i)
        script += quote(argv[i]) + " ";
      auto r = guest(n, {"/usr/sbin/runuser", "-u", "bot", "--", "/bin/bash",
                         "-lc", script});
      std::cout << r.out;
      return r.code;
    }
    if (action == "browser-sandbox") {
      marker(n);
      prepare_browser(n);
      return 0;
    }
    if (action == "audit-proxy") {
      marker(n);
      if (state(n) != "ready")
        throw std::runtime_error("Guest not ready");
      std::cout << audit_proxy(n) << '\n';
      return 0;
    }
    if (action == "git-sync") {
      marker(n);
      provision_git(n);
      prepare_browser(n);
      return 0;
    }
    if (action == "inspect") {
      auto r = marker(n);
      r["vm_defined"] = exists(n);
      r["vm_state"] = state(n);
      std::cout << mj::dump(r) << '\n';
      return 0;
    }
    int lock =
        open("/run/lock/magbot.lock", O_CREAT | O_WRONLY | O_CLOEXEC, 0600);
    if (lock < 0 || flock(lock, LOCK_EX))
      throw std::runtime_error("Lifecycle lock failed");
    if (action == "despawn") {
      teardown(n);
      std::cout << "Removed only target bot VM and private data\n";
    } else if (action == "spawn") {
      int cpu = num(c, "default_vcpus"), ram = num(c, "default_ram_gib"),
          disk = num(c, "default_disk_gib");
      for (int i = 3; i + 1 < argc; i += 2) {
        std::string arg = argv[i];
        int v = std::stoi(argv[i + 1]);
        if (arg == "--vcpus")
          cpu = v;
        else if (arg == "--ram")
          ram = v;
        else if (arg == "--disk")
          disk = v;
        else
          throw std::runtime_error("Unknown option");
      }
      spawn(n, cpu, ram, disk, c);
    } else
      throw std::runtime_error("Unknown lifecycle action");
    close(lock);
    return 0;
  } catch (const std::exception &e) {
    std::cerr << "ERROR: " << e.what() << '\n';
    return 1;
  }
}
