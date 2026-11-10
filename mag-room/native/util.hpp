// Small shared helpers of the room: environment, time, ids, files, hashing,
// encoding and a subprocess runner with streamed output and cancellation.
#pragma once
#include "json.hpp"

#include <fcntl.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/rand.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

extern char **environ;

namespace mag {
using mj::Val;
namespace fs = std::filesystem;

// ------------------------------------------------------------------ basics
inline std::string env(const char *key, const std::string &def = "") {
  const char *v = getenv(key);
  return v && *v ? std::string(v) : def;
}
inline long envl(const char *key, long def) {
  const std::string v = env(key);
  if (v.empty()) return def;
  char *end = nullptr;
  const long n = strtol(v.c_str(), &end, 10);
  return end && *end == 0 ? n : def;
}
inline int64_t now_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch()).count();
}
inline int64_t now_s() { return now_ms() / 1000; }
inline int64_t mono_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::steady_clock::now().time_since_epoch()).count();
}
inline void logf(const std::string &line) { std::cerr << line << std::endl; }

inline std::string trim(const std::string &s) {
  const auto a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
  return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}
inline std::string lower(std::string s) {
  for (auto &c : s) c = (char)tolower((unsigned char)c);
  return s;
}
inline bool starts_with(const std::string &s, const std::string &p) {
  return s.size() >= p.size() && s.compare(0, p.size(), p) == 0;
}
inline bool ends_with(const std::string &s, const std::string &p) {
  return s.size() >= p.size() && s.compare(s.size() - p.size(), p.size(), p) == 0;
}
inline void replace_all(std::string &s, const std::string &from, const std::string &to) {
  if (from.empty()) return;
  for (size_t p = 0; (p = s.find(from, p)) != std::string::npos; p += to.size())
    s.replace(p, from.size(), to);
}
// At most max bytes, never splitting a code point, with a visible marker.
inline std::string clip(const std::string &s, size_t max, const std::string &marker = "") {
  if (s.size() <= max) return mj::utf8_fix(s);
  return mj::utf8_cut(s, max) + marker;
}
// Keep the end of a long text (live output tails).
inline std::string tail(const std::string &s, size_t max) {
  if (s.size() <= max) return mj::utf8_fix(s);
  size_t at = s.size() - max;
  while (at < s.size() && ((unsigned char)s[at] & 0xC0) == 0x80) ++at;
  return mj::utf8_fix(s.substr(at));
}

// JSON accessors that never throw.
inline std::string jstr(const Val &v, const char *key, const std::string &def = "") {
  const Val *p = v.find(key);
  return p && p->type == Val::STR ? p->str : def;
}
inline double jnum(const Val &v, const char *key, double def = 0) {
  const Val *p = v.find(key);
  return p && p->type == Val::NUM ? p->num : def;
}
inline bool jbool(const Val &v, const char *key, bool def = false) {
  const Val *p = v.find(key);
  if (!p) return def;
  if (p->type == Val::BOOL) return p->b;
  if (p->type == Val::NUM) return p->num != 0;
  return def;
}
inline Val jparse(const std::string &s) {
  Val v;
  if (!mj::parse(s, v)) return Val();
  return v;
}

// ------------------------------------------------------------- randomness
inline std::string random_bytes(size_t n) {
  std::string out(n, '\0');
  if (RAND_bytes((unsigned char *)out.data(), (int)n) != 1) abort();
  return out;
}
inline std::string hex(const std::string &raw) {
  static const char *digits = "0123456789abcdef";
  std::string out;
  out.reserve(raw.size() * 2);
  for (unsigned char c : raw) { out += digits[c >> 4]; out += digits[c & 15]; }
  return out;
}
inline std::string random_hex(size_t bytes) { return hex(random_bytes(bytes)); }
inline std::string random_token(size_t chars) {
  static const char *alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  std::string raw = random_bytes(chars), out;
  for (unsigned char c : raw) out += alphabet[c & 63];
  return out;
}
// Time-sortable id: milliseconds, a process-wide counter and random bits.
// Lexicographic order is creation order, also within one millisecond.
inline std::string new_id() {
  static std::mutex mu;
  static int64_t last = 0;
  static unsigned counter = 0;
  std::lock_guard<std::mutex> lk(mu);
  int64_t ms = now_ms();
  if (ms <= last) { ms = last; ++counter; } else { last = ms; counter = 0; }
  char buf[40];
  snprintf(buf, sizeof buf, "%012llx%04x%s", (unsigned long long)ms, counter & 0xffff, random_hex(3).c_str());
  return buf;
}
inline bool is_id(const std::string &s) {
  return s.size() >= 8 && s.size() <= 64 &&
         s.find_first_not_of("0123456789abcdef") == std::string::npos;
}
inline bool ct_equal(const std::string &a, const std::string &b) {
  if (a.size() != b.size()) return false;
  unsigned char diff = 0;
  for (size_t i = 0; i < a.size(); i++) diff |= (unsigned char)(a[i] ^ b[i]);
  return diff == 0;
}

// ------------------------------------------------------------------- hashes
inline std::string sha256_raw(const std::string &data) {
  unsigned char md[EVP_MAX_MD_SIZE];
  unsigned len = 0;
  EVP_Digest(data.data(), data.size(), md, &len, EVP_sha256(), nullptr);
  return std::string((char *)md, len);
}
inline std::string sha256_hex(const std::string &data) { return hex(sha256_raw(data)); }
inline std::string hmac_sha256_hex(const std::string &key, const std::string &data) {
  unsigned char md[EVP_MAX_MD_SIZE];
  unsigned len = 0;
  if (!HMAC(EVP_sha256(), key.data(), (int)key.size(), (const unsigned char *)data.data(), data.size(), md, &len))
    return "";
  return hex(std::string((char *)md, len));
}

// ---------------------------------------------------------------- encoding
inline std::string url_encode(const std::string &s) {
  static const char *digits = "0123456789ABCDEF";
  std::string out;
  for (unsigned char c : s) {
    if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') out += (char)c;
    else { out += '%'; out += digits[c >> 4]; out += digits[c & 15]; }
  }
  return out;
}
inline std::string url_decode(const std::string &s) {
  std::string out;
  for (size_t i = 0; i < s.size(); i++) {
    if (s[i] == '%' && i + 2 < s.size() && isxdigit((unsigned char)s[i + 1]) && isxdigit((unsigned char)s[i + 2])) {
      out += (char)strtol(s.substr(i + 1, 2).c_str(), nullptr, 16);
      i += 2;
    } else if (s[i] == '+') out += ' ';
    else out += s[i];
  }
  return out;
}
inline std::string b64_encode(const std::string &in) {
  std::string out(4 * ((in.size() + 2) / 3) + 1, '\0');
  const int n = EVP_EncodeBlock((unsigned char *)out.data(), (const unsigned char *)in.data(), (int)in.size());
  out.resize(n > 0 ? (size_t)n : 0);
  return out;
}
inline std::string b64_decode(std::string in) {
  for (auto &c : in) { if (c == '-') c = '+'; else if (c == '_') c = '/'; }
  in.erase(std::remove_if(in.begin(), in.end(), [](unsigned char c) { return isspace(c); }), in.end());
  while (in.size() % 4) in += '=';
  std::string out(in.size() / 4 * 3 + 3, '\0');
  const int n = EVP_DecodeBlock((unsigned char *)out.data(), (const unsigned char *)in.data(), (int)in.size());
  if (n < 0) return "";
  size_t len = (size_t)n;
  if (!in.empty() && in.back() == '=') { --len; if (in.size() > 1 && in[in.size() - 2] == '=') --len; }
  out.resize(len);
  return out;
}
inline std::string shell_quote(const std::string &s) {
  std::string out = "'";
  for (char c : s) { if (c == '\'') out += "'\\''"; else out += c; }
  return out + "'";
}

// ------------------------------------------------------------------- files
inline bool read_file(const std::string &path, std::string &out, size_t cap = 256u << 20) {
  int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
  if (fd < 0) return false;
  out.clear();
  char buf[65536];
  ssize_t n;
  while ((n = read(fd, buf, sizeof buf)) > 0) {
    if (out.size() + (size_t)n > cap) { close(fd); return false; }
    out.append(buf, (size_t)n);
  }
  close(fd);
  return n == 0;
}
inline std::string slurp(const std::string &path) {
  std::string s;
  read_file(path, s);
  return s;
}
// Write-then-rename with fsync: a reader sees the old or the new file, and a
// crash never leaves a half-written state file.
inline bool write_atomic(const std::string &path, const std::string &data, mode_t mode = 0640) {
  const std::string tmp = path + "." + random_hex(4) + ".tmp";
  int fd = open(tmp.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, mode);
  if (fd < 0) return false;
  size_t off = 0;
  while (off < data.size()) {
    const ssize_t n = write(fd, data.data() + off, data.size() - off);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) { close(fd); unlink(tmp.c_str()); return false; }
    off += (size_t)n;
  }
  const bool ok = fsync(fd) == 0;
  close(fd);
  if (!ok || rename(tmp.c_str(), path.c_str()) != 0) { unlink(tmp.c_str()); return false; }
  return true;
}
inline bool write_json(const std::string &path, const Val &v, mode_t mode = 0640) {
  return write_atomic(path, mj::dump(v) + "\n", mode);
}
inline Val read_json(const std::string &path) { return jparse(slurp(path)); }
inline bool exists(const std::string &path) { return access(path.c_str(), F_OK) == 0; }
inline int64_t file_size(const std::string &path) {
  struct stat st{};
  return stat(path.c_str(), &st) == 0 ? (int64_t)st.st_size : -1;
}
// Safe single path component for stored files.
inline std::string safe_name(const std::string &name, size_t max = 80) {
  std::string base = name;
  const auto slash = base.find_last_of("/\\");
  if (slash != std::string::npos) base = base.substr(slash + 1);
  std::string out;
  for (unsigned char c : base) {
    if (isalnum(c) || c == '.' || c == '-' || c == '_') out += (char)c;
    else if (c >= 0x80) out += (char)c; // UTF-8 names stay readable
    else out += '_';
  }
  out = mj::utf8_cut(out, max);
  while (!out.empty() && out[0] == '.') out.erase(0, 1);
  return out.empty() ? "datei" : out;
}
// Does an image file end in the middle of its picture? A photo that was
// picked while the camera was still writing it, or cut off on its way, has
// no end: a JPEG must reach its end mark after the picture data, a PNG its
// IEND chunk, a WebP the size its header announces. Anything else, and any
// file this cannot follow, is not judged.
inline bool image_cut(const std::string &d) {
  const auto u = [&](size_t i) { return (unsigned char)d[i]; };
  if (d.size() >= 3 && u(0) == 0xFF && u(1) == 0xD8 && u(2) == 0xFF) {
    size_t i = 2;
    for (;;) {
      if (i + 2 > d.size()) return true;
      if (u(i) != 0xFF) return false;              // not a marker where one belongs: not for us to judge
      const unsigned marker = u(i + 1);
      if (marker == 0xFF) { ++i; continue; }       // padding
      if (marker == 0xD9) return false;            // the end mark
      if ((marker >= 0xD0 && marker <= 0xD7) || marker == 0x01) { i += 2; continue; }
      if (i + 4 > d.size()) return true;
      const size_t length = ((size_t)u(i + 2) << 8) | u(i + 3);
      if (length < 2) return false;
      i += 2 + length;
      if (marker != 0xDA) continue;
      // Picture data: it runs until a marker that is neither a stuffed FF nor a restart.
      for (;;) {
        const void *hit = i < d.size() ? memchr(d.data() + i, 0xFF, d.size() - i) : nullptr;
        if (!hit) return true;
        i = (size_t)((const char *)hit - d.data());
        if (i + 2 > d.size()) return true;
        const unsigned next = u(i + 1);
        if (next == 0x00 || (next >= 0xD0 && next <= 0xD7)) { i += 2; continue; }
        break;
      }
    }
  }
  if (d.size() >= 8 && d.compare(0, 8, "\x89PNG\r\n\x1a\n", 8) == 0) {
    for (size_t i = 8; i + 12 <= d.size();) {
      if (d.compare(i + 4, 4, "IEND") == 0) return false;
      i += 12 + (((size_t)u(i) << 24) | ((size_t)u(i + 1) << 16) | ((size_t)u(i + 2) << 8) | u(i + 3));
    }
    return true;
  }
  if (d.size() >= 12 && d.compare(0, 4, "RIFF") == 0 && d.compare(8, 4, "WEBP") == 0)
    return (((size_t)u(7) << 24) | ((size_t)u(6) << 16) | ((size_t)u(5) << 8) | u(4)) + 8 > d.size();
  return false;
}

inline std::string mime_for(const std::string &name) {
  static const std::map<std::string, std::string> types = {
      {"html", "text/html; charset=utf-8"}, {"htm", "text/html; charset=utf-8"},
      {"js", "text/javascript; charset=utf-8"}, {"mjs", "text/javascript; charset=utf-8"},
      {"css", "text/css; charset=utf-8"}, {"json", "application/json"},
      {"webmanifest", "application/manifest+json"}, {"svg", "image/svg+xml"},
      {"png", "image/png"}, {"jpg", "image/jpeg"}, {"jpeg", "image/jpeg"}, {"gif", "image/gif"},
      {"webp", "image/webp"}, {"avif", "image/avif"}, {"ico", "image/x-icon"},
      {"heic", "image/heic"}, {"woff2", "font/woff2"}, {"wasm", "application/wasm"},
      {"txt", "text/plain; charset=utf-8"}, {"md", "text/markdown; charset=utf-8"},
      {"log", "text/plain; charset=utf-8"}, {"csv", "text/csv; charset=utf-8"},
      {"pdf", "application/pdf"}, {"zip", "application/zip"}, {"mp3", "audio/mpeg"},
      {"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
      {"pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
      {"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
      {"odt", "application/vnd.oasis.opendocument.text"}, {"odp", "application/vnd.oasis.opendocument.presentation"},
      {"ods", "application/vnd.oasis.opendocument.spreadsheet"}, {"rtf", "application/rtf"}, {"doc", "application/msword"},
      {"wav", "audio/wav"}, {"ogg", "audio/ogg"}, {"m4a", "audio/mp4"}, {"mp4", "video/mp4"},
      {"webm", "video/webm"}, {"mov", "video/quicktime"}, {"glb", "model/gltf-binary"},
      {"stl", "model/stl"}, {"ply", "application/octet-stream"}, {"gltf", "model/gltf+json"},
      {"geojson", "application/geo+json"}, {"xml", "application/xml"}, {"map", "application/json"},
      {"woff", "font/woff"}, {"ttf", "font/ttf"}, {"otf", "font/otf"}, {"obj", "text/plain; charset=utf-8"},
      {"mtl", "text/plain; charset=utf-8"}, {"glsl", "text/plain; charset=utf-8"}, {"bmp", "image/bmp"},
      {"tar", "application/x-tar"}, {"gz", "application/gzip"}};
  const auto dot = name.find_last_of('.');
  if (dot == std::string::npos) return "application/octet-stream";
  const auto it = types.find(lower(name.substr(dot + 1)));
  return it == types.end() ? "application/octet-stream" : it->second;
}

// ------------------------------------------------------------- subprocess
struct Cancel {
  std::atomic<bool> flag{false};
  void stop() { flag = true; }
  bool stopped() const { return flag.load(); }
};
using CancelPtr = std::shared_ptr<Cancel>;

struct RunOptions {
  int timeout_s = 120;
  size_t cap = 4u << 20;              // captured bytes; more is dropped, not fatal
  const Cancel *cancel = nullptr;
  const std::string *stdin_data = nullptr;
  std::function<void(const std::string &)> on_output; // every chunk as it arrives
};
struct RunResult {
  int code = -1;
  std::string out;
  bool timed_out = false, cancelled = false, truncated = false, spawned = false;
};

// Runs argv without a shell in its own process group; stdout and stderr share
// one pipe. Model-supplied data goes through an unlinked stdin file, never
// through argv. Cancel and timeout end the whole group (TERM, then KILL).
inline RunResult run(const std::vector<std::string> &args, const RunOptions &opt = {}) {
  RunResult r;
  if (args.empty()) return r;
  int p[2];
  if (pipe2(p, O_CLOEXEC)) return r;
  int input = -1;
  if (opt.stdin_data) {
    char name[] = "/tmp/mag-stdin-XXXXXX";
    input = mkstemp(name);
    if (input < 0) { close(p[0]); close(p[1]); return r; }
    unlink(name);
    size_t off = 0;
    while (off < opt.stdin_data->size()) {
      const ssize_t n = write(input, opt.stdin_data->data() + off, opt.stdin_data->size() - off);
      if (n < 0 && errno == EINTR) continue;
      if (n <= 0) break;
      off += (size_t)n;
    }
    lseek(input, 0, SEEK_SET);
  }
  posix_spawn_file_actions_t acts;
  posix_spawn_file_actions_init(&acts);
  posix_spawn_file_actions_adddup2(&acts, p[1], 1);
  posix_spawn_file_actions_adddup2(&acts, p[1], 2);
  if (input >= 0) posix_spawn_file_actions_adddup2(&acts, input, 0);
  else posix_spawn_file_actions_addopen(&acts, 0, "/dev/null", O_RDONLY, 0);
  posix_spawnattr_t attr;
  posix_spawnattr_init(&attr);
  posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
  posix_spawnattr_setpgroup(&attr, 0);
  std::vector<char *> argv;
  for (auto &a : args) argv.push_back(const_cast<char *>(a.c_str()));
  argv.push_back(nullptr);
  pid_t pid = 0;
  const int err = posix_spawnp(&pid, argv[0], &acts, &attr, argv.data(), environ);
  posix_spawn_file_actions_destroy(&acts);
  posix_spawnattr_destroy(&attr);
  if (input >= 0) close(input);
  close(p[1]);
  if (err) { close(p[0]); r.out = std::string("spawn failed: ") + strerror(err); return r; }
  r.spawned = true;
  fcntl(p[0], F_SETFL, O_NONBLOCK);
  int status = 0;
  bool exited = false, closed = false;
  const int64_t deadline = mono_ms() + (int64_t)opt.timeout_s * 1000;
  auto end_group = [&] {
    kill(-pid, SIGTERM);
    kill(pid, SIGTERM);
    for (int i = 0; i < 60 && !exited; i++) {
      if (waitpid(pid, &status, WNOHANG) == pid) exited = true; else usleep(50000);
    }
    if (!exited) { kill(-pid, SIGKILL); kill(pid, SIGKILL); waitpid(pid, &status, 0); exited = true; }
  };
  while (!exited || !closed) {
    char buf[16384];
    ssize_t n;
    while ((n = read(p[0], buf, sizeof buf)) > 0) {
      if (r.out.size() < opt.cap) r.out.append(buf, std::min((size_t)n, opt.cap - r.out.size()));
      else r.truncated = true;
      if (opt.on_output) opt.on_output(std::string(buf, (size_t)n));
    }
    if (n == 0) closed = true;
    if (!exited && waitpid(pid, &status, WNOHANG) == pid) exited = true;
    if (exited && closed) break;
    if (opt.cancel && opt.cancel->stopped()) { r.cancelled = true; end_group(); break; }
    if (mono_ms() > deadline) { r.timed_out = true; end_group(); break; }
    if (exited) { // children may hold the pipe open; give them a moment, then stop reading
      static const int grace_ms = 300;
      pollfd q{p[0], POLLIN, 0};
      if (poll(&q, 1, grace_ms) == 0) break;
      continue;
    }
    pollfd q{p[0], POLLIN, 0};
    poll(&q, 1, 40);
  }
  close(p[0]);
  r.code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
  return r;
}

} // namespace mag
