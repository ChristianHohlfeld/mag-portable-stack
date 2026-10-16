#include <atomic>
#pragma once
#include "json.hpp"
#include <algorithm>
#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <netinet/in.h>
#include <poll.h>
#include <random>
#include <spawn.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>
extern char **environ;
namespace mag {
using mj::Val;
namespace fs = std::filesystem;
inline std::string env(const char *k, std::string d = "") {
  auto v = getenv(k);
  return v && *v ? v : d;
}
inline std::string read(const fs::path &p) {
  std::ifstream f(p, std::ios::binary);
  if (!f)
    throw std::runtime_error("Cannot read " + p.string());
  return {std::istreambuf_iterator<char>(f), {}};
}
inline std::string trim(std::string s) {
  auto a = s.find_first_not_of(" \r\n\t"), b = s.find_last_not_of(" \r\n\t");
  return a == s.npos ? "" : s.substr(a, b - a + 1);
}
inline std::string quote(const std::string &s) {
  std::string r = "'";
  for (char c : s)
    r += c == '\'' ? "'\\''" : std::string(1, c);
  return r + "'";
}
inline std::string randomhex() {
  unsigned char b[16];
  int f = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
  if (f < 0 || ::read(f, b, 16) != 16)
    throw std::runtime_error("Random source unavailable");
  close(f);
  std::string s;
  for (auto c : b) {
    s += "0123456789abcdef"[c >> 4];
    s += "0123456789abcdef"[c & 15];
  }
  return s;
}
inline void write(const fs::path &p, const std::string &s) {
  auto tmp = p.string() + "." + randomhex() + ".tmp";
  int fd = open(tmp.c_str(), O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);
  if (fd < 0)
    throw std::runtime_error("Cannot write " + p.string());
  size_t n = 0;
  while (n < s.size()) {
    auto k = ::write(fd, s.data() + n, s.size() - n);
    if (k <= 0) {
      close(fd);
      unlink(tmp.c_str());
      throw std::runtime_error("Write failed");
    }
    n += k;
  }
  if (fsync(fd)) {
    close(fd);
    unlink(tmp.c_str());
    throw std::runtime_error("fsync failed");
  }
  close(fd);
  if (rename(tmp.c_str(), p.c_str()))
    throw std::runtime_error("Rename failed");
}
inline Val json(const std::string &s) {
  Val v;
  if (!mj::parse(s, v))
    throw std::runtime_error("Invalid JSON");
  return v;
}
inline std::string str(const Val &v, const char *k, std::string d = "") {
  auto p = v.find(k);
  return p ? p->asStr(d) : d;
}
inline long num(const Val &v, const char *k, long d = 0) {
  auto p = v.find(k);
  return p ? (long)p->asNum() : d;
}
struct Result {
  int code = -1;
  std::string out;
};
inline Result run(const std::vector<std::string> &args, int seconds = 120,
                  size_t cap = 4 * 1024 * 1024,
                  const std::atomic<bool> *cancel = nullptr) {
  if (args.empty())
    throw std::runtime_error("Empty process");
  int p[2];
  if (pipe2(p, O_CLOEXEC))
    throw std::runtime_error("pipe failed");
  posix_spawn_file_actions_t acts;
  posix_spawn_file_actions_init(&acts);
  posix_spawn_file_actions_adddup2(&acts, p[1], 1);
  posix_spawn_file_actions_adddup2(&acts, p[1], 2);
  posix_spawn_file_actions_addopen(&acts, 0, "/dev/null", O_RDONLY, 0);
  posix_spawn_file_actions_addclose(&acts, p[0]);
  posix_spawn_file_actions_addclose(&acts, p[1]);
  posix_spawnattr_t attr;
  posix_spawnattr_init(&attr);
  posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
  posix_spawnattr_setpgroup(&attr, 0);
  std::vector<char *> argv;
  for (auto &a : args)
    argv.push_back(const_cast<char *>(a.c_str()));
  argv.push_back(nullptr);
  pid_t pid;
  int err = posix_spawnp(&pid, argv[0], &acts, &attr, argv.data(), environ);
  posix_spawn_file_actions_destroy(&acts);
  posix_spawnattr_destroy(&attr);
  close(p[1]);
  if (err) {
    close(p[0]);
    throw std::runtime_error("Spawn failed: " + std::string(strerror(err)));
  }
  fcntl(p[0], F_SETFL, O_NONBLOCK);
  Result r;
  int st = 0;
  bool exited = false, closed = false;
  auto end = std::chrono::steady_clock::now() + std::chrono::seconds(seconds);
  while (!exited || !closed) {
    char b[8192];
    ssize_t n;
    while ((n = ::read(p[0], b, sizeof b)) > 0) {
      if (r.out.size() + n > cap) {
        kill(-pid, SIGKILL);
        waitpid(pid, &st, 0);
        close(p[0]);
        throw std::runtime_error("Process output limit");
      }
      r.out.append(b, n);
    }
    if (n == 0)
      closed = true;
    if (!exited && waitpid(pid, &st, WNOHANG) == pid)
      exited = true;
    if (exited && closed)
      break;
    if (std::chrono::steady_clock::now() > end) {
      kill(-pid, SIGKILL);
      if (!exited)
        waitpid(pid, &st, 0);
      close(p[0]);
      throw std::runtime_error("Process timeout");
    }
    if (cancel && cancel->load()) { // user pressed Stop: end the whole group
      kill(-pid, SIGTERM); kill(pid, SIGTERM);
      for (int i = 0; i < 40 && !exited; i++) {
        if (waitpid(pid, &st, WNOHANG) == pid) exited = true; else usleep(50000);
      }
      if (!exited) { kill(-pid, SIGKILL); kill(pid, SIGKILL); waitpid(pid, &st, 0); }
      close(p[0]);
      throw std::runtime_error("Abgebrochen (Stop)");
    }
    pollfd q{p[0], POLLIN, 0};
    poll(&q, 1, 30);
  }
  close(p[0]);
  r.code = WIFEXITED(st) ? WEXITSTATUS(st) : 128 + WTERMSIG(st);
  return r;
}
inline std::string checked(const std::vector<std::string> &a, int t = 120) {
  auto r = run(a, t);
  if (r.code)
    throw std::runtime_error(a[0] + ": " + r.out.substr(0, 1000));
  return r.out;
}
inline std::string decode(const std::string &s) {
  std::string o;
  for (size_t i = 0; i < s.size(); ++i) {
    if (s[i] == '%' && i + 2 < s.size()) {
      auto h = [](char c) -> int {
        if (c >= '0' && c <= '9')
          return c - '0';
        c = tolower((unsigned char)c);
        return c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
      };
      int a = h(s[i + 1]), b = h(s[i + 2]);
      if (a < 0 || b < 0)
        throw std::runtime_error("Bad URL escape");
      o += (char)((a << 4) | b);
      i += 2;
    } else
      o += s[i] == '+' ? ' ' : s[i];
  }
  return o;
}
inline std::string param(const std::string &q, const std::string &k) {
  std::istringstream ss(q);
  std::string p;
  while (std::getline(ss, p, '&')) {
    auto e = p.find('=');
    if (decode(p.substr(0, e)) == k)
      return e == p.npos ? "" : decode(p.substr(e + 1));
  }
  return "";
}
inline std::string unbase64(const std::string &s) {
  std::string o;
  unsigned v = 0;
  int bits = -8;
  std::string abc =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  for (unsigned char c : s) {
    auto p = abc.find(c);
    if (p == abc.npos) {
      if (c == '=')
        break;
      continue;
    }
    v = (v << 6) + (unsigned)p;
    bits += 6;
    if (bits >= 0) {
      o += (char)((v >> bits) & 255);
      bits -= 8;
    }
  }
  return o;
}
inline bool sendall(int fd, const std::string &s) {
  size_t n = 0;
  while (n < s.size()) {
    auto k = send(fd, s.data() + n, s.size() - n, MSG_NOSIGNAL);
    if (k < 0 && errno == EINTR)
      continue;
    if (k <= 0)
      return false;
    n += k;
  }
  return true;
}
struct Request {
  std::string method, path, query, body;
  std::map<std::string, std::string> headers;
};
inline Request request(int fd, size_t max = 64 * 1024 * 1024) {
  Request r;
  std::string data;
  char b[8192];
  size_t end;
  while ((end = data.find("\r\n\r\n")) == data.npos) {
    auto n = recv(fd, b, sizeof b, 0);
    if (n <= 0)
      throw std::runtime_error("Incomplete HTTP headers");
    data.append(b, n);
    if (data.size() > 65536)
      throw std::runtime_error("Header limit");
  }
  std::istringstream ss(data.substr(0, end));
  std::string line;
  std::getline(ss, line);
  std::istringstream first(line);
  std::string target, version;
  first >> r.method >> target >> version;
  if (version != "HTTP/1.1" && version != "HTTP/1.0")
    throw std::runtime_error("Invalid HTTP version");
  auto q = target.find('?');
  r.path = target.substr(0, q);
  if (q != target.npos)
    r.query = target.substr(q + 1);
  while (std::getline(ss, line)) {
    auto c = line.find(':');
    if (c == line.npos)
      throw std::runtime_error("Invalid header");
    auto k = trim(line.substr(0, c));
    std::transform(k.begin(), k.end(), k.begin(), ::tolower);
    if (r.headers.count(k))
      throw std::runtime_error("Duplicate header");
    r.headers[k] = trim(line.substr(c + 1));
  }
  if (r.headers.count("transfer-encoding"))
    throw std::runtime_error("Transfer encoding unsupported");
  size_t size = 0;
  if (r.headers.count("content-length")) {
    auto s = r.headers["content-length"];
    if (s.empty() || s.find_first_not_of("0123456789") != s.npos)
      throw std::runtime_error("Invalid content length");
    size = std::stoull(s);
  }
  if (size > max)
    throw std::runtime_error("Body limit");
  r.body = data.substr(end + 4);
  while (r.body.size() < size) {
    auto n = recv(fd, b, sizeof b, 0);
    if (n <= 0)
      throw std::runtime_error("Incomplete body");
    r.body.append(b, n);
  }
  r.body.resize(size);
  return r;
}
inline void response(int fd, int code, const std::string &body,
                     std::string type = "application/json") {
  sendall(fd, "HTTP/1.1 " + std::to_string(code) +
                  " Response\r\nConnection: close\r\nCache-Control: "
                  "no-store\r\nContent-Type: " +
                  type + "\r\nContent-Length: " + std::to_string(body.size()) +
                  "\r\n\r\n" + body);
}
inline int listen(const std::string &ip, int port) {
  int fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  int one = 1;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons(port);
  if (inet_pton(AF_INET, ip.c_str(), &a.sin_addr) != 1 ||
      bind(fd, (sockaddr *)&a, sizeof a) || ::listen(fd, 64)) {
    close(fd);
    throw std::runtime_error("Listen failed on " + ip + ":" +
                             std::to_string(port));
  }
  return fd;
}
inline std::string http(const std::string &ip, int port,
                        const std::string &path) {
  int f = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  timeval t{10, 0};
  setsockopt(f, SOL_SOCKET, SO_RCVTIMEO, &t, sizeof t);
  setsockopt(f, SOL_SOCKET, SO_SNDTIMEO, &t, sizeof t);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons(port);
  inet_pton(AF_INET, ip.c_str(), &a.sin_addr);
  if (connect(f, (sockaddr *)&a, sizeof a)) {
    close(f);
    throw std::runtime_error("HTTP connect failed");
  }
  sendall(f, "GET " + path + " HTTP/1.1\r\nHost: " + ip + ":" +
                 std::to_string(port) + "\r\nConnection: close\r\n\r\n");
  std::string out;
  char b[8192];
  ssize_t n;
  while ((n = recv(f, b, sizeof b, 0)) > 0) {
    out.append(b, n);
    auto split = out.find("\r\n\r\n");
    if (split != out.npos) {
      auto headers = out.substr(0, split);
      std::transform(headers.begin(), headers.end(), headers.begin(),
                     ::tolower);
      auto length = headers.find("content-length:");
      if (length != headers.npos) {
        auto size = std::stoull(headers.substr(length + 15));
        if (out.size() >= split + 4 + size)
          break;
      }
    }
    if (out.size() > 4 * 1024 * 1024) {
      close(f);
      throw std::runtime_error("HTTP response limit");
    }
  }
  close(f);
  auto p = out.find("\r\n\r\n");
  if (p == out.npos || out.substr(0, 12).find("200") == std::string::npos)
    throw std::runtime_error("HTTP failed");
  return out.substr(p + 4);
}
} // namespace mag
