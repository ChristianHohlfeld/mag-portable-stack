// Tailscale identity and owner/guest permissions for the single room.
// Included once from magroom.cpp. Trusted identity comes only from loopback
// (nginx on 127.0.0.1) after it has stripped client-supplied headers and
// copied X-Tailscale-User / X-Tailscale-Name from the native whois endpoint.
// A non-loopback peer is denied even if those headers are present.
// Loopback without an identity header is not an owner. Tests opt in with
// MAG_TEST_ALLOW_LOCAL_OWNER=1. No secrets live here; the owner login is
// MAG_OWNER_CANONICAL_LOGIN (default chris.hohlfeld@gmail.com) and must
// also be listed in access.json.
#pragma once
#include <cctype>
#include <map>
#include <mutex>
#include <string>
#include <vector>

struct Actor {
  bool ok = false;
  std::string role;   // "owner" or "guest"
  std::string login;  // Tailscale LoginName, empty for the header-less local owner
  std::string name;   // display name stored on messages
  std::string node;   // attested Tailscale node ID (tagged devices)
  std::string ip;     // attested source Tailscale address
};

struct AccessCfg {
  bool ready = false;
  bool exists = false;
  bool broken = false;
  std::string path;
  time_t mtime = 0;
  long size = -1;
  std::vector<std::string> owners;          // lower-case Tailscale logins
  std::string owner_name;                   // optional display name for the local owner
  std::map<std::string, bool> guest_allow;  // permission -> guest may use it
};

static AccessCfg ACCESS_CFG;
static std::mutex ACCESS_MTX;

static const char *ACCESS_PERMS[] = {
    "offline", "stop",    "jobs",     "memory", "reset",
    "delete",  "autotalk", "connectors", "browser"};

static std::string clean_display_name(const std::string &in) {
  std::string o;
  for (unsigned char c : in) {
    if (c < 0x20 || c == 0x7f || c == '[' || c == ']')
      continue;
    o.push_back((char)c);
    if (o.size() >= 40)
      break;
  }
  while (!o.empty() && ((unsigned char)o.back() & 0xc0) == 0x80)
    o.pop_back();
  size_t a = o.find_first_not_of(' '), b = o.find_last_not_of(' ');
  if (a == std::string::npos)
    return "";
  return o.substr(a, b - a + 1);
}

static bool valid_login(const std::string &s) {
  if (s.size() < 1 || s.size() > 160)
    return false;
  for (unsigned char c : s)
    if (!(std::isalnum(c) || c == '.' || c == '_' || c == '@' || c == '+' ||
          c == '-'))
      return false;
  return true;
}

static bool is_loopback_ip(const std::string &ip) {
  // 127.0.0.0/8. The room binds 127.0.0.1; nginx and local tools arrive there.
  // Any other peer is the network and cannot be trusted, headers included.
  return ip.size() >= 4 && ip.compare(0, 4, "127.") == 0;
}

static std::string connection_peer(int fd) {
  sockaddr_in ca{};
  socklen_t cl = sizeof ca;
  if (getpeername(fd, (sockaddr *)&ca, &cl) != 0 || ca.sin_family != AF_INET)
    return "";
  char buf[INET_ADDRSTRLEN];
  if (!inet_ntop(AF_INET, &ca.sin_addr, buf, sizeof buf))
    return "";
  return buf;
}

static void parse_access(const std::string &raw, AccessCfg &cfg) {
  Val root;
  if (!mj::parse(raw, root) || !root.isObj()) {
    cfg.broken = true;
    std::cerr << "access.json: ungueltig, Header-Identitaeten sind Gaeste\n";
    return;
  }
  if (auto owners = root.find("owners"); owners && owners->isArr()) {
    for (const auto &x : owners->asArr()) {
      std::string login = lc(trim(x.asStr()));
      if (valid_login(login))
        cfg.owners.push_back(login);
    }
  }
  if (auto n = root.find("owner_name"))
    cfg.owner_name = clean_display_name(n->asStr());
  if (auto perms = root.find("permissions"); perms && perms->isObj()) {
    for (const char *k : ACCESS_PERMS) {
      auto v = perms->find(k);
      if (!v || !v->isArr())
        continue;
      for (const auto &role : v->asArr())
        if (lc(trim(role.asStr())) == "guest")
          cfg.guest_allow[k] = true;
    }
  }
}

static void ensure_access() {
  std::string path = env("MAG_ROOM_ACCESS", "");
  if (path.empty())
    path = STATE_DIR + "/access.json";
  struct stat st{};
  bool exists = stat(path.c_str(), &st) == 0;
  {
    std::lock_guard<std::mutex> lk(ACCESS_MTX);
    if (ACCESS_CFG.ready && ACCESS_CFG.path == path &&
        ACCESS_CFG.exists == exists &&
        (!exists || (ACCESS_CFG.mtime == st.st_mtime &&
                     ACCESS_CFG.size == (long)st.st_size)))
      return;
  }
  AccessCfg next;
  next.ready = true;
  next.path = path;
  next.exists = exists;
  if (exists) {
    next.mtime = st.st_mtime;
    next.size = (long)st.st_size;
    if (st.st_size > 65536) {
      next.broken = true;
      std::cerr << "access.json: zu gross, ignoriert\n";
    } else {
      bool ok = false;
      std::string raw = read_file(path, &ok);
      if (!ok)
        next.broken = true;
      else
        parse_access(raw, next);
    }
  }
  std::lock_guard<std::mutex> lk(ACCESS_MTX);
  ACCESS_CFG = std::move(next);
}

static AccessCfg access_snapshot() {
  ensure_access();
  std::lock_guard<std::mutex> lk(ACCESS_MTX);
  return ACCESS_CFG;
}

static bool login_is_owner(const AccessCfg &cfg, const std::string &login) {
  std::string key = lc(login);
  // Production owner is the explicitly approved Tailscale identity.
  // An extra name in access.json does not become owner.
  const std::string canonical = lc(env("MAG_OWNER_CANONICAL_LOGIN",
                                       "chris.hohlfeld@gmail.com"));
  if (key != canonical) return false;
  for (const auto &o : cfg.owners)
    if (o == key) return true;
  return false;
}

static bool actor_can(const Actor &a, const std::string &perm) {
  if (!a.ok)
    return false;
  if (a.role == "owner")
    return true;
  // Historic access.json flags cannot elevate a guest into admin/control.
  // Preserve only the deliberately configurable non-destructive offline toggle.
  if (a.role != "guest" || perm != "offline") return false;
  auto cfg = access_snapshot();
  auto it = cfg.guest_allow.find(perm);
  return it != cfg.guest_allow.end() && it->second;
}

static Val actor_json(const Actor &a) {
  Val o = Val::object();
  o["role"] = a.role;
  o["name"] = a.name;
  o["login"] = a.login;
  Val p = Val::object();
  for (const char *k : ACCESS_PERMS)
    p[k] = actor_can(a, k);
  o["permissions"] = p;
  return o;
}

// hdrs keys are already lower-case. Returns false when the request must be
// denied (non-loopback, or a loopback identity header that is not a login).
static bool identify_actor(const std::string &peer,
                           const std::map<std::string, std::string> &hdrs,
                           Actor &out) {
  out = Actor();
  const bool local = is_loopback_ip(peer);
  auto pick = [&](const char *key) -> std::string {
    auto it = hdrs.find(key);
    return it == hdrs.end() ? "" : trim(it->second);
  };
  // Client-supplied on a non-loopback socket is spoofing. Ignore the values.
  std::string login = pick("x-tailscale-user");
  if (login.empty())
    login = pick("x-tailscale-login");
  if (!local)
    return false;
  AccessCfg cfg = access_snapshot();
  const std::string node = pick("x-tailscale-node");
  const bool tagged = pick("x-tailscale-guest") == "1";
  if (tagged && !node.empty()) {
    if (node.size() > 20 || node.find_first_not_of("0123456789") != std::string::npos)
      return false;
    std::string ip = pick("x-tailscale-ip");
    in_addr addr{};
    if (inet_pton(AF_INET, ip.c_str(), &addr) != 1) return false;
    out.ok = true;
    out.role = "guest";
    out.node = node;
    out.ip = ip;
    out.login = "tag:mag-guest:" + node;
    out.name = "Gast";
    return true;
  }
  if (login.empty()) {
    out.ok = true;
    out.role = "owner";
    out.name = cfg.owner_name.empty() ? "Nutzer" : cfg.owner_name;
    return true;
  }
  login = url_decode(login);
  if (!valid_login(login))
    return false;
  std::string name = url_decode(pick("x-tailscale-name"));
  name = clean_display_name(name);
  if (name.empty()) {
    auto at = login.find('@');
    name = clean_display_name(at == std::string::npos ? login
                                                      : login.substr(0, at));
  }
  if (name.empty())
    name = "Gast";
  out.ok = true;
  out.login = lc(login);
  out.name = name;
  out.role = login_is_owner(cfg, login) ? "owner" : "guest";
  return true;
}

// Central, fail-closed permission contract. Unknown methods/paths must NOT
// silently grant any guest mutation. "owner" is an unconditional owner gate:
// entries in access.json cannot promote guests to this permission.
static bool public_guest_read(const std::string &method, const std::string &p) {
  if (method != "GET" && method != "HEAD") return false;
  return p == "/" || p == "/index.html" || p == "/sw.js" ||
         p == "/manifest.webmanifest" || p == "/favicon.ico" ||
         p == "/favicon.svg" || p == "/api/state" || p == "/api/events" ||
         p == "/api/version" || p == "/api/model-status" ||
         p == "/api/bots" || p == "/api/room" || p == "/api/files" ||
         p.rfind("/static/", 0) == 0 || p.rfind("/api/file/", 0) == 0;
}
static bool guest_reaction_path(const std::string &p) {
  const std::string root = "/api/messages/", end = "/reactions";
  return p.rfind(root, 0) == 0 && p.size() > root.size() + end.size() &&
         p.compare(p.size() - end.size(), end.size(), end) == 0;
}
static std::string required_perm(const std::string &method,
                                 const std::string &path) {
  // Local health probes stay available to deploy/systemd. They grant no
  // room, admin or backup data.
  if (method == "GET" && (path == "/healthz" || path == "/readyz" ||
                          path == "/readyz/model"))
    return "read";
  if (public_guest_read(method, path)) return "read";
  if ((method == "POST" && (path == "/api/message" || path == "/api/stream" ||
        path == "/api/upload" || path == "/api/stt" || path == "/api/speak")) ||
      ((method == "POST" || method == "DELETE") && guest_reaction_path(path)))
    return "chat";
  if (path == "/api/offline" && method == "POST") return "offline";
  if (path == "/api/queue" && method == "GET") return "read";
  if (path.rfind("/api/queue/", 0) == 0 && path.size() > 11 &&
      path.find('/', 11) == std::string::npos &&
      path != "/api/queue/merge" && path != "/api/queue/resume" &&
      (method == "PUT" || method == "DELETE")) return "queue-own";
  // Never let a config-based guest permission accidentally authorize one
  // of the destructive operations (or future routes).
  if (path == "/api/room/clear" || path == "/api/reset" ||
      path == "/api/room/snapshots" ||
      path.rfind("/api/room/snapshots/", 0) == 0 ||
      path.rfind("/api/room/files/", 0) == 0 ||
      path.rfind("/api/room/messages/", 0) == 0 ||
      path.rfind("/api/undo/", 0) == 0 ||
      path == "/api/guests" || path.rfind("/api/guests/", 0) == 0 ||
      path == "/api/frontend" || path == "/api/agents" ||
      path.rfind("/api/agents/", 0) == 0 ||
      path.rfind("/api/agent/", 0) == 0 ||
      path == "/mcp/jobs" || path.rfind("/api/jobs", 0) == 0 ||
      path.rfind("/api/pages", 0) == 0 ||
      path.rfind("/api/model", 0) == 0 ||
      path.rfind("/api/queue", 0) == 0 ||
      path == "/api/cancel" || path == "/api/stop" ||
      path == "/api/offline" || path == "/api/autotalk" ||
      path == "/api/sampling" ||
      path.rfind("/api/connectors", 0) == 0 ||
      path.rfind("/api/plugins", 0) == 0 ||
      path.rfind("/api/browser", 0) == 0 ||
      path.rfind("/api/push", 0) == 0 ||
      path.rfind("/api/screen/", 0) == 0 ||
      path.rfind("/bot/", 0) == 0)
    return "owner";
  // Some routes are deliberately accessible to the owner for internal
  // lifecycle/readiness calls; no guest access on any new/unknown route.
  return "owner";
}
static bool permit_request(const std::string &method, const std::string &path,
                           const Actor &a) {
  if (!a.ok) return false;
  std::string required = required_perm(method, path);
  if (a.role == "owner") {
    // No bare 127.0.0.1 admin shortcut. Owner routes need a nonempty login
    // (and the WhoIs proof checked by the router) unless a unit test opts in.
    if (required == "owner" && a.login.empty() &&
        env("MAG_TEST_ALLOW_LOCAL_OWNER", "0") != "1") return false;
    return true;
  }
  return required == "read" || required == "chat" ||
         required == "queue-own" ||
         (required == "offline" && actor_can(a,"offline"));
}
