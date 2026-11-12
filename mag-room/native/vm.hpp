// The bots' own KVM guests: shell with live output, atomic file writes, the
// guest address, and the per-VM browser controller. Everything runs through
// the two narrow sudo wrappers (magbot-guest-run, magbot-agent-ctl); the room
// never talks to libvirt itself and never touches another bot's VM.
#pragma once
#include "browser_recovery.hpp"
#include "http.hpp"
#include "room.hpp"

namespace mag::vm {

inline bool valid_name(const std::string &vm) {
  if (vm.size() < 6 || vm.size() > 12 || vm.compare(0, 5, "agent") != 0) return false;
  return vm.find_first_not_of("0123456789", 5) == std::string::npos;
}

struct ExecResult {
  int code = -1;
  std::string out, log_path; // out: what the command printed (bounded)
  bool cancelled = false, timed_out = false, failed_to_start = false;
};

class Guests {
public:
  explicit Guests(const Config &cfg) : cfg_(cfg) {
    master_ = trim(slurp(cfg.state_dir + "/token"));
    if (master_.size() < 24) {
      master_ = random_token(32);
      write_atomic(cfg.state_dir + "/token", master_ + "\n", 0600);
    }
  }

  // One secret per VM, derived from the room's master token: a VM can prove
  // which bot it is, and cannot act as another bot or drive its browser.
  std::string token(const std::string &vm) const {
    return hmac_sha256_hex(master_, "vm:" + vm).substr(0, 48);
  }
  // Which VM presented this bridge token ("" = none).
  std::string vm_for_token(const std::string &token, const std::vector<std::string> &vms) const {
    for (auto &vm : vms)
      if (ct_equal(token, this->token(vm))) return vm;
    return "";
  }
  std::string bridge(const std::string &path) const {
    return "http://" + cfg_.bridge_ip + ":" + std::to_string(cfg_.bridge_port) + path;
  }

  // Runs a bash script as user bot in the VM. With on_output the output is
  // relayed while the command runs.
  ExecResult exec(const std::string &vm, const std::string &script, int timeout_s, const Cancel *cancel = nullptr,
                  const std::function<void(const std::string &)> &on_output = {}) {
    ExecResult r;
    if (!valid_name(vm)) { r.failed_to_start = true; r.out = "Dieser Bot hat keine eigene VM."; return r; }
    timeout_s = std::max(1, std::min(timeout_s, 3600));
    // The helper marks its own records with 0x1e ("log <path> <bytes>",
    // "timeout <s>", "cancelled"); everything else is the command's output.
    std::string pending;
    auto feed = [&](const std::string &chunk, bool flush) {
      pending += chunk;
      std::string visible;
      for (;;) {
        const size_t mark = pending.find('\x1e');
        if (mark == std::string::npos) { visible += pending; pending.clear(); break; }
        visible += pending.substr(0, mark);
        pending.erase(0, mark);
        const size_t nl = pending.find('\n');
        if (nl == std::string::npos) {
          if (flush || pending.size() > 4200) { visible += pending; pending.clear(); }
          break;
        }
        std::istringstream in(pending.substr(1, nl - 1));
        std::string verb;
        in >> verb;
        if (verb == "log") in >> r.log_path;
        else if (verb == "timeout") r.timed_out = true;
        else if (verb == "cancelled") r.cancelled = true;
        else visible += pending.substr(0, nl + 1); // not ours: plain output
        pending.erase(0, nl + 1);
      }
      if (visible.empty()) return;
      if (r.out.size() < kMaxOut) r.out.append(visible, 0, kMaxOut - r.out.size());
      if (on_output) on_output(visible);
    };
    RunOptions opt;
    opt.timeout_s = timeout_s + 45;
    opt.cancel = cancel;
    opt.stdin_data = &script;
    opt.cap = 64;
    opt.on_output = [&](const std::string &chunk) { feed(chunk, false); };
    const RunResult run = mag::run(wrapper({cfg_.guest_run, vm, "--stdin", std::to_string(timeout_s), "--live"}), opt);
    feed("", true);
    r.code = run.code;
    r.cancelled = r.cancelled || run.cancelled;
    r.timed_out = r.timed_out || run.timed_out || run.code == 124;
    if (!run.spawned) { r.failed_to_start = true; r.out = "Start des VM-Befehls fehlgeschlagen."; }
    return r;
  }

  // Quiet variant for internal housekeeping.
  ExecResult quiet(const std::string &vm, const std::string &script, int timeout_s = 30) {
    return exec(vm, script, timeout_s, nullptr, {});
  }

  // Atomic write below /home/bot (the guest helper enforces the path).
  ExecResult write(const std::string &vm, const std::string &path, const std::string &content, bool append,
                   const Cancel *cancel = nullptr) {
    ExecResult r;
    if (!valid_name(vm)) { r.failed_to_start = true; r.out = "Dieser Bot hat keine eigene VM."; return r; }
    if (path.empty() || path.size() > 4096) { r.failed_to_start = true; r.out = "Dateipfad fehlt oder ist zu lang."; return r; }
    RunOptions opt;
    opt.timeout_s = 300;
    opt.cancel = cancel;
    opt.stdin_data = &content;
    const RunResult run = mag::run(wrapper({cfg_.guest_run, vm, append ? "--append" : "--write", path}), opt);
    r.code = run.code;
    r.out = run.out;
    r.cancelled = run.cancelled;
    r.timed_out = run.timed_out;
    return r;
  }

  // Guest address on the libvirt network (cached for a minute).
  std::string ip(const std::string &vm, bool refresh = false) {
    if (!valid_name(vm)) return "";
    {
      std::lock_guard<std::mutex> lk(mu_);
      const auto it = ips_.find(vm);
      if (!refresh && it != ips_.end() && now_s() - it->second.second < 60) return it->second.first;
    }
    RunOptions opt;
    opt.timeout_s = 16;
    const RunResult run = mag::run(wrapper({cfg_.agent_ctl, "ip", vm}), opt);
    std::string found;
    std::istringstream in(run.out);
    std::string word;
    while (in >> word) {
      const size_t slash = word.find('/');
      if (slash != std::string::npos) word = word.substr(0, slash);
      in_addr a{};
      if (starts_with(word, "192.168.122.") && inet_pton(AF_INET, word.c_str(), &a) == 1) { found = word; break; }
    }
    std::lock_guard<std::mutex> lk(mu_);
    if (found.empty()) ips_.erase(vm);
    else ips_[vm] = {found, now_s()};
    return found;
  }

  // "ready" when the guest answers, "down" otherwise (cached 20 s).
  std::string state(const std::string &vm) {
    if (!valid_name(vm)) return "none";
    {
      std::lock_guard<std::mutex> lk(mu_);
      const auto it = states_.find(vm);
      if (it != states_.end() && now_s() - it->second.second < 20) return it->second.first;
    }
    const std::string st = ip(vm, true).empty() ? "down" : "ready";
    std::lock_guard<std::mutex> lk(mu_);
    states_[vm] = {st, now_s()};
    return st;
  }

  // Is this VM registered on the host? 0 = yes, 3 = no such VM, else unknown.
  int verify(const std::string &vm) {
    if (!valid_name(vm)) return 3;
    RunOptions opt;
    opt.timeout_s = 25;
    const RunResult r = mag::run(wrapper({cfg_.agent_ctl, "verify", vm}), opt);
    return r.spawned ? r.code : -1;
  }
  // Creates a fresh VM from the golden image (minutes). Never touches an
  // existing one: the helper refuses a name that is already registered.
  RunResult spawn(const std::string &vm) {
    RunOptions opt;
    opt.timeout_s = 1200;
    return mag::run(wrapper({cfg_.agent_ctl, "spawn", vm}), opt);
  }
  // The owner's reset: the VM is thrown away with everything in it and made
  // anew from the golden image under the same name (minutes). It does not
  // matter what state the old one is in, or whether there is one at all.
  RunResult reset(const std::string &vm) {
    forget(vm);
    RunOptions opt;
    opt.timeout_s = 2700; // another bot's reset may be ahead of it
    const RunResult r = mag::run(wrapper({cfg_.agent_ctl, "reset", vm}), opt);
    forget(vm);
    return r;
  }
  // Saves of the VM (newest first, see magbot save): [{id, kind, created_at, bytes}].
  Val saves(const std::string &vm) {
    if (!valid_name(vm)) return Val::array();
    RunOptions opt;
    opt.timeout_s = 20;
    const RunResult r = mag::run(wrapper({cfg_.agent_ctl, "saves", vm}), opt);
    Val v = jparse(r.out);
    return r.code == 0 && v.isArr() ? v : Val::array();
  }
  // A save of the VM while it runs on (minutes; kind: manual, chat, daily).
  RunResult save(const std::string &vm, const std::string &kind) {
    RunOptions opt;
    opt.timeout_s = 3 * 3600;
    return mag::run(wrapper({cfg_.agent_ctl, "save", vm, kind}), opt);
  }
  // The VM as it was in save <id>; its disk of now is kept as a save first.
  RunResult restore(const std::string &vm, const std::string &id, const std::string &token) {
    forget(vm);
    RunOptions opt;
    opt.timeout_s = 3 * 3600;
    const RunResult r = mag::run(wrapper({cfg_.agent_ctl, "restore", vm, id, token}), opt);
    forget(vm);
    return r;
  }
  // Nothing the room knew about this VM holds any more (its address, its
  // browser controller, its proxy settings).
  void forget(const std::string &vm) {
    std::lock_guard<std::mutex> lk(mu_);
    ips_.erase(vm);
    states_.erase(vm);
    starts_.erase(vm);
    proxied_.erase(vm);
  }
  // Host firewall switch for all bot VMs: no internet at all when offline.
  bool netmode(bool online) {
    RunOptions opt;
    opt.timeout_s = 20;
    const std::string helper = env("MAG_ROOM_NETMODE", "/usr/local/sbin/magbot-netmode");
    return mag::run(wrapper({helper, online ? "online" : "offline"}), opt).code == 0;
  }

  // ---- browser controller (mag-browser inside the VM, port 9225) ----------
  http::ClientResult browser(const std::string &vm, const std::string &method, const std::string &path,
                             const std::string &body = "", int timeout_s = 60, const Cancel *cancel = nullptr) {
    http::ClientResult res;
    std::string error;
    const std::string addr = ensure_browser(vm, error);
    if (addr.empty()) { res.error = error; return res; }
    return browser_raw(addr, vm, method, path, body, timeout_s, cancel);
  }

  // Makes sure the VM's controller runs the current protocol and returns the
  // guest address. One start at a time per VM; a failure is remembered for
  // 30 s so a broken VM cannot pile up waiting requests.
  std::string ensure_browser(const std::string &vm, std::string &error) {
    if (!valid_name(vm)) { error = "Dieser Bot hat keine eigene VM."; return ""; }
    std::shared_ptr<Start> start;
    {
      std::lock_guard<std::mutex> lk(mu_);
      auto &slot = starts_[vm];
      if (!slot) slot = std::make_shared<Start>();
      start = slot;
    }
    std::unique_lock<std::timed_mutex> lk(start->mu, std::chrono::seconds(120));
    if (!lk.owns_lock()) { error = "Browser wird gerade gestartet, bitte gleich erneut versuchen."; return ""; }
    if (now_s() - start->failed_at < 30) { error = start->failure; return ""; }
    std::string addr = ip(vm);
    if (addr.empty()) addr = ip(vm, true);
    if (addr.empty()) return fail(*start, error, "VM " + vm + " ist nicht erreichbar.");
    for (int probe = 0; probe < 3; ++probe) {
      const auto st = browser_raw(addr, vm, "GET", "/state", "", probe ? 3 : 2, nullptr);
      if (current(st)) return addr;
      if (st.status == 401 || st.status == 403) break; // other token: relaunch
      if (st.status == 200 && jparse(st.body).find("udp_transport")) break; // older protocol
      if (probe < 2) usleep(200000);
    }
    audit_proxy(vm);
    const std::string keydir = cfg_.state_dir + "/browser-udp-keys", keyfile = keydir + "/" + vm + ".key";
    fs::create_directories(keydir);
    chmod(keydir.c_str(), 0700);
    std::string key = trim(slurp(keyfile));
    if (key.size() != 64) {
      key = random_hex(32);
      if (!write_atomic(keyfile, key, 0600)) return fail(*start, error, "UDP-Schlüssel konnte nicht gespeichert werden.");
    }
    const auto stale = quiet(vm, browser_controller_stop_script(), 20);
    if (stale.code) return fail(*start, error, "Alter Browser-Controller lässt sich nicht beenden: " + clip(stale.out, 300));
    addr = ip(vm, true);
    if (addr.empty()) return fail(*start, error, "VM " + vm + " ist nicht erreichbar.");
    const std::string tok = token(vm);
    const std::string launch =
        "install -d -m 0700 /home/bot/browserd && test -w /home/bot/browserd && "
        "curl -fsS --connect-timeout 5 --max-time 25 " + shell_quote(bridge("/tool/mag-browser?token=" + tok)) +
        " -o /home/bot/browserd/.mag-browser-new && test -s /home/bot/browserd/.mag-browser-new && "
        "chmod 700 /home/bot/browserd/.mag-browser-new && "
        "mv -f /home/bot/browserd/.mag-browser-new /home/bot/browserd/mag-browser && "
        "printf %s " + shell_quote(tok) + " > /home/bot/browserd/token && chmod 600 /home/bot/browserd/token && "
        "printf %s " + shell_quote(key) + " > /home/bot/browserd/udp-key && chmod 600 /home/bot/browserd/udp-key && "
        "/home/bot/browserd/mag-browser --daemon";
    const auto started = quiet(vm, launch, 40);
    if (started.code) return fail(*start, error, "Browser-Start fehlgeschlagen: " + clip(started.out, 300));
    for (int i = 0; i < 60; ++i) {
      if (current(browser_raw(addr, vm, "GET", "/state", "", 2, nullptr))) return addr;
      usleep(250000);
    }
    const auto diag = quiet(vm, "tail -n 8 /home/bot/browserd/browser.log 2>/dev/null || true", 10);
    return fail(*start, error, "Browser antwortet nach dem Start nicht: " + clip(diag.out, 400));
  }

  const Config &config() const { return cfg_; }

private:
  static constexpr size_t kMaxOut = 256 * 1024;
  struct Start {
    std::timed_mutex mu;
    int64_t failed_at = 0;
    std::string failure;
  };
  const Config &cfg_;
  std::string master_;
  std::mutex mu_;
  std::map<std::string, std::pair<std::string, int64_t>> ips_, states_;
  std::map<std::string, std::shared_ptr<Start>> starts_;
  std::set<std::string> proxied_;

  std::vector<std::string> wrapper(std::vector<std::string> argv) const {
    if (cfg_.sudo) argv.insert(argv.begin(), {"sudo", "-n"});
    return argv;
  }
  static bool current(const http::ClientResult &r) {
    return r.status == 200 && jstr(jparse(r.body), "udp_transport") == "MGB5";
  }
  std::string fail(Start &start, std::string &error, const std::string &why) {
    start.failed_at = now_s();
    start.failure = error = why;
    return "";
  }
  http::ClientResult browser_raw(const std::string &addr, const std::string &vm, const std::string &method,
                                 const std::string &path, const std::string &body, int timeout_s,
                                 const Cancel *cancel) {
    http::ClientOptions opt;
    opt.method = method;
    opt.body = body;
    opt.timeout_s = timeout_s;
    opt.connect_timeout_s = 3;
    opt.cancel = cancel;
    opt.headers.push_back({"Authorization", "Bearer " + token(vm)});
    if (method == "POST") opt.headers.push_back({"Content-Type", "application/json"});
    return http::fetch("http://" + addr + ":9225" + path, opt);
  }
  // Guest trust store and proxy settings of the audit web proxy; once per
  // room process and VM, never fatal.
  void audit_proxy(const std::string &vm) {
    {
      std::lock_guard<std::mutex> lk(mu_);
      if (!proxied_.insert(vm).second) return;
    }
    RunOptions opt;
    opt.timeout_s = 600;
    mag::run(wrapper({cfg_.agent_ctl, "audit-proxy", vm}), opt);
  }
};

} // namespace mag::vm
