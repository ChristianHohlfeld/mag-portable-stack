// mag-numa-swap: explicit, confirmed model hot-swap per NUMA node.
// One model per node, never more than two loaded. Never runs by itself:
// the room calls `swap` only after the owner confirmed it in the UI.
//
//   mag-numa-swap render <node> <model>   systemd unit for that pair
//   mag-numa-swap admit  <node> <model>   admission check only (exit 3 = refused)
//   mag-numa-swap swap   <node> <model>   stop old -> start new -> health -> commit,
//                                          rollback to the previous model on failure
//   mag-numa-swap status                  state JSON
//
// Exit codes: 0 ok/already serving, 2 usage/config, 3 refused (admission),
// 5 another swap runs, 6 failed and rolled back, 7 failed and rollback failed.
// Environment (defaults for production; tests override):
//   MAG_NUMA_CATALOG   /opt/mag-stack/current/ops/numa/catalog.json
//   MAG_NUMA_STATE     /var/lib/mag-numa/state.json
//   MAG_NUMA_UNIT_DIR  /etc/systemd/system
//   MAG_NUMA_SYSTEMCTL systemctl
//   MAG_NUMA_CGROUP    /sys/fs/cgroup/system.slice
//   MAG_NUMA_HEALTH_TIMEOUT_S 10800, MAG_NUMA_POLL_MS 2000
#include "json.hpp"
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <thread>
#include <algorithm>
using mj::Val;
static const Val &or_empty(const Val *v) { static const Val e; return v ? *v : e; }

static std::string env(const char *k, const std::string &d) {
  const char *v = std::getenv(k);
  return v && *v ? v : d;
}
static std::string slurp(const std::string &p) {
  std::ifstream f(p, std::ios::binary);
  std::stringstream ss;
  ss << f.rdbuf();
  return ss.str();
}
static bool write_atomic(const std::string &p, const std::string &body) {
  const std::string tmp = p + ".tmp";
  {
    std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
    if (!f) return false;
    f << body;
    f.flush();
    if (!f) return false;
  }
  return rename(tmp.c_str(), p.c_str()) == 0;
}
static long now_s() { return (long)time(nullptr); }

struct Cfg { Val root; std::string state_path, unit_dir, systemctl, cgroup; };
static Cfg CFG;

static const Val *node_cfg(int n) {
  for (auto &x : CFG.root["nodes"].asArr())
    if ((int)x.find("node")->asNum(-1) == n) return &x;
  return nullptr;
}
static const Val *model_cfg(const std::string &id) {
  for (auto &x : CFG.root["models"].asArr())
    if (x.find("id") && x.find("id")->asStr() == id) return &x;
  return nullptr;
}
static std::string unit_name(int n) { return "mag-numa" + std::to_string(n) + ".service"; }

static Val &node_state(Val &s, int n);
// All nodes exist after loading: later lookups never insert, so references
// into the state object stay valid.
static Val load_state() {
  Val s;
  if (!mj::parse(slurp(CFG.state_path), s) || !s.isObj()) s = Val::object();
  if (!s.find("nodes") || !s["nodes"].isObj()) s["nodes"] = Val::object();
  for (auto &x : CFG.root["nodes"].asArr()) node_state(s, (int)x.find("node")->asNum());
  return s;
}
static void save_state(Val &s) {
  s["updated"] = now_s();
  write_atomic(CFG.state_path, mj::dump(s));
}
static Val &node_state(Val &s, int n) {
  Val &ns = s["nodes"][std::to_string(n)];
  if (!ns.isObj()) {
    ns = Val::object();
    const Val *nc = node_cfg(n);
    ns["model"] = nc && nc->find("default") ? nc->find("default")->asStr() : "";
    ns["state"] = "serving";
    ns["progress"] = 100;
  }
  return ns;
}

static int run(const std::vector<std::string> &argv, std::string *out = nullptr) {
  int pipefd[2];
  if (out && pipe(pipefd) != 0) return -1;
  pid_t pid = fork();
  if (pid == 0) {
    if (out) { dup2(pipefd[1], 1); close(pipefd[0]); close(pipefd[1]); }
    std::vector<char *> a;
    for (auto &s : argv) a.push_back(const_cast<char *>(s.c_str()));
    a.push_back(nullptr);
    execvp(a[0], a.data());
    _exit(127);
  }
  if (out) {
    close(pipefd[1]);
    char buf[512];
    ssize_t r;
    while ((r = read(pipefd[0], buf, sizeof buf)) > 0) out->append(buf, (size_t)r);
    close(pipefd[0]);
  }
  int st = 0;
  waitpid(pid, &st, 0);
  return WIFEXITED(st) ? WEXITSTATUS(st) : -1;
}
static int sysctl(const std::vector<std::string> &args, std::string *out = nullptr) {
  std::vector<std::string> a{CFG.systemctl};
  a.insert(a.end(), args.begin(), args.end());
  return run(a, out);
}
static bool unit_active(const std::string &u) { return sysctl({"is-active", "--quiet", u}) == 0; }
static bool unit_failed(const std::string &u) { return sysctl({"is-failed", "--quiet", u}) == 0; }

// Every unit that can hold a model, across both nodes.
static std::vector<std::string> model_units(int n) {
  std::vector<std::string> out{unit_name(n)};
  if (const Val *nc = node_cfg(n))
    for (auto &u : or_empty(nc->find("legacy_units")).asArr())
      out.push_back(u.asStr());
  return out;
}
static int loaded_count() {
  int c = 0;
  for (auto &nc : CFG.root["nodes"].asArr())
    for (auto &u : model_units((int)nc.find("node")->asNum()))
      if (unit_active(u)) ++c;
  return c;
}

static int http_status(int port, const std::string &path) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) return -1;
  timeval tv{3, 0};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons((uint16_t)port);
  a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (connect(fd, (sockaddr *)&a, sizeof a) != 0) { close(fd); return -1; }
  std::string req = "GET " + path + " HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
  if (write(fd, req.data(), req.size()) < 0) { close(fd); return -1; }
  char buf[64] = {0};
  ssize_t r = read(fd, buf, sizeof buf - 1);
  close(fd);
  if (r < 12) return -1;
  return atoi(buf + 9);
}

static std::string gib(double g) { return std::to_string((long)g) + "G"; }

static std::string render(int n, const Val &m) {
  const Val &nc = *node_cfg(n);
  const Val &host = CFG.root["host"];
  const std::string cpus = nc.find("cpus")->asStr();
  const double ram = m.find("ram_gib")->asNum();
  const int threads = (int)(nc.find("threads") ? nc.find("threads")->asNum(14) : 14);
  const std::string port = std::to_string((int)nc.find("port")->asNum());
  const std::string slots = (host.find("slots_dir") ? host.find("slots_dir")->asStr() : "/var/lib/mag-llm/slots") +
                            "/numa" + std::to_string(n);
  std::string exec = "/usr/bin/numactl --physcpubind=" + cpus + " --membind=" + std::to_string(n) + " " +
                     m.find("bin")->asStr() + " --model " + m.find("model")->asStr() + " --alias " +
                     m.find("id")->asStr() + " --host 127.0.0.1 --port " + port + " --threads " +
                     std::to_string(threads) + " --threads-batch " + std::to_string(threads) +
                     " --numa numactl --slot-save-path " + slots;
  if (m.find("mlock_args")) {
    for (auto &a : m.find("mlock_args")->asArr()) exec += " " + a.asStr();
  } else {
    exec += " --mlock";
  }
  for (auto &a : or_empty(m.find("args")).asArr()) exec += " " + a.asStr();
  std::ostringstream u;
  u << "# Generated by mag-numa-swap. Do not edit; swap via the MAG room.\n"
    << "[Unit]\nDescription=MAG model " << m.find("label")->asStr() << " on NUMA node " << n << "\n"
    << "After=network-online.target\nWants=network-online.target\n"
    << "ConditionPathExists=" << m.find("model")->asStr() << "\n"
    << "StartLimitIntervalSec=1800\nStartLimitBurst=2\n\n[Service]\nType=simple\n"
    << "User=" << (host.find("user") ? host.find("user")->asStr() : "chris") << "\n"
    << "Environment=MAG_NUMA_NODE=" << n << " MAG_NUMA_MODEL=" << m.find("id")->asStr() << "\n"
    << "ExecStartPre=+/usr/bin/install -d -m 0750 -o " << (host.find("user") ? host.find("user")->asStr() : "chris")
    << " " << slots << "\n"
    << "ExecStart=" << exec << "\n"
    << "Restart=on-failure\nRestartSec=60\nTimeoutStartSec=10800\nTimeoutStopSec=120\n"
    // Strict NUMA binding: cpuset (cannot be widened by the process) + membind.
    << "CPUAffinity=" << cpus << "\nAllowedCPUs=" << cpus << "\n"
    << "AllowedMemoryNodes=" << n << "\nNUMAPolicy=bind\nNUMAMask=" << n << "\n"
    // Memory sized to the model: protected from reclaim, capped, never swapped.
    << "MemoryAccounting=yes\nMemoryMin=" << gib(ram) << "\nMemoryLow=" << gib(ram) << "\n"
    << "MemoryHigh=" << gib(ram + 8) << "\nMemoryMax=" << gib(ram + 16) << "\nMemorySwapMax=0\n"
    << "LimitMEMLOCK=infinity\nOOMScoreAdjust=-900\nOOMPolicy=continue\n"
    << "Nice=5\nNoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=read-only\n"
    << "ReadWritePaths=-" << slots << "\nPrivateTmp=yes\nRestrictAddressFamilies=AF_INET AF_UNIX\n\n"
    << "[Install]\nWantedBy=multi-user.target\n";
  return u.str();
}

// "" = admitted, otherwise the refusal shown to the user.
static std::string admit(int n, const Val &m, Val &state) {
  const Val *nc = node_cfg(n);
  const double ram = m.find("ram_gib")->asNum();
  const double budget = nc->find("budget_gib")->asNum();
  const double reserve = nc->find("reserve_gib") ? nc->find("reserve_gib")->asNum() : 0;
  if (ram + reserve > budget)
    return m.find("label")->asStr() + " needs " + gib(ram) + " + " + gib(reserve) +
           " reserve; NUMA " + std::to_string(n) + " budget is " + gib(budget) + ".";
  double other = 0;
  for (auto &x : CFG.root["nodes"].asArr()) {
    const int o = (int)x.find("node")->asNum();
    if (o == n) continue;
    const Val &os = node_state(state, o);
    if (os.find("state") && os.find("state")->asStr() == "idle") continue;
    if (const Val *om = model_cfg(os.find("model")->asStr())) other += om->find("ram_gib")->asNum();
  }
  const double limit = CFG.root["host"].find("limit_gib")->asNum();
  if (ram + other > limit)
    return "Host limit: " + gib(ram) + " + " + gib(other) + " loaded on the other node exceeds " +
           gib(limit) + ".";
  return "";
}

static void set_node(Val &s, int n, const std::string &state, int progress, const std::string &err = "") {
  Val &ns = node_state(s, n);
  ns["state"] = state;
  ns["progress"] = progress;
  ns["error"] = err;
  ns["since"] = now_s();
  save_state(s);
}

static long cgroup_bytes(int n) {
  std::string v = slurp(CFG.cgroup + "/" + unit_name(n) + "/memory.current");
  return v.empty() ? 0 : atol(v.c_str());
}

// Starts unit for node n and waits for /health 200. "" on success.
static std::string start_and_wait(int n, const Val &m, Val &s, const std::string &label) {
  const int port = (int)node_cfg(n)->find("port")->asNum();
  if (sysctl({"start", unit_name(n)}) != 0) return "systemctl start failed";
  const long timeout = atol(env("MAG_NUMA_HEALTH_TIMEOUT_S", "10800").c_str());
  const long poll = atol(env("MAG_NUMA_POLL_MS", "2000").c_str());
  const double want = m.find("ram_gib")->asNum() * 1024.0 * 1024 * 1024;
  const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(timeout);
  while (std::chrono::steady_clock::now() < end) {
    int code = http_status(port, "/health");
    if (code == 200) return "";
    if (unit_failed(unit_name(n))) return "unit failed while loading";
    int pct = want > 0 ? (int)(100.0 * (double)cgroup_bytes(n) / want) : 0;
    set_node(s, n, label, std::clamp(pct, 1, 99));
    std::this_thread::sleep_for(std::chrono::milliseconds(poll));
  }
  return "health check timed out";
}

static int cmd_swap(int n, const std::string &id) {
  const Val *m = model_cfg(id);
  if (!m || !node_cfg(n)) { std::cerr << "unknown node or model\n"; return 2; }
  std::string lock = CFG.state_path + ".lock";
  int lfd = open(lock.c_str(), O_CREAT | O_RDWR, 0644);
  if (lfd < 0 || flock(lfd, LOCK_EX | LOCK_NB) != 0) { std::cerr << "another swap is running\n"; return 5; }
  Val s = load_state();
  Val &ns = node_state(s, n);
  const std::string prev = ns.find("model")->asStr();
  const int port = (int)node_cfg(n)->find("port")->asNum();
  if (prev == id && ns.find("state")->asStr() == "serving" && http_status(port, "/health") == 200) {
    std::cout << "already serving " << id << " on NUMA " << n << "\n";
    return 0;
  }
  if (std::string why = admit(n, *m, s); !why.empty()) { std::cerr << why << "\n"; return 3; }
  ns["previous"] = prev;
  ns["target"] = id;
  set_node(s, n, "swapping", 0);
  const std::string upath = CFG.unit_dir + "/" + unit_name(n);
  const std::string prev_unit = slurp(upath);
  // Unload first: stop every unit that can hold a model on this node.
  for (auto &u : model_units(n)) {
    if (unit_active(u)) sysctl({"stop", u});
    if (u != unit_name(n)) sysctl({"disable", u}); // reboot must not bring back a third model
  }
  if (loaded_count() > 1) {
    set_node(s, n, "error", 0, "two models still loaded after stop; aborted");
    std::cerr << "refusing: more than one model still loaded\n";
    return 7;
  }
  write_atomic(upath, render(n, *m));
  sysctl({"daemon-reload"});
  std::string err = start_and_wait(n, *m, s, "loading");
  if (err.empty()) {
    sysctl({"enable", unit_name(n)});
    Val &done = node_state(s, n);
    done["model"] = id;
    done["target"] = "";
    set_node(s, n, "serving", 100);
    std::cout << "serving " << id << " on NUMA " << n << "\n";
    return 0;
  }
  // Rollback to the previous model on this node.
  sysctl({"stop", unit_name(n)});
  const Val *pm = model_cfg(prev);
  set_node(s, n, "rollback", 0, err);
  if (!pm) { set_node(s, n, "error", 0, err + "; no previous model"); return 7; }
  if (!prev_unit.empty()) write_atomic(upath, prev_unit);
  else write_atomic(upath, render(n, *pm));
  sysctl({"daemon-reload"});
  std::string err2 = start_and_wait(n, *pm, s, "rollback");
  Val &rb = node_state(s, n);
  rb["model"] = prev;
  rb["target"] = "";
  if (err2.empty()) {
    sysctl({"enable", unit_name(n)});
    set_node(s, n, "serving", 100, "swap to " + id + " failed: " + err + " (rolled back)");
    std::cerr << "failed: " << err << "; rolled back to " << prev << "\n";
    return 6;
  }
  set_node(s, n, "error", 0, "swap failed: " + err + "; rollback failed: " + err2);
  std::cerr << "failed: " << err << "; rollback failed: " << err2 << "\n";
  return 7;
}

int main(int argc, char **argv) {
  if (argc < 2) { std::cerr << "usage: mag-numa-swap render|admit|swap <node> <model> | status\n"; return 2; }
  CFG.state_path = env("MAG_NUMA_STATE", "/var/lib/mag-numa/state.json");
  CFG.unit_dir = env("MAG_NUMA_UNIT_DIR", "/etc/systemd/system");
  CFG.systemctl = env("MAG_NUMA_SYSTEMCTL", "systemctl");
  CFG.cgroup = env("MAG_NUMA_CGROUP", "/sys/fs/cgroup/system.slice");
  const std::string cat = env("MAG_NUMA_CATALOG", "/opt/mag-stack/current/ops/numa/catalog.json");
  if (!mj::parse(slurp(cat), CFG.root) || !CFG.root.isObj() || !CFG.root["nodes"].isArr() ||
      !CFG.root["models"].isArr() || CFG.root["nodes"].asArr().size() > 2) {
    std::cerr << "invalid catalog " << cat << " (max two nodes)\n";
    return 2;
  }
  const std::string cmd = argv[1];
  if (cmd == "status") { Val s = load_state(); for (auto &x : CFG.root["nodes"].asArr()) node_state(s, (int)x.find("node")->asNum()); std::cout << mj::dump(s) << "\n"; return 0; }
  if (argc < 4) { std::cerr << "missing <node> <model>\n"; return 2; }
  const int n = atoi(argv[2]);
  const std::string id = argv[3];
  const Val *m = model_cfg(id);
  if (!m || !node_cfg(n)) { std::cerr << "unknown node or model\n"; return 2; }
  if (cmd == "render") { std::cout << render(n, *m); return 0; }
  if (cmd == "admit") {
    Val s = load_state();
    std::string why = admit(n, *m, s);
    if (!why.empty()) { std::cerr << why << "\n"; return 3; }
    std::cout << "admitted\n";
    return 0;
  }
  if (cmd == "swap") return cmd_swap(n, id);
  std::cerr << "unknown command\n";
  return 2;
}
