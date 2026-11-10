// mag-modeld: the single owner of the model lifecycle.
//
// For every bot in models.json (lena: NUMA0 :8191, noah: NUMA1 :8192):
//   GGUF missing/partial  -> "downloading"; resumes with mag-model-fetch@<bot>
//                            when no other process is writing the file
//   size == Hugging Face  -> one SHA256 pass, then a <bot>.<kind>.ok stamp
//   verified              -> start mag-model@<bot>.service and watch /health
// The result is /run/mag-models/status.json, which the room shows as
// "lädt herunter", "wird geprüft", "lädt", "bereit". Control verbs arrive as
// files in <state>/control/ (start, stop, restart, freeze, thaw).
// A model file is never deleted, truncated or rewritten here.
#include "json.hpp"

#include <arpa/inet.h>
#include <curl/curl.h>
#include <dirent.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <openssl/evp.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

using mj::Val;
using Clock = std::chrono::steady_clock;

static std::atomic<bool> g_stop{false};
// Overridable for the test fixture (fake systemctl); production uses PATH.
static std::string g_systemctl = "systemctl";
// A download counts as abandoned after this long without growth.
static int g_quiet_s = 75;
static std::string g_state_dir = "/var/lib/mag-models";
static std::string g_run_dir = "/run/mag-models";

static void logf(const std::string &line) { std::cerr << line << std::endl; }

static std::string read_file(const std::string &path, size_t cap = 1 << 20) {
  std::ifstream f(path, std::ios::binary);
  if (!f) return "";
  std::string s((std::istreambuf_iterator<char>(f)), {});
  if (s.size() > cap) s.resize(cap);
  return s;
}

static bool write_atomic(const std::string &path, const std::string &body, mode_t mode = 0644) {
  const std::string tmp = path + ".tmp";
  int fd = open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
  if (fd < 0) return false;
  size_t off = 0;
  while (off < body.size()) {
    ssize_t n = write(fd, body.data() + off, body.size() - off);
    if (n <= 0) { close(fd); unlink(tmp.c_str()); return false; }
    off += (size_t)n;
  }
  fchmod(fd, mode);
  close(fd);
  return rename(tmp.c_str(), path.c_str()) == 0;
}

static std::string trim(std::string s) {
  auto a = s.find_first_not_of(" \t\r\n\""), b = s.find_last_not_of(" \t\r\n\"");
  return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}

static std::string lower(std::string s) {
  for (auto &c : s) c = (char)tolower((unsigned char)c);
  return s;
}

// fork/exec without a shell; stdout+stderr captured (bounded).
static int run(const std::vector<std::string> &argv, std::string *out = nullptr, int timeout_s = 30) {
  int p[2];
  if (pipe2(p, O_CLOEXEC)) return -1;
  pid_t pid = fork();
  if (pid < 0) { close(p[0]); close(p[1]); return -1; }
  if (pid == 0) {
    dup2(p[1], 1); dup2(p[1], 2);
    int devnull = open("/dev/null", O_RDONLY);
    if (devnull >= 0) dup2(devnull, 0);
    std::vector<char *> args;
    for (auto &a : argv) args.push_back(const_cast<char *>(a.c_str()));
    args.push_back(nullptr);
    execvp(args[0], args.data());
    _exit(127);
  }
  close(p[1]);
  std::string buf;
  const auto deadline = Clock::now() + std::chrono::seconds(timeout_s);
  char chunk[4096];
  for (;;) {
    fd_set set; FD_ZERO(&set); FD_SET(p[0], &set);
    timeval tv{1, 0};
    int r = select(p[0] + 1, &set, nullptr, nullptr, &tv);
    if (r > 0) {
      ssize_t n = read(p[0], chunk, sizeof chunk);
      if (n <= 0) break;
      if (buf.size() < 65536) buf.append(chunk, (size_t)n);
    } else if (Clock::now() > deadline) {
      kill(pid, SIGKILL);
      break;
    }
  }
  close(p[0]);
  int status = 0;
  waitpid(pid, &status, 0);
  if (out) *out = buf;
  return WIFEXITED(status) ? WEXITSTATUS(status) : 128;
}

// ---------------------------------------------------------------- artifacts
struct Remote {
  bool ok = false;
  uint64_t size = 0;
  std::string sha256;
};

static size_t header_cb(char *data, size_t size, size_t n, void *user) {
  static_cast<std::string *>(user)->append(data, size * n);
  return size * n;
}

// Hugging Face answers a HEAD on /resolve/ with X-Linked-Size (the LFS/Xet
// object size, the Content-Length of the final download) and X-Linked-Etag
// (its SHA256). Plain files answer 200 with Content-Length directly.
static Remote remote_head(const std::string &url) {
  Remote r;
  if (url.empty()) return r;
  CURL *h = curl_easy_init();
  if (!h) return r;
  std::string headers;
  curl_easy_setopt(h, CURLOPT_URL, url.c_str());
  curl_easy_setopt(h, CURLOPT_NOBODY, 1L);
  curl_easy_setopt(h, CURLOPT_FOLLOWLOCATION, 0L);
  curl_easy_setopt(h, CURLOPT_TIMEOUT, 25L);
  curl_easy_setopt(h, CURLOPT_CONNECTTIMEOUT, 10L);
  curl_easy_setopt(h, CURLOPT_PROTOCOLS_STR, "https");
  curl_easy_setopt(h, CURLOPT_USERAGENT, "mag-modeld/1");
  curl_easy_setopt(h, CURLOPT_HEADERFUNCTION, header_cb);
  curl_easy_setopt(h, CURLOPT_HEADERDATA, &headers);
  long code = 0;
  if (curl_easy_perform(h) == CURLE_OK) curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
  std::map<std::string, std::string> hd;
  std::istringstream in(headers);
  std::string line;
  while (std::getline(in, line)) {
    auto c = line.find(':');
    if (c == std::string::npos) continue;
    hd[lower(line.substr(0, c))] = trim(line.substr(c + 1));
  }
  auto number = [&](const char *k) -> uint64_t {
    auto it = hd.find(k);
    if (it == hd.end() || it->second.empty() ||
        it->second.find_first_not_of("0123456789") != std::string::npos) return 0;
    return std::stoull(it->second);
  };
  auto hex64 = [](const std::string &s) {
    return s.size() == 64 && s.find_first_not_of("0123456789abcdef") == std::string::npos;
  };
  if (code >= 200 && code < 400) {
    r.size = number("x-linked-size");
    if (hex64(hd["x-linked-etag"])) r.sha256 = hd["x-linked-etag"];
    if (!r.size && code == 200) r.size = number("content-length");
    if (!r.size && code >= 300) {
      // No linked size: follow the redirect and take the final Content-Length.
      curl_easy_setopt(h, CURLOPT_FOLLOWLOCATION, 1L);
      curl_easy_setopt(h, CURLOPT_MAXREDIRS, 5L);
      headers.clear();
      curl_off_t len = -1;
      if (curl_easy_perform(h) == CURLE_OK &&
          curl_easy_getinfo(h, CURLINFO_CONTENT_LENGTH_DOWNLOAD_T, &len) == CURLE_OK && len > 0)
        r.size = (uint64_t)len;
    }
    r.ok = r.size > 0;
  }
  curl_easy_cleanup(h);
  return r;
}

// Is any process holding this file open? A running wget/curl is the
// downloader; a second writer on the same file would corrupt it.
static bool has_open_handle(const std::string &path) {
  struct stat want{};
  if (stat(path.c_str(), &want)) return false;
  DIR *proc = opendir("/proc");
  if (!proc) return false;
  bool found = false;
  const pid_t self = getpid();
  while (auto *e = readdir(proc)) {
    if (!isdigit((unsigned char)e->d_name[0])) continue;
    if (atoi(e->d_name) == self) continue;
    const std::string fds = std::string("/proc/") + e->d_name + "/fd";
    DIR *d = opendir(fds.c_str());
    if (!d) continue;
    while (auto *f = readdir(d)) {
      if (f->d_name[0] == '.') continue;
      struct stat st{};
      if (stat((fds + "/" + f->d_name).c_str(), &st)) continue;
      if (st.st_dev == want.st_dev && st.st_ino == want.st_ino) { found = true; break; }
    }
    closedir(d);
    if (found) break;
  }
  closedir(proc);
  return found;
}

struct Artifact {
  std::string kind, path, url, pinned_sha;
  uint64_t pinned_size = 0;
  bool configured = false;

  Remote remote;
  Clock::time_point remote_checked{};
  bool remote_tried = false;

  Clock::time_point last_fetch{};
  bool fetched_once = false;

  bool exists = false;
  uint64_t size = 0;
  time_t mtime = 0;
  std::deque<std::pair<Clock::time_point, uint64_t>> samples;
  Clock::time_point last_growth = Clock::now();
  bool writer = false;

  enum Verify { UNKNOWN, RUNNING, OK, BAD } verify = UNKNOWN;
  std::shared_ptr<std::atomic<uint64_t>> hashed = std::make_shared<std::atomic<uint64_t>>(0);
  std::shared_ptr<std::atomic<int>> verify_result = std::make_shared<std::atomic<int>>(0); // 0 running, 1 ok, 2 bad
  std::string verify_detail;
  uint64_t verify_size = 0;
  time_t verify_mtime = 0;

  uint64_t expected() const { return remote.ok ? remote.size : pinned_size; }
  std::string expected_sha() const { return !remote.sha256.empty() ? remote.sha256 : pinned_sha; }
  bool complete() const { return exists && expected() > 0 && size == expected(); }
  double rate() const {
    if (samples.size() < 2) return 0;
    const double dt = std::chrono::duration<double>(samples.back().first - samples.front().first).count();
    return dt > 1 ? (double)(samples.back().second - samples.front().second) / dt : 0;
  }
};

static std::string stamp_path(const std::string &bot, const Artifact &a, const char *suffix) {
  return g_state_dir + "/" + bot + "." + a.kind + "." + suffix;
}

static void observe(Artifact &a) {
  struct stat st{};
  const uint64_t before = a.size;
  a.exists = stat(a.path.c_str(), &st) == 0 && S_ISREG(st.st_mode);
  a.size = a.exists ? (uint64_t)st.st_size : 0;
  a.mtime = a.exists ? st.st_mtime : 0;
  const auto now = Clock::now();
  if (a.size != before) a.last_growth = now;
  a.samples.emplace_back(now, a.size);
  while (a.samples.size() > 2 && now - a.samples.front().first > std::chrono::seconds(90))
    a.samples.pop_front();
}

static void verify_async(const std::string &bot, Artifact &a) {
  a.verify = Artifact::RUNNING;
  a.verify_size = a.size;
  a.verify_mtime = a.mtime;
  a.hashed->store(0);
  a.verify_result->store(0);
  const std::string want = a.expected_sha();
  auto hashed = a.hashed;
  auto result = a.verify_result;
  const std::string path = a.path, ok_stamp = stamp_path(bot, a, "ok"), bad_stamp = stamp_path(bot, a, "bad");
  const uint64_t size = a.size;
  const time_t mtime = a.mtime;
  std::thread([=] {
    std::string got = "-";
    bool ok = true;
    if (!want.empty()) {
      ok = false;
      int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
      EVP_MD_CTX *ctx = EVP_MD_CTX_new();
      if (fd >= 0 && ctx && EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr) == 1) {
        posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL);
        std::vector<unsigned char> buf(8 << 20);
        ssize_t n;
        while (!g_stop && (n = read(fd, buf.data(), buf.size())) > 0) {
          EVP_DigestUpdate(ctx, buf.data(), (size_t)n);
          hashed->fetch_add((uint64_t)n);
        }
        unsigned char md[EVP_MAX_MD_SIZE];
        unsigned len = 0;
        if (!g_stop && EVP_DigestFinal_ex(ctx, md, &len) == 1) {
          static const char *hex = "0123456789abcdef";
          got.clear();
          for (unsigned i = 0; i < len; i++) { got += hex[md[i] >> 4]; got += hex[md[i] & 15]; }
          ok = got == want;
        }
      }
      if (ctx) EVP_MD_CTX_free(ctx);
      if (fd >= 0) close(fd);
      if (g_stop) return;
    }
    const std::string line = std::to_string(size) + " " + std::to_string((long long)mtime) + " " + got + "\n";
    if (ok) {
      unlink(bad_stamp.c_str());
      write_atomic(ok_stamp, line);
    } else {
      write_atomic(bad_stamp, line);
    }
    result->store(ok ? 1 : 2);
  }).detach();
}

// True when a stamp records exactly this file (size and mtime).
static bool stamp_matches(const std::string &path, const Artifact &a) {
  std::istringstream in(read_file(path, 256));
  uint64_t size = 0;
  long long mtime = 0;
  return (in >> size >> mtime) && size == a.size && mtime == (long long)a.mtime;
}

// ---------------------------------------------------------------------- bots
struct Unit {
  std::string active, sub, result, freezer;
  long restarts = 0;
  long long since_us = 0;
};

static Unit unit_state(const std::string &unit) {
  Unit u;
  std::string out;
  run({g_systemctl, "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "Result",
       "-p", "NRestarts", "-p", "FreezerState", "-p", "ActiveEnterTimestampMonotonic"}, &out, 10);
  std::istringstream in(out);
  std::string line;
  while (std::getline(in, line)) {
    auto eq = line.find('=');
    if (eq == std::string::npos) continue;
    const std::string k = line.substr(0, eq), v = line.substr(eq + 1);
    if (k == "ActiveState") u.active = v;
    else if (k == "SubState") u.sub = v;
    else if (k == "Result") u.result = v;
    else if (k == "FreezerState") u.freezer = v;
    else if (k == "NRestarts") u.restarts = atol(v.c_str());
    else if (k == "ActiveEnterTimestampMonotonic") u.since_us = atoll(v.c_str());
  }
  return u;
}

// 200 = ready, 503 = loading, 0 = no listener yet.
static int health(int port) {
  int fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0) return 0;
  timeval tv{2, 0};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons((uint16_t)port);
  inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
  int code = 0;
  if (connect(fd, (sockaddr *)&a, sizeof a) == 0) {
    const char req[] = "GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if (send(fd, req, sizeof req - 1, MSG_NOSIGNAL) > 0) {
      // Read to EOF (bounded) so the server never sees a reset mid-reply.
      std::string reply;
      char buf[1024];
      ssize_t n;
      while (reply.size() < 8192 && (n = recv(fd, buf, sizeof buf, 0)) > 0) reply.append(buf, (size_t)n);
      if (reply.size() > 12) code = atoi(reply.c_str() + 9);
    }
  }
  close(fd);
  return code;
}

struct Bot {
  std::string id, label, unit, spec_hash;
  int port = 0, numa = 0, ctx = 0;
  Artifact model, draft;

  std::string state = "unknown", detail;
  bool ever_ready = false;       // since the current unit start
  long seen_restarts = -1;
  Clock::time_point model_verified_at{};
  bool model_verified_seen = false;
  Clock::time_point last_start{};
  bool started_once = false;
  time_t ready_since = 0;
  bool restart_pending = false, draft_active = false;
};

static bool flag(const std::string &bot, const char *name) {
  return access((g_state_dir + "/" + bot + "." + name).c_str(), F_OK) == 0;
}
static void set_flag(const std::string &bot, const char *name, bool on) {
  const std::string p = g_state_dir + "/" + bot + "." + name;
  if (on) write_atomic(p, "1\n");
  else unlink(p.c_str());
}

static void track(const std::string &bot, Artifact &a) {
  if (!a.configured) return;
  observe(a);
  const auto now = Clock::now();
  // Remote facts are needed only while the file is not known to be good.
  const bool stamped = a.exists && stamp_matches(stamp_path(bot, a, "ok"), a);
  if (stamped) {
    a.verify = Artifact::OK;
    return;
  }
  if (!a.remote_tried || (!a.remote.ok && now - a.remote_checked > std::chrono::minutes(5)) ||
      (a.remote.ok && !a.complete() && now - a.remote_checked > std::chrono::minutes(30))) {
    Remote r = remote_head(a.url);
    a.remote_tried = true;
    a.remote_checked = now;
    if (r.ok) {
      if (a.pinned_size && r.size != a.pinned_size)
        logf(bot + "/" + a.kind + ": Hugging Face size " + std::to_string(r.size) +
             " differs from pinned " + std::to_string(a.pinned_size) + "; using Hugging Face");
      a.remote = r;
    }
  }
  if (a.verify == Artifact::RUNNING) {
    const int res = a.verify_result->load();
    if (res == 1) {
      a.verify = Artifact::OK;
      logf(bot + "/" + a.kind + ": verified " + a.path);
    } else if (res == 2) {
      a.verify = Artifact::BAD;
      a.verify_detail = "SHA256 stimmt nicht mit Hugging Face überein";
      logf(bot + "/" + a.kind + ": SHA256 mismatch, file left untouched: " + a.path);
    }
    return;
  }
  if (a.verify == Artifact::BAD || a.verify == Artifact::OK) {
    // A changed file (new download) gets a new verdict.
    if (a.size == a.verify_size && a.mtime == a.verify_mtime) return;
    a.verify = Artifact::UNKNOWN;
  }
  if (a.exists && stamp_matches(stamp_path(bot, a, "bad"), a)) {
    a.verify = Artifact::BAD;
    a.verify_size = a.size;
    a.verify_mtime = a.mtime;
    a.verify_detail = "SHA256 stimmt nicht mit Hugging Face überein";
    return;
  }
  a.writer = a.exists && !a.complete() ? has_open_handle(a.path) : false;
  if (a.complete() && now - a.last_growth > std::chrono::seconds(4) && !has_open_handle(a.path))
    verify_async(bot, a);
}

static Val artifact_json(const Artifact &a) {
  Val v = Val::object();
  v["bytes"] = (double)a.size;
  v["total"] = (double)a.expected();
  const double rate = a.rate();
  v["rate"] = rate;
  if (rate > 1 && a.expected() > a.size) v["eta"] = (double)(a.expected() - a.size) / rate;
  if (a.verify == Artifact::RUNNING && a.verify_size)
    v["verified"] = (double)a.hashed->load() / (double)a.verify_size;
  v["ok"] = a.verify == Artifact::OK;
  return v;
}

static void systemctl(const std::string &verb, const std::string &unit) {
  std::string out;
  const int rc = run({g_systemctl, verb, "--no-block", unit}, &out, 30);
  logf("systemctl " + verb + " " + unit + (rc ? " -> " + std::to_string(rc) + " " + trim(out) : ""));
}

static void apply_control(Bot &b, const std::string &verb) {
  logf(b.id + ": control " + verb);
  if (verb == "stop") {
    set_flag(b.id, "stopped", true);
    systemctl("thaw", b.unit);
    systemctl("stop", b.unit);
  } else if (verb == "start") {
    set_flag(b.id, "stopped", false);
    systemctl("thaw", b.unit);
    systemctl("reset-failed", b.unit);
    b.started_once = false;
  } else if (verb == "restart") {
    set_flag(b.id, "stopped", false);
    systemctl("thaw", b.unit);
    systemctl("reset-failed", b.unit);
    systemctl("restart", b.unit);
    b.ever_ready = false;
  } else if (verb == "freeze") {
    systemctl("freeze", b.unit);
  } else if (verb == "thaw") {
    systemctl("thaw", b.unit);
  }
}

// Control files are written by the unprivileged room: regular files only,
// never followed, never blocked on, content from a fixed verb list.
static void read_control(std::map<std::string, Bot> &bots) {
  const std::string dir = g_state_dir + "/control";
  for (auto &kv : bots) {
    const std::string path = dir + "/" + kv.first;
    int fd = open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) continue;
    struct stat st{};
    char buf[32] = {0};
    std::string verb;
    if (fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && read(fd, buf, sizeof buf - 1) > 0) verb = trim(buf);
    close(fd);
    unlink(path.c_str());
    for (const char *ok : {"start", "stop", "restart", "freeze", "thaw"})
      if (verb == ok) apply_control(kv.second, verb);
  }
}

static void step(Bot &b) {
  track(b.id, b.model);
  track(b.id, b.draft);
  const auto now = Clock::now();
  const Unit u = unit_state(b.unit);
  const bool running = u.active == "active" || u.active == "activating" || u.active == "reloading";
  b.detail.clear();
  b.draft_active = false;
  b.restart_pending = false;

  // Resume a download nobody is working on: one file at a time, only when
  // no process has it open (a second writer would corrupt it), and the
  // optional drafter only once the model itself is complete.
  for (Artifact *a : {&b.model, &b.draft}) {
    if (!a->configured || a->url.empty() || a->complete() || a->verify == Artifact::OK) continue;
    if (a == &b.draft && b.model.verify != Artifact::OK) break;
    if (a->exists && a->expected() && a->size > a->expected()) continue; // never touch an oversized file
    if (now - a->last_growth < std::chrono::seconds(g_quiet_s)) break;
    if (a->fetched_once && now - a->last_fetch < std::chrono::minutes(5)) break;
    const std::string unit = "mag-model-fetch@" + b.id + "-" + a->kind + ".service";
    const std::string active = unit_state(unit).active;
    if (active == "active" || active == "activating") break;
    if (a->exists && has_open_handle(a->path)) break;
    systemctl("reset-failed", unit);
    systemctl("start", unit);
    a->fetched_once = true;
    a->last_fetch = now;
    break;
  }

  const Artifact &m = b.model;
  if (m.verify == Artifact::OK && !b.model_verified_seen) {
    b.model_verified_seen = true;
    b.model_verified_at = now;
  }
  if (m.verify != Artifact::OK) b.model_verified_seen = false;

  if (running) {
    const std::string spec = trim(read_file(g_state_dir + "/slots/" + b.id + "/.spec", 256));
    b.draft_active = spec.find("draft=0") == std::string::npos && spec.find("draft=") != std::string::npos;
    b.restart_pending = !spec.empty() && spec.compare(0, b.spec_hash.size(), b.spec_hash) != 0;
    if (b.seen_restarts >= 0 && u.restarts > b.seen_restarts && !b.ever_ready && b.draft_active) {
      // The server died before it ever answered /health while the optional
      // drafter was attached: run without it rather than loop.
      logf(b.id + ": start with drafter failed; drafter disabled (" + g_state_dir + "/" + b.id + ".draft.off)");
      set_flag(b.id, "draft.off", true);
      systemctl("restart", b.unit);
    }
    b.seen_restarts = u.restarts;
    if (u.freezer == "frozen") {
      b.state = "paused";
    } else {
      const int code = u.active == "active" ? health(b.port) : 0;
      if (code == 200) {
        if (b.state != "ready") b.ready_since = time(nullptr);
        b.state = "ready";
        b.ever_ready = true;
      } else {
        b.state = "loading";
      }
    }
    return;
  }
  b.ever_ready = false;
  b.seen_restarts = u.restarts;

  if (m.verify == Artifact::BAD) { b.state = "corrupt"; b.detail = m.verify_detail; return; }
  if (m.exists && m.expected() && m.size > m.expected()) {
    b.state = "corrupt";
    b.detail = "Datei ist größer als bei Hugging Face";
    return;
  }
  if (m.verify == Artifact::RUNNING) { b.state = "verifying"; return; }
  if (m.verify != Artifact::OK) {
    b.state = m.exists || m.writer ? "downloading" : "missing";
    if (!m.expected()) b.detail = "Größe bei Hugging Face noch nicht bekannt";
    else if (!m.writer && now - m.last_growth > std::chrono::seconds(g_quiet_s))
      b.detail = "Download wird fortgesetzt";
    return;
  }
  if (flag(b.id, "stopped")) { b.state = "stopped"; return; }
  // The optional drafter usually completes moments after the model (same
  // download chain). Give it a few minutes instead of restarting later.
  const Artifact &d = b.draft;
  if (d.configured && d.verify != Artifact::OK && d.verify != Artifact::BAD && !flag(b.id, "draft.off")) {
    const bool moving = d.writer || d.verify == Artifact::RUNNING ||
                        (d.exists && now - d.last_growth < std::chrono::minutes(2));
    if (moving && now - b.model_verified_at < std::chrono::minutes(10)) {
      b.state = "downloading";
      b.detail = "Modell fertig, Drafter folgt";
      return;
    }
  }
  if (u.active == "failed") {
    b.state = "failed";
    b.detail = "Start fehlgeschlagen (" + u.result + ")";
    // StartLimit hit or a hard error: try again every 10 minutes.
    if (b.started_once && now - b.last_start < std::chrono::minutes(10)) return;
    systemctl("reset-failed", b.unit);
  }
  if (b.started_once && now - b.last_start < std::chrono::seconds(20)) {
    if (b.state != "failed") b.state = "loading";
    return;
  }
  systemctl("start", b.unit);
  b.started_once = true;
  b.last_start = now;
  b.state = "loading";
}

// The room saves one small slot file per bot and day (the evaluated system
// prefix). Older ones are of no use; conversations are never saved to disk.
static void prune_prefix_files(const std::string &bot) {
  const std::string dir = g_state_dir + "/slots/" + bot;
  DIR *d = opendir(dir.c_str());
  if (!d) return;
  const time_t now = time(nullptr);
  while (auto *e = readdir(d)) {
    const std::string name = e->d_name;
    if (name.compare(0, 7, "prefix-") != 0 || name.size() < 12 || name.compare(name.size() - 4, 4, ".bin") != 0) continue;
    struct stat st{};
    const std::string path = dir + "/" + name;
    if (stat(path.c_str(), &st) == 0 && S_ISREG(st.st_mode) && now - st.st_mtime > 2 * 86400) unlink(path.c_str());
  }
  closedir(d);
}

static Artifact artifact_from(const Val *v, const char *kind) {
  Artifact a;
  a.kind = kind;
  if (!v || !v->isObj()) return a;
  auto s = [&](const char *k) { auto p = v->find(k); return p ? p->asStr() : std::string(); };
  a.path = s("path");
  a.url = s("url");
  a.pinned_sha = s("sha256");
  if (auto p = v->find("size")) a.pinned_size = (uint64_t)p->asNum();
  a.configured = !a.path.empty();
  return a;
}

static std::string sha256_hex(const std::string &s) {
  unsigned char md[EVP_MAX_MD_SIZE];
  unsigned len = 0;
  EVP_Digest(s.data(), s.size(), md, &len, EVP_sha256(), nullptr);
  static const char *hex = "0123456789abcdef";
  std::string out;
  for (unsigned i = 0; i < len; i++) { out += hex[md[i] >> 4]; out += hex[md[i] & 15]; }
  return out;
}

static void write_status(const std::map<std::string, Bot> &bots) {
  Val root = Val::object();
  root["updated"] = (double)time(nullptr);
  Val list = Val::object();
  for (auto &kv : bots) {
    const Bot &b = kv.second;
    Val v = Val::object();
    v["state"] = b.state;
    v["label"] = b.label;
    v["port"] = b.port;
    v["numa"] = b.numa;
    v["ctx"] = b.ctx;
    v["detail"] = b.detail;
    v["model"] = artifact_json(b.model);
    if (b.draft.configured) {
      Val d = artifact_json(b.draft);
      d["active"] = b.draft_active;
      v["draft"] = d;
    }
    if (b.state == "ready") v["since"] = (double)b.ready_since;
    v["restart_pending"] = b.restart_pending;
    list[kv.first] = v;
  }
  root["bots"] = list;
  write_atomic(g_run_dir + "/status.json", mj::dump(root) + "\n");
}

static int usage() {
  std::cerr << "usage: mag-modeld [--config FILE]\n"
               "       mag-modeld status\n"
               "       mag-modeld ctl <bot> start|stop|restart|freeze|thaw\n"
               "       mag-modeld spec-hash <bot>\n";
  return 64;
}

int main(int argc, char **argv) {
  std::string config = "/usr/local/lib/mag-models/models.json";
  if (const char *e = getenv("MAG_MODELS_CONF")) config = e;
  std::vector<std::string> args(argv + 1, argv + argc);
  if (!args.empty() && args[0] == "--config") {
    if (args.size() < 2) return usage();
    config = args[1];
    args.erase(args.begin(), args.begin() + 2);
  }
  Val conf;
  if (!mj::parse(read_file(config, 4 << 20), conf) || !conf.isObj()) {
    std::cerr << "mag-modeld: cannot read " << config << "\n";
    return 78;
  }
  if (auto p = conf.find("state_dir")) g_state_dir = p->asStr(g_state_dir);
  if (const char *e = getenv("MAG_MODELS_RUN")) g_run_dir = e;
  if (const char *e = getenv("MAG_MODELD_SYSTEMCTL")) g_systemctl = e;
  if (const char *e = getenv("MAG_MODELD_QUIET_S")) g_quiet_s = std::max(1, atoi(e));
  int poll_ms = 3000;
  if (const char *e = getenv("MAG_MODELD_POLL_MS")) poll_ms = std::max(100, atoi(e));

  if (!args.empty() && args[0] == "status") {
    std::cout << read_file(g_run_dir + "/status.json");
    return 0;
  }
  if (!args.empty() && args[0] == "ctl") {
    if (args.size() != 3) return usage();
    const Val *bots = conf.find("bots");
    if (!bots || !bots->find(args[1])) { std::cerr << "unknown bot\n"; return 64; }
    return write_atomic(g_state_dir + "/control/" + args[1], args[2] + "\n", 0660) ? 0 : 1;
  }
  const Val *list = conf.find("bots");
  const Val *runtime = conf.find("runtime");
  const std::string rev = runtime && runtime->find("rev") ? runtime->find("rev")->asStr() : "";
  // Identity of a bot's launch configuration; run.sh records it at start.
  auto spec_hash = [&](const Val &bot) { return sha256_hex(mj::dump(bot) + "\n" + rev); };
  if (!args.empty() && args[0] == "spec-hash") {
    const Val *bot = args.size() == 2 && list ? list->find(args[1]) : nullptr;
    if (!bot) return usage();
    std::cout << spec_hash(*bot) << "\n";
    return 0;
  }
  if (!args.empty()) return usage();

  std::map<std::string, Bot> bots;
  if (list && list->isObj())
    for (auto &kv : *list->obj) {
      Bot b;
      b.id = kv.first;
      b.unit = "mag-model@" + b.id + ".service";
      auto num = [&](const char *k) { auto p = kv.second.find(k); return p ? (int)p->asNum() : 0; };
      b.label = kv.second.find("label") ? kv.second.find("label")->asStr() : b.id;
      b.port = num("port");
      b.numa = num("numa");
      b.ctx = num("ctx");
      b.model = artifact_from(kv.second.find("model"), "model");
      b.draft = artifact_from(kv.second.find("draft"), "draft");
      b.spec_hash = spec_hash(kv.second);
      if (!b.model.configured || b.port <= 0) {
        std::cerr << "mag-modeld: bot " << b.id << " lacks model.path/port\n";
        return 78;
      }
      bots.emplace(b.id, std::move(b));
    }
  if (bots.empty()) { std::cerr << "mag-modeld: no bots configured\n"; return 78; }

  signal(SIGPIPE, SIG_IGN);
  struct sigaction sa{};
  sa.sa_handler = [](int) { g_stop = true; };
  sigaction(SIGTERM, &sa, nullptr);
  sigaction(SIGINT, &sa, nullptr);
  curl_global_init(CURL_GLOBAL_DEFAULT);
  mkdir(g_run_dir.c_str(), 0755);
  std::map<std::string, std::string> last;
  logf("mag-modeld: " + std::to_string(bots.size()) + " bots from " + config);
  time_t pruned = 0;
  while (!g_stop) {
    if (time(nullptr) - pruned > 3600) {
      pruned = time(nullptr);
      for (auto &kv : bots) prune_prefix_files(kv.first);
    }
    read_control(bots);
    for (auto &kv : bots) {
      step(kv.second);
      const std::string now = kv.second.state + (kv.second.detail.empty() ? "" : " (" + kv.second.detail + ")");
      if (last[kv.first] != now) logf(kv.first + ": " + now);
      last[kv.first] = now;
    }
    write_status(bots);
    for (int i = 0; i < poll_ms / 100 && !g_stop; i++) usleep(100 * 1000);
  }
  return 0;
}
