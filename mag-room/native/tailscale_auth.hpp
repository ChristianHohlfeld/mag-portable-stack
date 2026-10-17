// Native Tailscale LocalAPI identity for nginx auth_request.
// Compiled into magroom; no helper daemon, subprocess, token or TCP listener.
// The calling nginx subrequest supplies the real client socket address.
#pragma once
#include <sys/un.h>
#include <cerrno>
#include <climits>
#include <openssl/hmac.h>
#include <openssl/rand.h>

static bool ts_remote(const std::string &raw, std::string &normalized) {
  // This nginx vhost listens on IPv4 only. No whitespace, DNS or URL escapes.
  auto colon = raw.rfind(':');
  if (colon == std::string::npos || raw.size() > 64) return false;
  std::string ip = raw.substr(0, colon), port = raw.substr(colon + 1);
  if (port.empty() || port.size() > 5 ||
      !std::all_of(port.begin(), port.end(), [](unsigned char c) { return c >= '0' && c <= '9'; }))
    return false;
  unsigned long p = std::strtoul(port.c_str(), nullptr, 10);
  if (p < 1 || p > 65535) return false;
  in_addr addr{};
  char canonical[INET_ADDRSTRLEN]{};
  if (inet_pton(AF_INET, ip.c_str(), &addr) != 1 ||
      !inet_ntop(AF_INET, &addr, canonical, sizeof canonical) ||
      ip != canonical) return false;
  normalized = ip + ":" + std::to_string(p);
  return true;
}

static bool ts_http_body(const std::string &wire, std::string &body) {
  auto sep = wire.find("\r\n\r\n");
  if (sep == std::string::npos || sep > 32768) return false;
  std::istringstream status(wire.substr(0, wire.find("\r\n")));
  std::string version;
  int code = 0;
  status >> version >> code;
  if ((version != "HTTP/1.1" && version != "HTTP/1.0") || code != 200)
    return false;
  bool chunked = false, has_length = false;
  size_t length = 0, start = wire.find("\r\n") + 2;
  while (start < sep) {
    auto end = wire.find("\r\n", start);
    if (end == std::string::npos || end > sep) return false;
    auto line = wire.substr(start, end - start);
    auto pos = line.find(':');
    if (pos == std::string::npos) return false;
    auto key = lc(trim(line.substr(0, pos)));
    auto value = lc(trim(line.substr(pos + 1)));
    if (key == "transfer-encoding") {
      if (value != "chunked") return false;
      chunked = true;
    } else if (key == "content-length") {
      if (value.empty() || value.size() > 7 ||
          !std::all_of(value.begin(), value.end(), [](unsigned char c) { return c >= '0' && c <= '9'; }))
        return false;
      has_length = true;
      length = (size_t)std::strtoul(value.c_str(), nullptr, 10);
    }
    start = end + 2;
  }
  std::string data = wire.substr(sep + 4);
  if (chunked) {
    if (has_length) return false;
    size_t offset = 0;
    while (true) {
      auto end = data.find("\r\n", offset);
      if (end == std::string::npos || end - offset > 16) return false;
      auto digits = data.substr(offset, end - offset);
      if (digits.empty() || !std::all_of(digits.begin(), digits.end(), [](unsigned char c) {
            return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
                   (c >= 'A' && c <= 'F');
          })) return false;
      size_t n = 0;
      try { n = std::stoul(digits, nullptr, 16); }
      catch (...) { return false; }
      offset = end + 2;
      if (n == 0) return offset + 2 <= data.size() && data.compare(offset, 2, "\r\n") == 0;
      if (n > 1024 * 1024 - body.size() || data.size() - offset < n + 2 ||
          data.compare(offset + n, 2, "\r\n") != 0) return false;
      body.append(data, offset, n);
      offset += n + 2;
    }
  }
  if (has_length && length != data.size()) return false;
  if (data.size() > 1024 * 1024) return false;
  body = std::move(data);
  return true;
}

static bool ts_lookup(const std::string &remote, mj::Val &profile) {
  const std::string path = env("MAG_TAILSCALED_SOCKET", "/var/run/tailscale/tailscaled.sock");
  if (path.empty() || path.size() >= sizeof(sockaddr_un::sun_path)) return false;
  int sock = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (sock < 0) return false;
  timeval tv{};
  tv.tv_sec = 2;
  setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
  setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  std::memcpy(addr.sun_path, path.c_str(), path.size() + 1);
  bool ok = connect(sock, (sockaddr *)&addr, sizeof addr) == 0;
  const std::string request =
      "GET /localapi/v0/whois?addr=" + url_encode(remote) +
      " HTTP/1.1\r\nHost: local-tailscaled.sock\r\nConnection: close\r\n\r\n";
  if (ok) {
    size_t sent = 0;
    while (sent < request.size()) {
      ssize_t n = send(sock, request.data() + sent, request.size() - sent, MSG_NOSIGNAL);
      if (n <= 0) { ok = false; break; }
      sent += (size_t)n;
    }
  }
  std::string response;
  char buf[8192];
  while (ok) {
    ssize_t n = recv(sock, buf, sizeof buf, 0);
    if (n == 0) break;
    if (n < 0 || response.size() + (size_t)n > 1024 * 1024 + 32768) {
      ok = false;
      break;
    }
    response.append(buf, (size_t)n);
  }
  close(sock);
  if (!ok) return false;
  std::string body;
  return ts_http_body(response, body) && mj::parse(body, profile) && profile.isObj();
}


// A local process may spoof identity headers. An HMAC from the native whois
// subrequest proves the identity was actually attested via tailscaled; nginx
// copies the proof only from that internal subrequest, never from the client.
// The key is generated only in RAM and never exposed through page HTML.
static const std::string &ts_proof_secret() {
  static const std::string key = [] {
    unsigned char bytes[32];
    if (RAND_bytes(bytes, sizeof bytes) != 1) std::abort();
    return std::string((char *)bytes, sizeof bytes);
  }();
  return key;
}
static std::string ts_proof(const std::string &user,
                            const std::string &node,
                            const std::string &ip,
                            const std::string &tagged, int64_t epoch_min) {
  std::string data = user + "\n" + node + "\n" + ip + "\n" + tagged +
                     "\n" + std::to_string(epoch_min);
  unsigned char digest[EVP_MAX_MD_SIZE];
  unsigned n = 0;
  if (!HMAC(EVP_sha256(), ts_proof_secret().data(),
            (int)ts_proof_secret().size(),
            (const unsigned char *)data.data(), data.size(), digest, &n))
    return "";
  static const char *hex = "0123456789abcdef";
  std::string out;
  for (unsigned i=0;i<n;++i) {out += hex[digest[i] >> 4]; out += hex[digest[i] & 15];}
  return out;
}
static bool ts_proof_valid(const std::map<std::string,std::string> &h) {
  auto get = [&](const char *name) -> std::string {
    auto it = h.find(name); return it == h.end() ? "" : it->second;
  };
  std::string proof = get("x-mag-auth-proof");
  if (proof.size() != 64) return false;
  std::string user = get("x-tailscale-user"), node = get("x-tailscale-node"),
              ip = get("x-tailscale-ip"), tag = get("x-tailscale-guest");
  in_addr addr{};
  if (inet_pton(AF_INET,ip.c_str(),&addr) != 1) return false;
  if ((!user.empty() && !valid_login(user)) ||
      (!node.empty() && (node.size()>20 || node.find_first_not_of("0123456789")!=std::string::npos)))
    return false;
  if (!(tag.empty() || tag == "1")) return false;
  const int64_t minute = now_s()/60;
  return ct_eq(proof,ts_proof(user,node,ip,tag,minute)) ||
         ct_eq(proof,ts_proof(user,node,ip,tag,minute-1));
}

static void native_tailscale_auth(int fd, const Req &r) {
  // Only nginx on loopback may call this. Missing/invalid identity NEVER
  // becomes the implicit local owner used by ordinary direct loopback calls.
  if (!is_loopback_ip(connection_peer(fd))) {
    send_text(fd, 403, "forbidden\n");
    return;
  }
  if (r.method != "GET" && r.method != "HEAD") {
    send_text(fd, 405, "method not allowed\n");
    return;
  }
  std::string remote, raw;
  if (auto it = r.hdrs.find("x-mag-remote"); it != r.hdrs.end()) raw = it->second;
  if (!ts_remote(raw, remote)) {
    send_text(fd, 403, "missing or invalid socket address\n");
    return;
  }
  Val obj;
  if (!ts_lookup(remote, obj)) {
    send_text(fd, 403, "tailscale identity unavailable\n");
    return;
  }
  // A tagged device has a Node identity and tags, not a UserProfile.
  auto node = obj.find("Node");
  std::string node_id, remote_ip = remote.substr(0, remote.rfind(':'));
  bool tagged = false;
  if (node && node->isObj()) {
    auto id = node->find("ID");
    if (id && id->type == Val::NUM && id->asNum() > 0 &&
        id->asNum() < 9007199254740991.0)
      node_id = std::to_string((uint64_t)id->asNum());
    auto tags = node->find("Tags");
    if (tags && tags->isArr())
      for (const auto &tag : tags->asArr())
        if (tag.asStr() == "tag:mag-guest") tagged = true;
  }
  if (tagged && !node_id.empty()) {
    send_response(fd, 200, "text/plain; charset=utf-8", "",
                  {{"X-Tailscale-Node", node_id},
                   {"X-Tailscale-IP", remote_ip},
                   {"X-Tailscale-Guest", "1"},
                   {"X-Mag-Auth-Proof", ts_proof("",node_id,remote_ip,"1",now_s()/60)}});
    return;
  }
  auto user = obj.find("UserProfile");
  if (!user || !user->isObj()) {
    send_text(fd, 403, "missing authenticated Tailscale identity\n");
    return;
  }
  auto field = [&](const char *key) -> std::string {
    auto p = user->find(key);
    return p && p->type == Val::STR ? trim(p->asStr()) : "";
  };
  const std::string login = field("LoginName");
  if (!valid_login(login)) {
    send_text(fd, 403, "invalid Tailscale login\n");
    return;
  }
  std::string name = clean_display_name(field("DisplayName"));
  if (name.empty()) name = clean_display_name(login.substr(0, login.find('@')));
  if (name.empty()) name = login;
  send_response(fd, 200, "text/plain; charset=utf-8", "",
                {{"X-Tailscale-User", login},
                 {"X-Tailscale-Login", login},
                 {"X-Tailscale-Name", url_encode(name)},
                 {"X-Tailscale-IP", remote_ip},
                 {"X-Mag-Auth-Proof", ts_proof(login,"",remote_ip,"",now_s()/60)}});
}
