// Owner-controlled, tag-scoped Tailscale guest provisioning.
// Private state is kept in access.json; no auth-key plaintext is persisted.
#pragma once
#include <curl/curl.h>
#include <openssl/sha.h>
#include <openssl/rand.h>
#include <sys/stat.h>
#include <mutex>
#include <map>

static std::mutex GUEST_MTX;
static std::mutex GUEST_API_MTX;
static std::string GUEST_TOKEN;
static int64_t GUEST_TOKEN_UNTIL = 0;
static std::map<std::string, std::vector<int64_t>> GUEST_RATE;

static std::string guest_field(const Val &v, const char *name) {
  auto p = v.find(name);
  return p && p->type == Val::STR ? p->asStr() : "";
}
static int64_t guest_number(const Val &v, const char *name) {
  auto p = v.find(name);
  return p && p->type == Val::NUM ? (int64_t)p->asNum() : 0;
}
static bool guest_flag(const Val &v, const char *name) {
  auto p = v.find(name);
  return p && p->asBool();
}
static std::string guest_hash(const std::string &s) {
  unsigned char bytes[SHA256_DIGEST_LENGTH];
  SHA256(reinterpret_cast<const unsigned char *>(s.data()), s.size(), bytes);
  static const char hex[] = "0123456789abcdef";
  std::string result;
  for (unsigned char c : bytes) { result += hex[c >> 4]; result += hex[c & 15]; }
  return result;
}
static std::string guest_random() {
  unsigned char buf[32];
  if (RAND_bytes(buf, sizeof buf) != 1) return "";
  static const char *abc="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  std::string s;
  for (unsigned char b : buf) s.push_back(abc[b & 63]);
  return s;
}
static std::string guest_path() {
  return env("MAG_ROOM_ACCESS", STATE_DIR + "/access.json");
}
static bool guest_load(Val &root) {
  bool ok = false;
  std::string s = read_file(guest_path(), &ok);
  if (!ok || s.size() > 65536 || !mj::parse(s, root) || !root.isObj())
    return false;
  const Val *owners = root.find("owners");
  return owners && owners->isArr() && !owners->asArr().empty();
}
static bool guest_save(const Val &root) {
  // No secrets, but owner membership and device revocations are confidential.
  try { mag::write(guest_path(), mj::dump(root) + "\n"); return true; }
  catch (...) { return false; }
}
static Val *guest_records(Val &root) {
  auto p = root.find("guest_invites");
  if (!p) { root["guest_invites"] = Val::array(); p = root.find("guest_invites"); }
  return p && p->isArr() ? p : nullptr;
}
static bool guest_ident(const std::string &s) {
  return !s.empty() && s.size() < 128 &&
      std::all_of(s.begin(), s.end(), [](unsigned char c) {
        return std::isalnum(c) || c == '-' || c == '_';
      });
}
static void guest_audit(const std::string &action, const std::string &owner,
                        const std::string &id) {
  // All inputs are server-generated or validated. No OAuth material or raw key.
  std::string line = std::to_string(now_s()) + " " + action + " " + owner +
                     " " + id + "\n";
  std::string path = STATE_DIR + "/guest-audit.log";
  int f = open(path.c_str(), O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (f < 0) return;
  (void)::write(f, line.data(), line.size());
  (void)fsync(f); close(f);
}
static bool guest_limited(const std::string &actor) {
  auto &hits = GUEST_RATE[actor];
  int64_t now = now_s();
  hits.erase(std::remove_if(hits.begin(), hits.end(),
                           [now](int64_t n){return now-n>600;}), hits.end());
  if (hits.size() >= 6) return true;
  hits.push_back(now);
  return false;
}
static bool guest_live(const std::string &node) {
  if (!guest_ident(node)) return false;
  std::lock_guard<std::mutex> guard(GUEST_MTX);
  Val cfg;
  if (!guest_load(cfg)) return false;
  auto arr = cfg.find("guest_invites");
  if (!arr || !arr->isArr()) return false;
  for (const auto &item : arr->asArr())
    if (guest_field(item, "node") == node &&
        !guest_flag(item, "revoked") && guest_number(item, "expires") > now_s() &&
        guest_ident(guest_field(item, "device")))
      return true;
  return false;
}
static std::string guest_name_for_node(const std::string &node) {
  if (!guest_ident(node)) return "Gast";
  std::lock_guard<std::mutex> guard(GUEST_MTX);
  Val cfg;
  if (!guest_load(cfg)) return "Gast";
  auto arr = cfg.find("guest_invites");
  if (arr && arr->isArr()) for (const auto &r : arr->asArr())
    if (guest_field(r,"node") == node && !guest_flag(r,"revoked") &&
        guest_number(r,"expires") > now_s()) {
      auto name = clean_display_name(guest_field(r,"name"));
      return name.empty() ? "Gast" : name;
    }
  return "Gast";
}
static size_t guest_curl_write(char *p, size_t s, size_t n, void *ctx) {
  auto &out = *static_cast<std::string *>(ctx);
  size_t len = s*n;
  if (len > 1024*1024 || out.size() > 1024*1024 - len) return 0;
  out.append(p, len);
  return len;
}
struct GuestHTTP { long status = 0; Val json; bool ok = false; };
static GuestHTTP guest_http(const std::string &url, const std::string &method,
                            const std::string &auth, const std::string &payload = "",
                            bool basic = false) {
  GuestHTTP res;
  CURL *c = curl_easy_init();
  if (!c) return res;
  std::string data;
  curl_slist *headers = nullptr;
  headers = curl_slist_append(headers, "Accept: application/json");
  if (!basic) headers = curl_slist_append(headers, ("Authorization: Bearer " + auth).c_str());
  if (method == "POST") headers = curl_slist_append(headers, "Content-Type: application/json");
  curl_easy_setopt(c, CURLOPT_URL, url.c_str());
  curl_easy_setopt(c, CURLOPT_HTTPHEADER, headers);
  curl_easy_setopt(c, CURLOPT_PROTOCOLS_STR, "https");
  curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 0L);
  curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 4L);
  curl_easy_setopt(c, CURLOPT_TIMEOUT, 9L);
  curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, guest_curl_write);
  curl_easy_setopt(c, CURLOPT_WRITEDATA, &data);
  curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
  if (basic) curl_easy_setopt(c, CURLOPT_USERPWD, auth.c_str());
  if (method == "DELETE") curl_easy_setopt(c, CURLOPT_CUSTOMREQUEST, "DELETE");
  if (method == "POST") {
    curl_easy_setopt(c, CURLOPT_POST, 1L);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, payload.c_str());
    curl_easy_setopt(c, CURLOPT_POSTFIELDSIZE, (long)payload.size());
  }
  CURLcode rc = curl_easy_perform(c);
  curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &res.status);
  curl_slist_free_all(headers);
  curl_easy_cleanup(c);
  res.ok = rc == CURLE_OK;
  if (res.ok) mj::parse(data, res.json);
  return res;
}
static std::string guest_oauth() {
  if (!GUEST_TOKEN.empty() && now_s() < GUEST_TOKEN_UNTIL) return GUEST_TOKEN;
  auto dir = env("CREDENTIALS_DIRECTORY", "");
  if (dir.empty()) return "";
  // Never use the environment itself for long-lived OAuth secrets.
  bool ok = false;
  auto raw = read_file(dir + "/mag-guest-oauth", &ok);
  if (!ok || raw.size() > 4096) return "";
  Val cred;
  if (!mj::parse(raw, cred) || !cred.isObj()) return "";
  auto id = guest_field(cred, "client_id"), secret = guest_field(cred, "client_secret");
  if (id.empty() || secret.empty()) return "";
  std::string form = "grant_type=client_credentials&client_id=" +
                     url_encode(id) + "&client_secret=" + url_encode(secret);
  // For the OAuth token endpoint Tailscale accepts form-encoded client credentials.
  CURL *c = curl_easy_init();
  if (!c) return "";
  std::string data;
  curl_easy_setopt(c, CURLOPT_URL, "https://api.tailscale.com/api/v2/oauth/token");
  curl_easy_setopt(c, CURLOPT_POSTFIELDS, form.c_str());
  curl_easy_setopt(c, CURLOPT_POSTFIELDSIZE, (long)form.size());
  curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 4L);
  curl_easy_setopt(c, CURLOPT_TIMEOUT, 9L);
  curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, guest_curl_write);
  curl_easy_setopt(c, CURLOPT_WRITEDATA, &data);
  long status = 0;
  auto rc = curl_easy_perform(c);
  curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &status);
  curl_easy_cleanup(c);
  if (rc != CURLE_OK || status != 200) return "";
  Val v;
  if (!mj::parse(data, v)) return "";
  GUEST_TOKEN = guest_field(v, "access_token");
  if (GUEST_TOKEN.empty()) return "";
  GUEST_TOKEN_UNTIL = now_s() + std::clamp(guest_number(v, "expires_in") - 90, (int64_t)60, (int64_t)3000);
  return GUEST_TOKEN;
}
static GuestHTTP guest_api(const std::string &method, const std::string &path,
                           const std::string &payload = "") {
  std::lock_guard<std::mutex> gate(GUEST_API_MTX);
  std::string t = guest_oauth();
  if (t.empty()) return {};
  auto result = guest_http("https://api.tailscale.com/api/v2/" + path, method, t, payload);
  if (result.status == 401) GUEST_TOKEN.clear();
  return result;
}
static bool guest_tagged(const Val &device) {
  auto tags = device.find("tags");
  if (!tags || !tags->isArr()) return false;
  for (auto &t : tags->asArr()) if (t.asStr() == "tag:mag-guest") return true;
  return false;
}
// Called only after native whois has attested node ID/tag to nginx.
static std::string guest_device_for_ip(const Val &response, const std::string &ip) {
  auto arr = response.find("devices");
  if (!arr || !arr->isArr()) return "";
  for (const auto &dev : arr->asArr()) {
    if (!guest_tagged(dev)) continue;
    auto addresses = dev.find("addresses");
    if (!addresses || !addresses->isArr()) continue;
    for (auto &v : addresses->asArr())
      if (v.asStr() == ip) {
        auto id = guest_field(dev, "id");
        return guest_ident(id) ? id : "";
      }
  }
  return "";
}
static Val guest_list() {
  Val out = Val::object();
  Val visible = Val::array();
  Val cfg;
  if (guest_load(cfg)) {
    auto arr = cfg.find("guest_invites");
    if (arr && arr->isArr()) for (auto &r : arr->asArr()) {
      if (guest_flag(r,"revoked")) continue;
      Val v = Val::object();
      for (auto k : {"id", "name", "note", "created", "expires", "key_expires", "device", "node"}) {
        if (auto p = r.find(k)) v[k] = *p;
      }
      v["claimed"] = !guest_field(r, "device").empty();
      visible.push(v);
    }
  }
  out["guests"] = visible;
  out["configured"] = !env("CREDENTIALS_DIRECTORY", "").empty();
  return out;
}
static bool guest_revoke_remote(const Val &r) {
  bool done = true;
  auto key = guest_field(r,"key_id");
  if (guest_ident(key)) {
    auto resp = guest_api("DELETE", "tailnet/-/keys/" + key);
    done = done && resp.ok && (resp.status >= 200 && resp.status < 300 || resp.status == 404);
  }
  auto device = guest_field(r,"device");
  if (guest_ident(device)) {
    auto resp = guest_api("DELETE", "device/" + device);
    done = done && resp.ok && (resp.status >= 200 && resp.status < 300 || resp.status == 404);
  }
  return done;
}
// The registry denial happens first; if Tailscale is down, cleanup retries.
static bool guest_revoke(const std::string &id, const std::string &who) {
  Val target;
  {
    std::lock_guard<std::mutex> guard(GUEST_MTX);
    Val cfg;
    if (!guest_load(cfg)) throw std::runtime_error("Gastregister konnte nicht geladen werden");
    auto arr = guest_records(cfg);
    if (!arr) return false;
    bool found = false, newly_revoked = false;
    for (auto &r : *arr->arr) if (guest_field(r,"id") == id) {
      found = true; newly_revoked = !guest_flag(r,"revoked");
      r["revoked"] = true; target = r; break;
    }
    if (!found) return true;  // idempotent DELETE
    if (!guest_save(cfg)) throw std::runtime_error("Gastregister konnte nicht gesperrt werden");
    if (newly_revoked) guest_audit("revoke", who, id);
  }
  bool remote_done = guest_revoke_remote(target);
  if (remote_done) {
    std::lock_guard<std::mutex> guard(GUEST_MTX);
    Val cfg;
    if (guest_load(cfg)) {
      auto arr = guest_records(cfg);
      if (arr) {
        auto &v = *arr->arr;
        v.erase(std::remove_if(v.begin(),v.end(),[&](const Val &r) {
          return guest_field(r,"id") == id && guest_flag(r,"revoked");
        }),v.end());
        (void)guest_save(cfg);
      }
    }
  }
  return remote_done;
}
static void guest_cleanup() {
  std::vector<std::string> expired;
  {
    std::lock_guard<std::mutex> guard(GUEST_MTX);
    Val cfg;
    if (!guest_load(cfg)) return;
    auto arr = cfg.find("guest_invites");
    if (!arr || !arr->isArr()) return;
    for (const auto &r : arr->asArr()) {
      if (guest_flag(r,"revoked") || guest_number(r,"expires") <= now_s() ||
          (guest_field(r,"device").empty() && guest_number(r,"key_expires") <= now_s()))
        expired.push_back(guest_field(r,"id"));
    }
  }
  for (auto &id : expired) if (guest_ident(id)) {
    try { (void)guest_revoke(id, "scheduler"); }
    catch (...) { std::cerr << "Gastablauf: Register nicht beschreibbar\n"; }
  }
}
static void guest_cleanup_loop() {
  const int every = std::clamp(envi("MAG_ROOM_GUEST_CLEANUP_SECONDS", 60), 1, 3600);
  for (;;) { std::this_thread::sleep_for(std::chrono::seconds(every)); guest_cleanup(); }
}

// API results contain no keys beyond the new key in the immediate create
// response, which is discarded after returning the single owner response.
struct GuestIssue {
  int code = 503;
  Val body = Val::object();
};
static GuestIssue guest_issue(const Actor &owner, const Val &request) {
  GuestIssue out;
  std::string name = clean_display_name(guest_field(request, "name"));
  std::string note = mj::utf8_cut(guest_field(request,"note"), 160);
  for (char &ch : note) if ((unsigned char)ch < 32) ch = ' ';
  int64_t expires = guest_number(request, "expires_at");
  if (expires < now_s() + 3600 || expires > now_s() + 30LL*86400) {
    out.code = 400; out.body["error"] = "Ablauf muss 1 Stunde bis 30 Tage in der Zukunft liegen";
    return out;
  }
  std::lock_guard<std::mutex> guard(GUEST_MTX);
  Val cfg;
  if (!guest_load(cfg)) { out.body["error"] = "access.json nicht beschreibbar"; return out; }
  if (guest_limited(owner.login)) {
    out.code = 429; out.body["error"] = "Maximal sechs Einladungen in zehn Minuten"; return out;
  }
  auto arr = guest_records(cfg);
  if (!arr || arr->size() >= 100) {
    out.body["error"] = "Einladungslimit erreicht"; return out;
  }
  Val params = Val::object(), capabilities = Val::object(), dev = Val::object(), create = Val::object();
  create["reusable"] = false;
  create["ephemeral"] = false; // mobile devices should not vanish simply by going offline
  create["preauthorized"] = true;
  Val tags = Val::array(); tags.push("tag:mag-guest");
  create["tags"] = tags;
  dev["create"] = create;
  capabilities["devices"] = dev;
  params["capabilities"] = capabilities;
  params["expirySeconds"] = 86400;
  params["description"] = "MAG guest (" + (name.empty() ? "guest" : name) + ")";
  auto reply = guest_api("POST", "tailnet/-/keys", mj::dump(params));
  if (!reply.ok || (reply.status != 200 && reply.status != 201)) {
    out.body["error"] = "Tailscale-Einladung konnte nicht erstellt werden"; return out;
  }
  std::string key = guest_field(reply.json, "key");
  std::string key_id = guest_field(reply.json, "id");
  if (key.empty() || !guest_ident(key_id)) {
    out.body["error"] = "Tailscale lieferte keinen gültigen Auth-Key"; return out;
  }
  std::string code = guest_random();
  if (code.size() != 32) {
    (void)guest_api("DELETE", "tailnet/-/keys/" + key_id);
    out.body["error"] = "Zufallsquelle nicht verfügbar"; return out;
  }
  std::string id = guest_random().substr(0, 16);
  Val invite = Val::object();
  invite["id"] = id;
  invite["key_id"] = key_id;
  invite["claim_hash"] = guest_hash(code);
  invite["name"] = name;
  invite["note"] = note;
  invite["created"] = (long)now_s();
  invite["expires"] = (long)expires;
  invite["key_expires"] = (long)(now_s() + 86400);
  invite["device"] = "";
  invite["node"] = "";
  invite["revoked"] = false;
  arr->push(invite);
  if (!guest_save(cfg)) {
    // Never leak an orphaned invitation which cannot be revoked by the job.
    (void)guest_api("DELETE", "tailnet/-/keys/" + key_id);
    out.body["error"] = "Einladung konnte nicht sicher gespeichert werden"; return out;
  }
  guest_audit("invite", owner.login, id);
  out.code = 201;
  out.body["id"] = id;
  out.body["name"] = name;
  out.body["key"] = key; // deliberately single response, no server-side replay
  out.body["invite_url"] = "https://mag.stardenburg.de/#invite=" + code +
                           "&key=" + url_encode(key);
  out.body["expires"] = (long)expires;
  out.body["key_expires"] = (long)(now_s() + 86400);
  return out;
}
static GuestIssue guest_claim(const Actor &candidate, const Val &request) {
  GuestIssue out;
  out.code = 403;
  if (candidate.node.empty() || candidate.ip.empty()) {
    out.body["error"] = "Kein getaggtes Tailscale-Gerät"; return out;
  }
  std::string code = guest_field(request,"code");
  if (code.size() != 32 || code.find_first_not_of(
       "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") != std::string::npos) {
    out.body["error"] = "Ungültiger Einladungscode"; return out;
  }
  // Rate limit before calling Tailscale; no attacker-controlled API IDs.
  std::lock_guard<std::mutex> guard(GUEST_MTX);
  if (guest_limited("claim:" + candidate.node)) {
    out.code = 429; out.body["error"] = "Zu viele Versuche"; return out;
  }
  Val cfg;
  if (!guest_load(cfg)) {
    out.body["error"] = "Zugang derzeit nicht verfügbar"; return out;
  }
  auto arr = guest_records(cfg);
  if (!arr) return out;
  int idx = -1;
  for (size_t i = 0; i < arr->size(); ++i) {
    const Val &item = arr->asArr()[i];
    if (!guest_flag(item,"revoked") &&
        guest_field(item,"device").empty() &&
        guest_number(item,"expires") > now_s() &&
        guest_number(item,"key_expires") > now_s() &&
        ct_eq(guest_field(item,"claim_hash"), guest_hash(code))) { idx = (int)i; break; }
  }
  if (idx < 0) { out.body["error"] = "Einladung abgelaufen oder bereits verwendet"; return out; }
  auto devices = guest_api("GET","tailnet/-/devices");
  if (!devices.ok || devices.status != 200) {
    out.code = 503; out.body["error"] = "Geräteprüfung derzeit nicht erreichbar"; return out;
  }
  auto device = guest_device_for_ip(devices.json, candidate.ip);
  if (device.empty()) {
    out.body["error"] = "Tailscale-Gerät noch nicht synchronisiert"; return out;
  }
  for (const auto &item : arr->asArr())
    if (!guest_flag(item,"revoked") &&
        (guest_field(item,"node") == candidate.node ||
         guest_field(item,"device") == device)) {
      out.body["error"] = "Gerät ist bereits registriert"; return out;
    }
  auto &row = (*arr->arr)[(size_t)idx];
  row["node"] = candidate.node;
  row["device"] = device;
  row["claim_hash"] = ""; // one-time proof cannot be replayed
  if (!guest_save(cfg)) {out.code = 503; out.body["error"] = "Speichern fehlgeschlagen"; return out;}
  guest_audit("claim", candidate.login, guest_field(row, "id"));
  out.code = 200; out.body["ok"] = true;
  return out;
}
static Val guest_list_live() {
  Val out = guest_list();
  auto arr = out.find("guests");
  if (!arr || !arr->isArr()) return out;
  auto status = guest_api("GET", "tailnet/-/devices");
  if (!status.ok || status.status != 200) {
    out["live_status"] = "unavailable";
    return out;
  }
  out["live_status"] = "ok";
  auto devices = status.json.find("devices");
  if (!devices || !devices->isArr()) return out;
  for (auto &r : *arr->arr) {
    auto id = guest_field(r,"device");
    if (id.empty()) continue;
    for (auto &d : devices->asArr()) {
      if (guest_field(d,"id") != id || !guest_tagged(d)) continue;
      r["device_name"] = guest_field(d,"name");
      if (auto p=d.find("online")) r["online"] = *p;
      if (auto p=d.find("lastSeen")) r["last_seen"] = *p;
      break;
    }
  }
  return out;
}
