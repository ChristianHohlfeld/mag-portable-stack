// HTTP for the room: a thread-per-connection HTTP/1.1 server (one request per
// connection, as nginx sends them), file responses with ranges, and a small
// libcurl client with streaming and cancellation for loopback services.
#pragma once
#include "util.hpp"

#include <arpa/inet.h>
#include <curl/curl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/sendfile.h>
#include <sys/socket.h>
#include <thread>

namespace mag::http {

using Headers = std::vector<std::pair<std::string, std::string>>;

struct Request {
  std::string method, path, query, body, peer;
  std::map<std::string, std::string> headers; // lower-case names
  bool bridge = false;                        // arrived on the VM bridge listener
  std::string header(const std::string &name) const {
    const auto it = headers.find(name);
    return it == headers.end() ? "" : it->second;
  }
  std::string param(const std::string &name) const {
    size_t at = 0;
    while (at < query.size()) {
      size_t amp = query.find('&', at);
      if (amp == std::string::npos) amp = query.size();
      const std::string pair = query.substr(at, amp - at);
      const size_t eq = pair.find('=');
      if (pair.substr(0, eq) == name) return eq == std::string::npos ? "" : url_decode(pair.substr(eq + 1));
      at = amp + 1;
    }
    return "";
  }
};

inline bool write_all(int fd, const char *data, size_t size) {
  size_t off = 0;
  while (off < size) {
    const ssize_t n = ::send(fd, data + off, size - off, MSG_NOSIGNAL);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) return false;
    off += (size_t)n;
  }
  return true;
}
inline bool write_all(int fd, const std::string &s) { return write_all(fd, s.data(), s.size()); }
// Same for a regular file descriptor.
inline bool write_file_all(int fd, const char *data, size_t size) {
  size_t off = 0;
  while (off < size) {
    const ssize_t n = ::write(fd, data + off, size - off);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) return false;
    off += (size_t)n;
  }
  return true;
}

inline const char *reason(int code) {
  switch (code) {
  case 200: return "OK"; case 201: return "Created"; case 202: return "Accepted";
  case 204: return "No Content"; case 206: return "Partial Content";
  case 301: return "Moved Permanently"; case 302: return "Found"; case 304: return "Not Modified";
  case 400: return "Bad Request"; case 401: return "Unauthorized"; case 403: return "Forbidden";
  case 404: return "Not Found"; case 405: return "Method Not Allowed"; case 409: return "Conflict";
  case 410: return "Gone"; case 413: return "Payload Too Large"; case 415: return "Unsupported Media Type";
  case 416: return "Range Not Satisfiable"; case 422: return "Unprocessable Content";
  case 429: return "Too Many Requests"; case 500: return "Internal Server Error";
  case 502: return "Bad Gateway"; case 503: return "Service Unavailable"; case 504: return "Gateway Timeout";
  default: return "Status";
  }
}

inline std::string head(int code, const std::string &ctype, long long length, const Headers &extra) {
  std::string h = "HTTP/1.1 " + std::to_string(code) + " " + reason(code) + "\r\n";
  if (!ctype.empty()) h += "Content-Type: " + ctype + "\r\n";
  if (length >= 0) h += "Content-Length: " + std::to_string(length) + "\r\n";
  h += "X-Content-Type-Options: nosniff\r\n";
  bool cache = false;
  for (auto &kv : extra) {
    if (lower(kv.first) == "cache-control") cache = true;
    h += kv.first + ": " + kv.second + "\r\n";
  }
  if (!cache) h += "Cache-Control: no-store\r\n";
  h += "Connection: close\r\n\r\n";
  return h;
}

inline void send(int fd, int code, const std::string &ctype, const std::string &body, const Headers &extra = {}) {
  write_all(fd, head(code, ctype, (long long)body.size(), extra) + body);
}
inline void send_json(int fd, int code, const Val &v, const Headers &extra = {}) {
  send(fd, code, "application/json; charset=utf-8", mj::dump(v), extra);
}
inline void send_error(int fd, int code, const std::string &message) {
  Val v = Val::object();
  v["error"] = message;
  send_json(fd, code, v);
}
inline void send_ok(int fd) {
  Val v = Val::object();
  v["ok"] = true;
  send_json(fd, 200, v);
}

// Streams a file with single-range support; large files never enter memory.
inline void send_file(int fd, const Request &req, const std::string &path, const std::string &ctype,
                      const Headers &extra = {}) {
  const int file = open(path.c_str(), O_RDONLY | O_CLOEXEC);
  struct stat st{};
  if (file < 0 || fstat(file, &st) != 0 || !S_ISREG(st.st_mode)) {
    if (file >= 0) close(file);
    send_error(fd, 404, "Datei fehlt");
    return;
  }
  const long long total = st.st_size;
  long long from = 0, to = total - 1;
  int code = 200;
  Headers headers = extra;
  headers.push_back({"Accept-Ranges", "bytes"});
  const std::string range = req.header("range");
  if (starts_with(range, "bytes=") && range.find(',') == std::string::npos) {
    const std::string spec = range.substr(6);
    const size_t dash = spec.find('-');
    if (dash != std::string::npos) {
      const std::string a = spec.substr(0, dash), b = spec.substr(dash + 1);
      bool ok = true;
      if (a.empty() && !b.empty()) { from = std::max(0LL, total - atoll(b.c_str())); }
      else if (!a.empty()) { from = atoll(a.c_str()); if (!b.empty()) to = std::min(total - 1, atoll(b.c_str())); }
      else ok = false;
      if (!ok || from > to || from >= total) {
        close(file);
        send(fd, 416, "", "", {{"Content-Range", "bytes */" + std::to_string(total)}});
        return;
      }
      code = 206;
      headers.push_back({"Content-Range", "bytes " + std::to_string(from) + "-" + std::to_string(to) + "/" + std::to_string(total)});
    }
  }
  timeval tv{180, 0};
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
  if (write_all(fd, head(code, ctype, to - from + 1, headers)) && req.method != "HEAD") {
    off_t offset = from;
    long long left = to - from + 1;
    while (left > 0) {
      const ssize_t n = sendfile(fd, file, &offset, (size_t)std::min<long long>(left, 1 << 20));
      if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
      if (n <= 0) break;
      left -= n;
    }
  }
  close(file);
}

// ------------------------------------------------------------------ server
inline bool read_request(int fd, Request &r, size_t max_body) {
  std::string buf;
  char chunk[16384];
  size_t end;
  while ((end = buf.find("\r\n\r\n")) == std::string::npos) {
    if (buf.size() > 65536) return false;
    const ssize_t n = recv(fd, chunk, sizeof chunk, 0);
    if (n <= 0) return false;
    buf.append(chunk, (size_t)n);
  }
  std::istringstream lines(buf.substr(0, end));
  std::string line, target, version;
  if (!std::getline(lines, line)) return false;
  std::istringstream first(line);
  first >> r.method >> target >> version;
  if (r.method.empty() || target.empty() || target[0] != '/') return false;
  const size_t q = target.find('?');
  r.path = target.substr(0, q);
  r.query = q == std::string::npos ? "" : target.substr(q + 1);
  while (std::getline(lines, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    const size_t colon = line.find(':');
    if (colon == std::string::npos) continue;
    r.headers[lower(trim(line.substr(0, colon)))] = trim(line.substr(colon + 1));
  }
  if (r.headers.count("transfer-encoding")) return false;
  size_t length = 0;
  const std::string cl = r.header("content-length");
  if (!cl.empty()) {
    if (cl.find_first_not_of("0123456789") != std::string::npos || cl.size() > 12) return false;
    length = (size_t)strtoull(cl.c_str(), nullptr, 10);
  }
  if (length > max_body) return false;
  r.body = buf.substr(end + 4);
  if (r.body.size() > length) r.body.resize(length);
  r.body.reserve(length);
  while (r.body.size() < length) {
    const ssize_t n = recv(fd, chunk, std::min(sizeof chunk, length - r.body.size()), 0);
    if (n <= 0) return false;
    r.body.append(chunk, (size_t)n);
  }
  return true;
}

inline std::string peer_of(int fd) {
  sockaddr_in a{};
  socklen_t len = sizeof a;
  char buf[INET_ADDRSTRLEN] = {0};
  if (getpeername(fd, (sockaddr *)&a, &len) != 0 || a.sin_family != AF_INET) return "";
  inet_ntop(AF_INET, &a.sin_addr, buf, sizeof buf);
  return buf;
}

using Handler = std::function<void(int fd, Request &)>;

inline int listen_on(const std::string &host, int port) {
  const int fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0) return -1;
  int one = 1;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons((uint16_t)port);
  if (inet_pton(AF_INET, host.c_str(), &a.sin_addr) != 1 || bind(fd, (sockaddr *)&a, sizeof a) != 0 ||
      listen(fd, 256) != 0) {
    close(fd);
    return -1;
  }
  return fd;
}

// Accept loop; every connection gets its own detached thread. A request that
// cannot be parsed is answered with 400 and never reaches the handler.
inline void serve(int listener, bool bridge, const Handler &handler, std::atomic<bool> &stop,
                  size_t max_body = 80u << 20) {
  static std::atomic<int> open_connections{0};
  while (!stop) {
    pollfd p{listener, POLLIN, 0};
    if (poll(&p, 1, 500) <= 0) continue;
    const int fd = accept4(listener, nullptr, nullptr, SOCK_CLOEXEC);
    if (fd < 0) continue;
    if (open_connections.load() >= 400) {
      send_error(fd, 503, "Zu viele Verbindungen");
      close(fd);
      continue;
    }
    ++open_connections;
    std::thread([fd, bridge, handler, max_body] {
      int one = 1;
      setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
      timeval tv{30, 0};
      setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
      setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
      Request r;
      r.bridge = bridge;
      r.peer = peer_of(fd);
      try {
        if (read_request(fd, r, max_body)) handler(fd, r);
        else send_error(fd, 400, "Ungültige Anfrage");
      } catch (const std::exception &e) {
        logf(std::string("http: ") + r.method + " " + r.path + ": " + e.what());
        send_error(fd, 500, "Interner Fehler");
      } catch (...) {
        send_error(fd, 500, "Interner Fehler");
      }
      shutdown(fd, SHUT_RDWR);
      close(fd);
      --open_connections;
    }).detach();
  }
}

// ------------------------------------------------------------------ client
struct ClientOptions {
  std::string method = "GET", body, unix_socket;
  Headers headers;
  int timeout_s = 30, connect_timeout_s = 5;
  int stall_s = 0;                   // abort when no byte arrives for this long (0 = off)
  bool direct = true;                // bypass proxy environment (loopback, VM net)
  const Cancel *cancel = nullptr;
  size_t cap = 64u << 20;
  // Streaming consumer; when set the body is not collected. Return false to abort.
  std::function<bool(const char *, size_t)> on_data;
};
struct ClientResult {
  long status = 0;
  std::string body, error, content_type;
  bool cancelled = false;
  bool ok() const { return error.empty() && status >= 200 && status < 300; }
};

namespace detail {
struct Ctx {
  const ClientOptions *opt;
  ClientResult *res;
  bool aborted = false;
};
inline size_t on_write(char *data, size_t size, size_t n, void *user) {
  auto *c = static_cast<Ctx *>(user);
  const size_t len = size * n;
  if (c->opt->on_data) {
    if (!c->opt->on_data(data, len)) { c->aborted = true; return 0; }
  } else if (c->res->body.size() + len <= c->opt->cap) {
    c->res->body.append(data, len);
  } else {
    c->aborted = true;
    return 0;
  }
  return len;
}
inline int on_progress(void *user, curl_off_t, curl_off_t, curl_off_t, curl_off_t) {
  auto *c = static_cast<Ctx *>(user);
  return c->opt->cancel && c->opt->cancel->stopped() ? 1 : 0;
}
} // namespace detail

inline ClientResult fetch(const std::string &url, const ClientOptions &opt = {}) {
  ClientResult res;
  CURL *h = curl_easy_init();
  if (!h) { res.error = "curl"; return res; }
  detail::Ctx ctx{&opt, &res};
  curl_slist *list = nullptr;
  for (auto &kv : opt.headers) list = curl_slist_append(list, (kv.first + ": " + kv.second).c_str());
  list = curl_slist_append(list, "Expect:");
  curl_easy_setopt(h, CURLOPT_URL, url.c_str());
  curl_easy_setopt(h, CURLOPT_HTTPHEADER, list);
  curl_easy_setopt(h, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(h, CURLOPT_CONNECTTIMEOUT, (long)opt.connect_timeout_s);
  curl_easy_setopt(h, CURLOPT_TIMEOUT, (long)opt.timeout_s);
  curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, detail::on_write);
  curl_easy_setopt(h, CURLOPT_WRITEDATA, &ctx);
  curl_easy_setopt(h, CURLOPT_XFERINFOFUNCTION, detail::on_progress);
  curl_easy_setopt(h, CURLOPT_XFERINFODATA, &ctx);
  curl_easy_setopt(h, CURLOPT_NOPROGRESS, 0L);
  curl_easy_setopt(h, CURLOPT_FOLLOWLOCATION, 0L);
  curl_easy_setopt(h, CURLOPT_USERAGENT, "magroom");
  if (opt.direct) curl_easy_setopt(h, CURLOPT_NOPROXY, "*");
  else {
    // Outbound requests go through the audit web proxy of the unit
    // environment, which presents its own CA.
    const std::string ca = env("CURL_CA_BUNDLE", env("SSL_CERT_FILE"));
    if (!ca.empty()) curl_easy_setopt(h, CURLOPT_CAINFO, ca.c_str());
  }
  if (opt.stall_s > 0) {
    curl_easy_setopt(h, CURLOPT_LOW_SPEED_LIMIT, 1L);
    curl_easy_setopt(h, CURLOPT_LOW_SPEED_TIME, (long)opt.stall_s);
  }
  if (!opt.unix_socket.empty()) curl_easy_setopt(h, CURLOPT_UNIX_SOCKET_PATH, opt.unix_socket.c_str());
  if (opt.method == "POST" || opt.method == "PUT" || opt.method == "DELETE" || opt.method == "PATCH") {
    curl_easy_setopt(h, CURLOPT_CUSTOMREQUEST, opt.method.c_str());
    if (opt.method != "DELETE" || !opt.body.empty()) {
      curl_easy_setopt(h, CURLOPT_POSTFIELDS, opt.body.data());
      curl_easy_setopt(h, CURLOPT_POSTFIELDSIZE_LARGE, (curl_off_t)opt.body.size());
    }
  } else if (opt.method == "HEAD") {
    curl_easy_setopt(h, CURLOPT_NOBODY, 1L);
  }
  const CURLcode rc = curl_easy_perform(h);
  curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &res.status);
  char *ctype = nullptr;
  if (curl_easy_getinfo(h, CURLINFO_CONTENT_TYPE, &ctype) == CURLE_OK && ctype) res.content_type = ctype;
  if (rc != CURLE_OK) {
    if (opt.cancel && opt.cancel->stopped()) { res.cancelled = true; res.error = "abgebrochen"; }
    else if (ctx.aborted && opt.on_data) res.error = "";          // consumer ended the stream on purpose
    else res.error = curl_easy_strerror(rc);
  }
  curl_slist_free_all(list);
  curl_easy_cleanup(h);
  return res;
}

inline ClientResult post_json(const std::string &url, const Val &body, ClientOptions opt = {}) {
  opt.method = "POST";
  opt.body = mj::dump(body);
  opt.headers.push_back({"Content-Type", "application/json"});
  return fetch(url, opt);
}

} // namespace mag::http
