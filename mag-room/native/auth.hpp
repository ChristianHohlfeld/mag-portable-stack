// Who is asking. nginx authenticates every request with an auth_request to
// /_internal/tailscale/whois; the room asks tailscaled (LocalAPI socket) who
// owns the client address and answers with identity headers plus an HMAC
// proof, which nginx copies onto the proxied request. The room accepts an
// identity only together with that proof, so headers alone prove nothing.
//
// One user: the owner logins listed in access.json. Everybody else is denied.
// A direct loopback request without identity headers (local tools, the
// recovery port) is "local": it may read and chat, nothing destructive.
#pragma once
#include "http.hpp"
#include "room.hpp"

namespace mag::auth {

struct Actor {
  bool ok = false;
  std::string role, login, name; // role: owner | local
  bool owner() const { return ok && role == "owner"; }
};

inline bool valid_login(const std::string &s) {
  if (s.empty() || s.size() > 160) return false;
  for (unsigned char c : s)
    if (!(isalnum(c) || c == '.' || c == '_' || c == '@' || c == '+' || c == '-')) return false;
  return true;
}
inline bool is_loopback(const std::string &ip) { return starts_with(ip, "127."); }
inline std::string clean_name(const std::string &in) {
  std::string out;
  for (unsigned char c : in) {
    if (c < 0x20 || c == 0x7f || c == '[' || c == ']') continue;
    out += (char)c;
  }
  return trim(mj::utf8_cut(out, 40));
}

class Gate {
public:
  explicit Gate(const Config &cfg) : cfg_(cfg), secret_(random_bytes(32)), ui_token_(random_token(43)) {}

  const std::string &ui_token() const { return ui_token_; }

  // ---- nginx auth_request -------------------------------------------------
  void whois(int fd, const http::Request &r) {
    auto deny = [&](const char *why) { http::send(fd, 403, "text/plain; charset=utf-8", why); };
    if (!is_loopback(r.peer)) return deny("forbidden\n");
    if (r.method != "GET" && r.method != "HEAD") return http::send(fd, 405, "text/plain; charset=utf-8", "method not allowed\n");
    std::string remote;
    if (!canonical_remote(r.header("x-mag-remote"), remote)) return deny("missing or invalid socket address\n");
    http::ClientOptions opt;
    opt.unix_socket = env("MAG_TAILSCALED_SOCKET", "/var/run/tailscale/tailscaled.sock");
    opt.timeout_s = 8;
    opt.connect_timeout_s = 3;
    opt.headers.push_back({"Host", "local-tailscaled.sock"});
    opt.cap = 1 << 20;
    const auto res = http::fetch("http://local-tailscaled.sock/localapi/v0/whois?addr=" + url_encode(remote), opt);
    const Val who = jparse(res.body);
    if (res.status != 200 || !who.isObj()) return deny("tailscale identity unavailable\n");
    const std::string ip = remote.substr(0, remote.rfind(':'));
    // Tagged devices (former guest invitations) have no user: no access.
    const Val *user = who.find("UserProfile");
    const std::string login = user ? trim(jstr(*user, "LoginName")) : "";
    if (!valid_login(login)) return deny("missing authenticated Tailscale identity\n");
    std::string name = user ? clean_name(jstr(*user, "DisplayName")) : "";
    if (name.empty()) name = clean_name(login.substr(0, login.find('@')));
    if (name.empty()) name = login;
    http::send(fd, 200, "text/plain; charset=utf-8", "",
               {{"X-Tailscale-User", login},
                {"X-Tailscale-Login", login},
                {"X-Tailscale-Name", url_encode(name)},
                {"X-Tailscale-IP", ip},
                {"X-Mag-Auth-Proof", proof(login, ip, now_s() / 60)}});
  }

  // ---- every other request ------------------------------------------------
  Actor identify(const http::Request &r) {
    Actor a;
    if (!is_loopback(r.peer)) return a;
    const std::string login = url_decode(r.header("x-tailscale-user"));
    if (login.empty() && r.header("x-tailscale-login").empty() && r.header("x-tailscale-guest").empty()) {
      a.ok = true;
      a.role = env("MAG_TEST_ALLOW_LOCAL_OWNER") == "1" ? "owner" : "local";
      a.name = "Chris";
      return a;
    }
    if (!valid_login(login) || !r.header("x-tailscale-guest").empty()) return a;
    const std::string ip = r.header("x-tailscale-ip"), given = r.header("x-mag-auth-proof");
    in_addr parsed{};
    if (given.size() != 64 || inet_pton(AF_INET, ip.c_str(), &parsed) != 1) return a;
    const int64_t minute = now_s() / 60;
    if (!ct_equal(given, proof(login, ip, minute)) && !ct_equal(given, proof(login, ip, minute - 1))) return a;
    if (!is_owner(login)) return a;
    a.ok = true;
    a.role = "owner";
    a.login = login;
    a.name = clean_name(url_decode(r.header("x-tailscale-name")));
    if (a.name.empty()) a.name = "Chris";
    return a;
  }

  // Cross-site request forgery: a state-changing request must come from the
  // page itself (its token, or the browser's same-origin marker), and an
  // Origin header must name this host.
  bool same_site(const http::Request &r) const {
    const std::string origin = r.header("origin"), host = r.header("host");
    if (!origin.empty() && !host.empty() && origin != "https://" + host && origin != "http://" + host) return false;
    if (r.method == "GET" || r.method == "HEAD" || r.method == "OPTIONS") return true;
    return ct_equal(r.header("x-mag-token"), ui_token_) || r.header("sec-fetch-site") == "same-origin";
  }

private:
  const Config &cfg_;
  const std::string secret_, ui_token_;
  std::mutex mu_;
  std::vector<std::string> owners_;
  int64_t owners_mtime_ = -1;

  std::string proof(const std::string &login, const std::string &ip, int64_t minute) const {
    return hmac_sha256_hex(secret_, login + "\n\n" + ip + "\n\n" + std::to_string(minute));
  }

  static bool canonical_remote(const std::string &raw, std::string &out) {
    const size_t colon = raw.rfind(':');
    if (colon == std::string::npos || raw.size() > 64) return false;
    const std::string ip = raw.substr(0, colon), port = raw.substr(colon + 1);
    if (port.empty() || port.size() > 5 || port.find_first_not_of("0123456789") != std::string::npos) return false;
    const long p = atol(port.c_str());
    in_addr a{};
    char canon[INET_ADDRSTRLEN] = {0};
    if (p < 1 || p > 65535 || inet_pton(AF_INET, ip.c_str(), &a) != 1 || !inet_ntop(AF_INET, &a, canon, sizeof canon) || ip != canon)
      return false;
    out = ip + ":" + std::to_string(p);
    return true;
  }

  bool is_owner(const std::string &login) {
    struct stat st{};
    const bool have = stat(cfg_.access_file.c_str(), &st) == 0;
    std::lock_guard<std::mutex> lk(mu_);
    if (!have) { owners_.clear(); owners_mtime_ = -1; return false; }
    if (st.st_mtime != owners_mtime_) {
      owners_.clear();
      owners_mtime_ = st.st_mtime;
      if (st.st_size <= 65536) {
        const Val root = read_json(cfg_.access_file);
        if (const Val *list = root.find("owners"); list && list->isArr())
          for (auto &o : list->asArr()) {
            const std::string l = lower(trim(o.asStr()));
            if (valid_login(l)) owners_.push_back(l);
          }
      }
    }
    return std::find(owners_.begin(), owners_.end(), lower(login)) != owners_.end();
  }
};

} // namespace mag::auth
