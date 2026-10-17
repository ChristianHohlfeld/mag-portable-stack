// Local/tailnet S23 control. One session, token auth, no public bind.
// Does not listen on 8720 and does not talk to cloudflared or s23pub.
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <spawn.h>
#include <poll.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <openssl/sha.h>

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

extern char **environ;

namespace {

struct AdbResult {
  int code = -1;
  bool timed_out = false;
  std::string out;
  std::string err;
};

std::string g_token;
std::string g_adb = "adb";
std::string g_serial;
std::string g_bind = "127.0.0.1";
int g_port = 18721;
int g_cmd_timeout_ms = 4000;
int g_idle_ms = 30000;
int g_frame_ms = 200;
int g_listen = -1;

struct Session {
  std::mutex mu;
  std::string id;
  std::chrono::steady_clock::time_point used{};
  bool held = false;
};

Session g_session;

std::string env_str(const char *key, const std::string &fallback) {
  const char *v = std::getenv(key);
  return v && *v ? std::string(v) : fallback;
}

int env_int(const char *key, int fallback, int lo, int hi) {
  const char *v = std::getenv(key);
  if (!v || !*v) return fallback;
  char *end = nullptr;
  long n = std::strtol(v, &end, 10);
  if (!end || *end || n < lo || n > hi) return fallback;
  return static_cast<int>(n);
}

bool ct_eq(const std::string &a, const std::string &b) {
  if (a.size() != b.size()) return false;
  unsigned char d = 0;
  for (size_t i = 0; i < a.size(); ++i) d |= static_cast<unsigned char>(a[i] ^ b[i]);
  return d == 0;
}

bool valid_serial(const std::string &s) {
  if (s.empty() || s.size() > 80) return false;
  for (unsigned char c : s)
    if (!std::isalnum(c) && c != ':' && c != '.' && c != '_' && c != '-') return false;
  return true;
}

bool ipv4_parts(const std::string &ip, unsigned &a, unsigned &b, unsigned &c, unsigned &d) {
  char extra = 0;
  if (std::sscanf(ip.c_str(), "%u.%u.%u.%u%c", &a, &b, &c, &d, &extra) != 4) return false;
  return a < 256 && b < 256 && c < 256 && d < 256;
}

bool tailnet_ip(const std::string &ip) {
  unsigned a, b, c, d;
  if (ipv4_parts(ip, a, b, c, d)) {
    uint32_t v = (a << 24) | (b << 16) | (c << 8) | d;
    return v >= 0x64400000u && v <= 0x647fffffu;
  }
  unsigned char bytes[16];
  if (inet_pton(AF_INET6, ip.c_str(), bytes) != 1) return false;
  return bytes[0] == 0xfd && bytes[1] == 0x7a && bytes[2] == 0x11 &&
         bytes[3] == 0x5c && bytes[4] == 0xa1 && bytes[5] == 0xe0;
}

bool bind_allowed(const std::string &ip) {
  if (ip == "127.0.0.1" || ip == "::1") return true;
  if (ip == "0.0.0.0" || ip == "::" || ip == "*") return false;
  const char *flag = std::getenv("MAG_S23_BIND_TAILNET");
  if (!flag || std::strcmp(flag, "1") != 0) return false;
  return tailnet_ip(ip);
}

bool port_allowed(int port) {
  return port > 0 && port <= 65535 && port != 8720;
}

std::string trim_copy(std::string s) {
  while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
  size_t i = 0;
  while (i < s.size() && (s[i] == ' ' || s[i] == '\n' || s[i] == '\r')) ++i;
  return s.substr(i);
}

bool load_token() {
  if (const char *e = std::getenv("MAG_S23_TOKEN")) {
    g_token = e;
  } else {
    std::string path = env_str("MAG_S23_TOKEN_FILE", "/run/mag-s23/token");
    struct stat st {};
    if (stat(path.c_str(), &st) != 0 || !S_ISREG(st.st_mode)) {
      std::cerr << "s23ctl: token file missing\n";
      return false;
    }
    if (st.st_mode & 077) {
      std::cerr << "s23ctl: token file must be mode 0600\n";
      return false;
    }
    FILE *f = std::fopen(path.c_str(), "r");
    if (!f) return false;
    char buf[512];
    if (!std::fgets(buf, sizeof buf, f)) {
      std::fclose(f);
      return false;
    }
    std::fclose(f);
    g_token = trim_copy(buf);
  }
  if (g_token.size() < 16 || g_token.size() > 256) {
    std::cerr << "s23ctl: token must be 16 to 256 characters\n";
    return false;
  }
  return true;
}

std::string random_id() {
  unsigned char buf[16];
  FILE *f = std::fopen("/dev/urandom", "rb");
  if (!f || std::fread(buf, 1, sizeof buf, f) != sizeof buf) {
    if (f) std::fclose(f);
    return {};
  }
  std::fclose(f);
  static const char *hex = "0123456789abcdef";
  std::string s;
  for (unsigned char c : buf) {
    s.push_back(hex[c >> 4]);
    s.push_back(hex[c & 15]);
  }
  return s;
}

bool send_all(int fd, const char *p, size_t n) {
  while (n) {
    ssize_t w = ::send(fd, p, n, MSG_NOSIGNAL);
    if (w < 0) {
      if (errno == EINTR) continue;
      return false;
    }
    p += w;
    n -= static_cast<size_t>(w);
  }
  return true;
}

bool send_str(int fd, const std::string &s) { return send_all(fd, s.data(), s.size()); }

void read_some(int fd, std::string &out, size_t cap, bool &eof) {
  char buf[8192];
  ssize_t n = ::read(fd, buf, sizeof buf);
  if (n < 0) {
    if (errno == EINTR || errno == EAGAIN) return;
    eof = true;
    return;
  }
  if (n == 0) {
    eof = true;
    return;
  }
  if (out.size() + static_cast<size_t>(n) > cap) {
    eof = true;
    return;
  }
  out.append(buf, static_cast<size_t>(n));
}

AdbResult run_argv(const std::vector<std::string> &args, int timeout_ms, size_t cap) {
  AdbResult r;
  int outp[2] = {-1, -1};
  int errp[2] = {-1, -1};
  if (pipe2(outp, O_CLOEXEC) != 0 || pipe2(errp, O_CLOEXEC) != 0) {
    r.err = "pipe failed";
    return r;
  }
  posix_spawn_file_actions_t fa;
  posix_spawn_file_actions_init(&fa);
  posix_spawn_file_actions_adddup2(&fa, outp[1], 1);
  posix_spawn_file_actions_adddup2(&fa, errp[1], 2);
  posix_spawn_file_actions_addclose(&fa, outp[0]);
  posix_spawn_file_actions_addclose(&fa, outp[1]);
  posix_spawn_file_actions_addclose(&fa, errp[0]);
  posix_spawn_file_actions_addclose(&fa, errp[1]);
  std::vector<char *> av;
  av.reserve(args.size() + 1);
  for (const auto &s : args) av.push_back(const_cast<char *>(s.c_str()));
  av.push_back(nullptr);
  pid_t pid = 0;
  int rc = posix_spawnp(&pid, av[0], &fa, nullptr, av.data(), environ);
  posix_spawn_file_actions_destroy(&fa);
  close(outp[1]);
  close(errp[1]);
  if (rc != 0) {
    close(outp[0]);
    close(errp[0]);
    r.code = 127;
    r.err = "spawn failed";
    return r;
  }
  bool out_eof = false, err_eof = false;
  auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (!out_eof || !err_eof) {
    auto now = std::chrono::steady_clock::now();
    if (now >= deadline) {
      r.timed_out = true;
      break;
    }
    int remain = static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count());
    pollfd fds[2];
    int nfd = 0;
    int ix[2] = {-1, -1};
    if (!out_eof) {
      ix[0] = nfd;
      fds[nfd].fd = outp[0];
      fds[nfd].events = POLLIN;
      ++nfd;
    }
    if (!err_eof) {
      ix[1] = nfd;
      fds[nfd].fd = errp[0];
      fds[nfd].events = POLLIN;
      ++nfd;
    }
    int pr = poll(fds, static_cast<nfds_t>(nfd), remain);
    if (pr < 0 && errno == EINTR) continue;
    if (pr <= 0) {
      r.timed_out = true;
      break;
    }
    if (ix[0] >= 0 && (fds[ix[0]].revents & (POLLIN | POLLHUP | POLLERR)))
      read_some(outp[0], r.out, cap, out_eof);
    if (ix[1] >= 0 && (fds[ix[1]].revents & (POLLIN | POLLHUP | POLLERR)))
      read_some(errp[0], r.err, 65536, err_eof);
  }
  close(outp[0]);
  close(errp[0]);
  if (r.timed_out) kill(pid, SIGKILL);
  int status = 0;
  while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
  if (r.timed_out) r.code = -1;
  else if (WIFEXITED(status)) r.code = WEXITSTATUS(status);
  else r.code = -1;
  return r;
}

bool looks_disconnected(const AdbResult &r) {
  if (r.timed_out || r.code == 127 || r.code == 0) return false;
  std::string s = r.out + "\n" + r.err;
  return s.find("offline") != std::string::npos || s.find("no devices") != std::string::npos ||
         s.find("not found") != std::string::npos || s.find("closed") != std::string::npos;
}

std::vector<std::string> adb_base() {
  return {g_adb, "-s", g_serial};
}

AdbResult adb_run(const std::vector<std::string> &tail, bool allow_retry) {
  auto args = adb_base();
  args.insert(args.end(), tail.begin(), tail.end());
  AdbResult r = run_argv(args, g_cmd_timeout_ms, 8 * 1024 * 1024);
  if (!allow_retry || !looks_disconnected(r)) return r;
  auto rec = adb_base();
  rec.push_back("reconnect");
  run_argv(rec, g_cmd_timeout_ms, 65536);
  return run_argv(args, g_cmd_timeout_ms, 8 * 1024 * 1024);
}

std::string json_escape(const std::string &s) {
  std::string o;
  for (unsigned char c : s) {
    switch (c) {
      case '"': o += "\\\""; break;
      case '\\': o += "\\\\"; break;
      case '\n': o += "\\n"; break;
      case '\r': o += "\\r"; break;
      default:
        if (c < 0x20) {
          char b[8];
          std::snprintf(b, sizeof b, "\\u%04x", c);
          o += b;
        } else o.push_back(static_cast<char>(c));
    }
  }
  return o;
}

bool json_string(const std::string &body, const char *key, std::string &out) {
  std::string pat = std::string("\"") + key + "\"";
  auto p = body.find(pat);
  if (p == std::string::npos) return false;
  p = body.find(':', p + pat.size());
  if (p == std::string::npos) return false;
  p = body.find('"', p + 1);
  if (p == std::string::npos) return false;
  ++p;
  out.clear();
  while (p < body.size()) {
    char c = body[p++];
    if (c == '"') return true;
    if (c == '\\') {
      if (p >= body.size()) return false;
      char e = body[p++];
      if (e == 'n') out.push_back('\n');
      else if (e == 'u') return false;
      else out.push_back(e);
    } else out.push_back(c);
  }
  return false;
}

bool json_long(const std::string &body, const char *key, long &out) {
  std::string pat = std::string("\"") + key + "\"";
  auto p = body.find(pat);
  if (p == std::string::npos) return false;
  p = body.find(':', p + pat.size());
  if (p == std::string::npos) return false;
  ++p;
  while (p < body.size() && std::isspace(static_cast<unsigned char>(body[p]))) ++p;
  char *end = nullptr;
  long n = std::strtol(body.c_str() + p, &end, 10);
  if (end == body.c_str() + p) return false;
  out = n;
  return true;
}

struct Request {
  std::string method;
  std::string path;
  std::map<std::string, std::string> headers;
  std::string body;
};

std::string lower(std::string s) {
  for (char &c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return s;
}

bool read_request(int fd, Request &req) {
  std::string buf;
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
  while (buf.find("\r\n\r\n") == std::string::npos) {
    auto now = std::chrono::steady_clock::now();
    if (now >= deadline || buf.size() > 16384) return false;
    int remain = static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count());
    pollfd pfd {};
    pfd.fd = fd;
    pfd.events = POLLIN;
    if (poll(&pfd, 1, remain) <= 0) return false;
    char tmp[2048];
    ssize_t n = ::recv(fd, tmp, sizeof tmp, 0);
    if (n <= 0) return false;
    buf.append(tmp, static_cast<size_t>(n));
  }
  auto cut = buf.find("\r\n\r\n");
  std::string head = buf.substr(0, cut);
  std::string rest = buf.substr(cut + 4);
  std::istringstream in(head);
  std::string line;
  if (!std::getline(in, line)) return false;
  if (!line.empty() && line.back() == '\r') line.pop_back();
  std::istringstream rl(line);
  std::string version;
  if (!(rl >> req.method >> req.path >> version)) return false;
  auto q = req.path.find('?');
  if (q != std::string::npos) req.path.resize(q);
  while (std::getline(in, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    auto c = line.find(':');
    if (c == std::string::npos) continue;
    std::string k = lower(trim_copy(line.substr(0, c)));
    std::string v = trim_copy(line.substr(c + 1));
    req.headers[k] = v;
  }
  size_t need = 0;
  if (req.headers.count("content-length")) {
    need = static_cast<size_t>(std::strtoul(req.headers["content-length"].c_str(), nullptr, 10));
    if (need > 65536) return false;
  }
  while (rest.size() < need) {
    char tmp[2048];
    ssize_t n = ::recv(fd, tmp, sizeof tmp, 0);
    if (n <= 0) return false;
    rest.append(tmp, static_cast<size_t>(n));
  }
  req.body = rest.substr(0, need);
  return true;
}

void http(int fd, int status, const std::string &type, const std::string &body,
          const std::vector<std::pair<std::string, std::string>> &extra) {
  std::ostringstream o;
  o << "HTTP/1.1 " << status << " X\r\nContent-Type: " << type
    << "\r\nContent-Length: " << body.size()
    << "\r\nConnection: close\r\nCache-Control: no-store\r\n";
  for (const auto &kv : extra) o << kv.first << ": " << kv.second << "\r\n";
  o << "\r\n";
  std::string h = o.str();
  send_str(fd, h);
  send_str(fd, body);
}

void json_out(int fd, int status, const std::string &body, long latency) {
  http(fd, status, "application/json", body,
       {{"X-S23-Latency-Ms", std::to_string(latency)}});
}

bool authorized(const Request &req) {
  auto it = req.headers.find("authorization");
  if (it == req.headers.end()) return false;
  const std::string prefix = "Bearer ";
  if (it->second.size() < prefix.size() || it->second.compare(0, prefix.size(), prefix) != 0)
    return false;
  return ct_eq(it->second.substr(prefix.size()), g_token);
}

std::string session_header(const Request &req) {
  auto it = req.headers.find("x-s23-session");
  return it == req.headers.end() ? std::string() : it->second;
}

bool session_ok(const std::string &id, bool touch) {
  std::lock_guard<std::mutex> lock(g_session.mu);
  if (!g_session.held || id.empty() || !ct_eq(id, g_session.id)) return false;
  auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - g_session.used)
                .count();
  if (ms >= g_idle_ms) {
    g_session.held = false;
    g_session.id.clear();
    return false;
  }
  if (touch) g_session.used = std::chrono::steady_clock::now();
  return true;
}

void session_touch_locked() { g_session.used = std::chrono::steady_clock::now(); }

std::string session_state() {
  std::lock_guard<std::mutex> lock(g_session.mu);
  if (!g_session.held) return "free";
  auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - g_session.used)
                .count();
  if (ms >= g_idle_ms) {
    g_session.held = false;
    g_session.id.clear();
    return "free";
  }
  return "held";
}

long elapsed_ms(std::chrono::steady_clock::time_point start) {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::steady_clock::now() - start)
      .count();
}

bool safe_text(const std::string &in, std::string &out) {
  if (in.empty() || in.size() > 200) return false;
  out.clear();
  for (unsigned char c : in) {
    if (c == ' ') {
      out += "%s";
      continue;
    }
    if (c < 0x21 || c > 0x7e) return false;
    if (std::string("%\"'$&;|<>\\`").find(static_cast<char>(c)) != std::string::npos) return false;
    out.push_back(static_cast<char>(c));
  }
  return true;
}

bool safe_key(const std::string &k) {
  if (k.size() < 9 || k.size() > 40 || k.compare(0, 8, "KEYCODE_") != 0) return false;
  for (size_t i = 8; i < k.size(); ++i) {
    unsigned char c = static_cast<unsigned char>(k[i]);
    if (!std::isupper(c) && !std::isdigit(c) && c != '_') return false;
  }
  return true;
}

std::string b64(const unsigned char *data, size_t n) {
  static const char *t = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string o;
  for (size_t i = 0; i < n; i += 3) {
    unsigned v = static_cast<unsigned>(data[i]) << 16;
    if (i + 1 < n) v |= static_cast<unsigned>(data[i + 1]) << 8;
    if (i + 2 < n) v |= data[i + 2];
    o.push_back(t[(v >> 18) & 63]);
    o.push_back(t[(v >> 12) & 63]);
    o.push_back(i + 1 < n ? t[(v >> 6) & 63] : '=');
    o.push_back(i + 2 < n ? t[v & 63] : '=');
  }
  return o;
}

std::string ws_accept(const std::string &key) {
  std::string src = key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
  unsigned char dig[SHA_DIGEST_LENGTH];
  SHA1(reinterpret_cast<const unsigned char *>(src.data()), src.size(), dig);
  return b64(dig, SHA_DIGEST_LENGTH);
}

bool ws_send(int fd, int opcode, const std::string &payload) {
  unsigned char hdr[10];
  hdr[0] = static_cast<unsigned char>(0x80 | (opcode & 0x0f));
  size_t hlen = 2;
  if (payload.size() < 126) {
    hdr[1] = static_cast<unsigned char>(payload.size());
  } else if (payload.size() <= 65535) {
    hdr[1] = 126;
    hdr[2] = static_cast<unsigned char>((payload.size() >> 8) & 0xff);
    hdr[3] = static_cast<unsigned char>(payload.size() & 0xff);
    hlen = 4;
  } else {
    hdr[1] = 127;
    uint64_t n = payload.size();
    for (int i = 0; i < 8; ++i)
      hdr[2 + i] = static_cast<unsigned char>((n >> (56 - 8 * i)) & 0xff);
    hlen = 10;
  }
  if (!send_all(fd, reinterpret_cast<char *>(hdr), hlen)) return false;
  return payload.empty() || send_all(fd, payload.data(), payload.size());
}

bool ws_read(int fd, int timeout_ms, int &opcode, std::string &payload) {
  auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  std::string buf;
  auto fill = [&](size_t need) -> bool {
    while (buf.size() < need) {
      auto now = std::chrono::steady_clock::now();
      if (now >= deadline) return false;
      int remain = static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count());
      pollfd pfd {};
      pfd.fd = fd;
      pfd.events = POLLIN;
      if (poll(&pfd, 1, remain) <= 0) return false;
      char tmp[4096];
      ssize_t n = ::recv(fd, tmp, sizeof tmp, 0);
      if (n <= 0) return false;
      buf.append(tmp, static_cast<size_t>(n));
    }
    return true;
  };
  if (!fill(2)) return false;
  opcode = static_cast<unsigned char>(buf[0]) & 0x0f;
  bool masked = (static_cast<unsigned char>(buf[1]) & 0x80) != 0;
  uint64_t len = static_cast<unsigned char>(buf[1]) & 0x7f;
  size_t pos = 2;
  if (len == 126) {
    if (!fill(4)) return false;
    len = (static_cast<unsigned char>(buf[2]) << 8) | static_cast<unsigned char>(buf[3]);
    pos = 4;
  } else if (len == 127) {
    if (!fill(10)) return false;
    len = 0;
    for (int i = 0; i < 8; ++i) len = (len << 8) | static_cast<unsigned char>(buf[2 + i]);
    pos = 10;
  }
  if (len > 1024 * 1024) return false;
  size_t need = pos + (masked ? 4 : 0) + static_cast<size_t>(len);
  if (!fill(need)) return false;
  unsigned char mask[4] = {0, 0, 0, 0};
  if (masked) {
    for (int i = 0; i < 4; ++i) mask[i] = static_cast<unsigned char>(buf[pos + i]);
    pos += 4;
  }
  payload.assign(buf, pos, static_cast<size_t>(len));
  if (masked)
    for (size_t i = 0; i < payload.size(); ++i)
      payload[i] = static_cast<char>(static_cast<unsigned char>(payload[i]) ^ mask[i % 4]);
  return true;
}

void serve_video(int fd, const std::string &session) {
  while (session_ok(session, true)) {
    int opcode = 0;
    std::string incoming;
    if (ws_read(fd, 1, opcode, incoming) && opcode == 8) {
      ws_send(fd, 8, "");
      return;
    }
    auto start = std::chrono::steady_clock::now();
    AdbResult shot = adb_run({"exec-out", "screencap", "-p"}, true);
    long ms = elapsed_ms(start);
    if (shot.timed_out || shot.code != 0 || shot.out.empty()) {
      std::string err = shot.timed_out ? "timeout" : trim_copy(shot.err.empty() ? shot.out : shot.err);
      ws_send(fd, 1, "{\"type\":\"error\",\"error\":\"" + json_escape(err) + "\"}");
      ws_send(fd, 8, "");
      return;
    }
    std::string meta = "{\"type\":\"frame\",\"latency_ms\":" + std::to_string(ms) +
                       ",\"bytes\":" + std::to_string(shot.out.size()) + "}";
    if (!ws_send(fd, 1, meta) || !ws_send(fd, 2, shot.out)) return;
    int opcode2 = 0;
    std::string ignore;
    if (ws_read(fd, g_frame_ms, opcode2, ignore) && opcode2 == 8) {
      ws_send(fd, 8, "");
      return;
    }
  }
  ws_send(fd, 8, "");
}

void handle(int fd, Request req) {
  auto start = std::chrono::steady_clock::now();
  if (!authorized(req)) {
    json_out(fd, 401, "{\"error\":\"unauthorized\"}", 0);
    return;
  }
  auto finish = [&](int status, const std::string &body) {
    json_out(fd, status, body, elapsed_ms(start));
  };
  if (req.method == "GET" && req.path == "/health") {
    finish(200, std::string("{\"ok\":true,\"bind\":\"") + json_escape(g_bind) +
                    "\",\"port\":" + std::to_string(g_port) + ",\"session\":\"" +
                    session_state() + "\"}");
    return;
  }
  if (req.method == "GET" && req.path == "/v1/status") {
    AdbResult st = adb_run({"get-state"}, true);
    AdbResult sz = adb_run({"shell", "wm", "size"}, true);
    if (st.timed_out || sz.timed_out) {
      finish(504, "{\"error\":\"adb timeout\"}");
      return;
    }
    if (st.code != 0) {
      finish(502, "{\"error\":\"" + json_escape(trim_copy(st.err.empty() ? st.out : st.err)) + "\"}");
      return;
    }
    finish(200, "{\"state\":\"" + json_escape(trim_copy(st.out)) + "\",\"display\":\"" +
                    json_escape(trim_copy(sz.out)) + "\",\"session\":\"" + session_state() + "\"}");
    return;
  }
  if (req.method == "POST" && req.path == "/v1/session") {
    std::lock_guard<std::mutex> lock(g_session.mu);
    if (g_session.held) {
      auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - g_session.used)
                    .count();
      if (ms < g_idle_ms) {
        finish(409, "{\"error\":\"session held\"}");
        return;
      }
    }
    g_session.id = random_id();
    g_session.held = !g_session.id.empty();
    session_touch_locked();
    if (!g_session.held) {
      finish(500, "{\"error\":\"no session id\"}");
      return;
    }
    finish(200, "{\"session\":\"" + g_session.id + "\",\"idle_ms\":" + std::to_string(g_idle_ms) + "}");
    return;
  }
  const bool needs_session = req.path.rfind("/v1/input/", 0) == 0 || req.path == "/v1/screenshot" ||
                             req.path == "/v1/latency" || req.path == "/v1/video" ||
                             (req.method == "DELETE" && req.path == "/v1/session");
  std::string sid = session_header(req);
  if (needs_session && !session_ok(sid, true)) {
    finish(440, "{\"error\":\"session required\"}");
    return;
  }
  if (req.method == "DELETE" && req.path == "/v1/session") {
    std::lock_guard<std::mutex> lock(g_session.mu);
    g_session.held = false;
    g_session.id.clear();
    finish(200, "{\"ok\":true}");
    return;
  }
  if (req.method == "GET" && req.path == "/v1/latency") {
    AdbResult r = adb_run({"shell", "echo", "s23"}, true);
    if (r.timed_out) {
      finish(504, "{\"error\":\"adb timeout\"}");
      return;
    }
    if (r.code != 0) {
      finish(502, "{\"error\":\"" + json_escape(trim_copy(r.err)) + "\"}");
      return;
    }
    finish(200, "{\"ok\":true,\"sample\":\"" + json_escape(trim_copy(r.out)) + "\"}");
    return;
  }
  if (req.method == "GET" && req.path == "/v1/screenshot") {
    AdbResult r = adb_run({"exec-out", "screencap", "-p"}, true);
    if (r.timed_out) {
      finish(504, "{\"error\":\"adb timeout\"}");
      return;
    }
    if (r.code != 0 || r.out.empty()) {
      finish(502, "{\"error\":\"" + json_escape(trim_copy(r.err.empty() ? "empty screenshot" : r.err)) + "\"}");
      return;
    }
    http(fd, 200, "image/png", r.out, {{"X-S23-Latency-Ms", std::to_string(elapsed_ms(start))}});
    return;
  }
  if (req.method == "GET" && req.path == "/v1/video") {
    auto up = req.headers.find("upgrade");
    auto key = req.headers.find("sec-websocket-key");
    if (up == req.headers.end() || lower(up->second) != "websocket" || key == req.headers.end()) {
      finish(400, "{\"error\":\"websocket required\"}");
      return;
    }
    std::string accept = ws_accept(trim_copy(key->second));
    std::string hdr =
        "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Accept: " +
        accept + "\r\n\r\n";
    if (!send_str(fd, hdr)) return;
    serve_video(fd, sid);
    return;
  }
  if (req.method == "POST" && req.path.rfind("/v1/input/", 0) == 0) {
    std::vector<std::string> tail;
    if (req.path == "/v1/input/tap") {
      long x = 0, y = 0;
      if (!json_long(req.body, "x", x) || !json_long(req.body, "y", y) || x < 0 || y < 0 || x > 20000 || y > 20000) {
        finish(400, "{\"error\":\"x and y must be 0..20000\"}");
        return;
      }
      tail = {"shell", "input", "tap", std::to_string(x), std::to_string(y)};
    } else if (req.path == "/v1/input/swipe") {
      long x1 = 0, y1 = 0, x2 = 0, y2 = 0, ms = 120;
      if (!json_long(req.body, "x1", x1) || !json_long(req.body, "y1", y1) || !json_long(req.body, "x2", x2) ||
          !json_long(req.body, "y2", y2) || x1 < 0 || y1 < 0 || x2 < 0 || y2 < 0 || x1 > 20000 || y1 > 20000 ||
          x2 > 20000 || y2 > 20000) {
        finish(400, "{\"error\":\"swipe coordinates must be 0..20000\"}");
        return;
      }
      json_long(req.body, "ms", ms);
      if (ms < 1) ms = 1;
      if (ms > 5000) ms = 5000;
      tail = {"shell", "input", "swipe", std::to_string(x1), std::to_string(y1), std::to_string(x2),
              std::to_string(y2), std::to_string(ms)};
    } else if (req.path == "/v1/input/text") {
      std::string text, safe;
      if (!json_string(req.body, "text", text) || !safe_text(text, safe)) {
        finish(400, "{\"error\":\"text must be 1..200 visible ASCII characters\"}");
        return;
      }
      tail = {"shell", "input", "text", safe};
    } else if (req.path == "/v1/input/key") {
      std::string key;
      long code = -1;
      if (json_string(req.body, "key", key)) {
        if (!safe_key(key)) {
          finish(400, "{\"error\":\"key must look like KEYCODE_HOME\"}");
          return;
        }
        tail = {"shell", "input", "keyevent", key};
      } else if (json_long(req.body, "code", code) && code >= 0 && code <= 999) {
        tail = {"shell", "input", "keyevent", std::to_string(code)};
      } else {
        finish(400, "{\"error\":\"key or code required\"}");
        return;
      }
    } else {
      finish(404, "{\"error\":\"unknown input\"}");
      return;
    }
    AdbResult r = adb_run(tail, true);
    if (r.timed_out) {
      finish(504, "{\"error\":\"adb timeout\"}");
      return;
    }
    if (r.code != 0) {
      finish(502, "{\"error\":\"" + json_escape(trim_copy(r.err.empty() ? r.out : r.err)) + "\"}");
      return;
    }
    finish(200, "{\"ok\":true}");
    return;
  }
  finish(404, "{\"error\":\"not found\"}");
}

void client_thread(int fd) {
  Request req;
  if (read_request(fd, req)) handle(fd, std::move(req));
  close(fd);
}

}  // namespace

int main(int argc, char **argv) {
  ::signal(SIGPIPE, SIG_IGN);
  if (argc == 3 && std::strcmp(argv[1], "--check-bind") == 0) return bind_allowed(argv[2]) ? 0 : 1;
  if (argc == 3 && std::strcmp(argv[1], "--check-port") == 0) return port_allowed(std::atoi(argv[2])) ? 0 : 1;
  g_bind = env_str("MAG_S23_BIND", "127.0.0.1");
  g_port = env_int("MAG_S23_PORT", 18721, 1, 65535);
  g_cmd_timeout_ms = env_int("MAG_S23_CMD_TIMEOUT_MS", 4000, 50, 60000);
  g_idle_ms = env_int("MAG_S23_SESSION_IDLE_MS", 30000, 50, 600000);
  g_frame_ms = env_int("MAG_S23_FRAME_MS", 200, 50, 5000);
  g_adb = env_str("MAG_S23_ADB", "adb");
  g_serial = env_str("MAG_S23_SERIAL", "");
  if (!bind_allowed(g_bind)) {
    std::cerr << "s23ctl: bind " << g_bind << " refused (loopback, or tailnet only with MAG_S23_BIND_TAILNET=1)\n";
    return 1;
  }
  if (!port_allowed(g_port)) {
    std::cerr << "s23ctl: port " << g_port << " refused (8720 is s23pub)\n";
    return 1;
  }
  if (!valid_serial(g_serial)) {
    std::cerr << "s23ctl: MAG_S23_SERIAL is required\n";
    return 1;
  }
  if (!load_token()) return 1;
  bool v6 = g_bind.find(':') != std::string::npos;
  g_listen = socket(v6 ? AF_INET6 : AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (g_listen < 0) return 1;
  int yes = 1;
  setsockopt(g_listen, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);
  if (v6) {
    int only = 1;
    setsockopt(g_listen, IPPROTO_IPV6, IPV6_V6ONLY, &only, sizeof only);
    sockaddr_in6 addr {};
    addr.sin6_family = AF_INET6;
    addr.sin6_port = htons(static_cast<uint16_t>(g_port));
    if (inet_pton(AF_INET6, g_bind.c_str(), &addr.sin6_addr) != 1 ||
        bind(g_listen, reinterpret_cast<sockaddr *>(&addr), sizeof addr) != 0) {
      std::cerr << "s23ctl: bind failed\n";
      return 1;
    }
  } else {
    sockaddr_in addr {};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(g_port));
    if (inet_pton(AF_INET, g_bind.c_str(), &addr.sin_addr) != 1 ||
        bind(g_listen, reinterpret_cast<sockaddr *>(&addr), sizeof addr) != 0) {
      std::cerr << "s23ctl: bind failed\n";
      return 1;
    }
  }
  if (listen(g_listen, 16) != 0) return 1;
  std::cerr << "s23ctl listening on " << g_bind << ":" << g_port << "\n";
  while (true) {
    int fd = accept4(g_listen, nullptr, nullptr, SOCK_CLOEXEC);
    if (fd < 0) {
      if (errno == EINTR) continue;
      return 1;
    }
    std::thread(client_thread, fd).detach();
  }
}
