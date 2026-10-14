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
PROFILE
apparmor_parser -r /etc/apparmor.d/mag-chromium
)SH";
  auto result = guest(n, {"/bin/bash", "-lc", script}, 30);
  if (result.code)
    throw std::runtime_error("Browser namespace profile failed");
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
          "Usage: magbot status|inspect|exec|spawn|despawn|doctor [ID]");
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
    if (action == "privilege-on" || action == "privilege-off" ||
        action == "privilege-renew") {
      marker(n); // Enforce ownership before any privileged guest action.
      if (state(n) != "ready")
        throw std::runtime_error("Guest not ready for root lease change");
      std::string command;
      if (action == "privilege-renew") {
        command="systemctl is-active --quiet mag-root-shell.service && "
                "test -S /run/mag-root-shell.sock && "
                "date +%s > /run/mag-root-shell.heartbeat";
      }else if (action == "privilege-off") {
        command = "systemctl kill --kill-who=all mag-root-shell.service "
                  ">/dev/null 2>&1 || true; "
                  "systemctl stop mag-root-shell.service >/dev/null 2>&1 || true; "
                  "rm -f /run/mag-root-shell.sock /run/mag-root-shell.heartbeat /usr/local/sbin/mag-root-console; "
                  "if systemctl is-active --quiet mag-root-shell.service; then "
                  "echo 'root service still active'; exit 1; fi; "
                  "test ! -S /run/mag-root-shell.sock";
      } else {
        if (argc != 4 ||
            !std::regex_match(argv[3], std::regex("[A-Za-z0-9_-]{24,128}")))
          throw std::runtime_error("Invalid root lease token");
        const std::string url =
            "http://192.168.122.1:8767/tool/mag-root-console?token=" +
            std::string(argv[3]);
        command =
            "set -e; "
            "systemctl stop mag-root-shell.service >/dev/null 2>&1 || true; "
            "curl --fail --silent --show-error --connect-timeout 5 "
            "--max-time 25 " + quote(url) +
            " -o /usr/local/sbin/.mag-root-console-new; "
            "test -s /usr/local/sbin/.mag-root-console-new; "
            "install -o root -g root -m 0700 "
            "/usr/local/sbin/.mag-root-console-new "
            "/usr/local/sbin/mag-root-console; "
            "rm -f /usr/local/sbin/.mag-root-console-new; "
            "date +%s > /run/mag-root-shell.heartbeat; "
            "chmod 0600 /run/mag-root-shell.heartbeat; "
            "systemd-run --quiet --collect --unit=mag-root-shell "
            "--property=KillMode=control-group "
            "--property=TimeoutStopSec=2s "
            "/usr/local/sbin/mag-root-console; "
            "i=0; until test -S /run/mag-root-shell.sock; do "
            "i=$((i+1)); if test \"$i\" -ge 40; then "
            "echo 'root PTY socket unavailable'; exit 1; fi; sleep 0.1; "
            "done";
      }
      auto result = guest(n,{"/bin/bash","-lc",command},55);
      std::cout << result.out;
      return result.code;
    }
    if (action == "browser-sandbox") {
      marker(n);
      prepare_browser(n);
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
