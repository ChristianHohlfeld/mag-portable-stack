// Web Push (RFC 8291 aes128gcm + VAPID ES256). Included once from magroom.cpp.
//
// notify() is the hook other room features use. A finished Job should call:
//   notify(PushNote("job", title, preview, "/?job=" + id, "job-" + id));
// There is no job-completion call site in this tree yet. Long bot runs and
// errors already call notify(). Keys come from the environment, never from git.
#pragma once
#include <openssl/bn.h>
#include <openssl/ec.h>
#include <openssl/ecdsa.h>
#include <openssl/evp.h>
#include <openssl/kdf.h>
#include <openssl/obj_mac.h>
#include <openssl/param_build.h>
#include <openssl/rand.h>

#include <cstdlib>
#include <cstring>
#include <ctime>
#include <map>
#include <mutex>
#include <thread>
#include <unistd.h>
#include <vector>

struct PushNote {
  std::string kind;   // "job", "run", or "error"
  std::string title;
  std::string body;
  std::string url;    // same-origin path opened when the notification is clicked
  std::string tag;
  PushNote() = default;
  PushNote(std::string kind, std::string title, std::string body,
           std::string url, std::string tag)
      : kind(std::move(kind)), title(std::move(title)), body(std::move(body)),
        url(std::move(url)), tag(std::move(tag)) {}
};

static std::mutex PUSH_MTX;
static std::mutex PUSH_RECENT_MTX;
static std::map<std::string, steady_clock::time_point> PUSH_RECENT;

static std::string push_b64url(const unsigned char *p, size_t n) {
  std::string s(4 * ((n + 2) / 3), '\0');
  int len = EVP_EncodeBlock(reinterpret_cast<unsigned char *>(&s[0]), p, (int)n);
  s.resize(len > 0 ? (size_t)len : 0);
  for (char &c : s) {
    if (c == '+')
      c = '-';
    else if (c == '/')
      c = '_';
  }
  while (!s.empty() && s.back() == '=')
    s.pop_back();
  return s;
}
static std::string push_b64url_dec(std::string s) {
  for (char &c : s) {
    if (c == '-')
      c = '+';
    else if (c == '_')
      c = '/';
  }
  while (s.size() % 4)
    s.push_back('=');
  if (s.empty())
    return {};
  std::string out(s.size(), '\0');
  int len = EVP_DecodeBlock(reinterpret_cast<unsigned char *>(&out[0]),
                            reinterpret_cast<const unsigned char *>(s.data()),
                            (int)s.size());
  if (len < 0)
    return {};
  int pad = 0;
  if (s.size() >= 1 && s.back() == '=')
    pad++;
  if (s.size() >= 2 && s[s.size() - 2] == '=')
    pad++;
  if (len < pad)
    return {};
  out.resize((size_t)len - (size_t)pad);
  return out;
}
static std::string push_collapse(std::string t) {
  std::string o;
  bool sp = false;
  for (unsigned char c : t) {
    if (c == '\n' || c == '\r' || c == '\t')
      c = ' ';
    if (c < 32)
      continue;
    if (c == ' ') {
      if (sp || o.empty())
        continue;
      sp = true;
      o.push_back(' ');
      continue;
    }
    sp = false;
    o.push_back((char)c);
    if (o.size() >= 180)
      break;
  }
  while (!o.empty() && o.back() == ' ')
    o.pop_back();
  return o;
}
static std::string push_safe_url(const std::string &url) {
  if (url.size() >= 1 && url[0] == '/' && (url.size() == 1 || url[1] != '/') &&
      url.size() <= 512) {
    for (unsigned char c : url)
      if (c <= 32 || c == '"' || c == '\\')
        return "/";
    return url;
  }
  return "/";
}
static bool push_good_endpoint(const std::string &e) {
  if (e.size() < 12 || e.size() > 2000 || e.rfind("https://", 0) != 0)
    return false;
  if (e.find('/', 8) == std::string::npos)
    return false;
  for (unsigned char c : e)
    if (c <= 32 || c == '"' || c == '\'' || c == '\\')
      return false;
  return true;
}
static std::string push_origin(const std::string &url) {
  auto p = url.find("://");
  if (p == std::string::npos)
    return {};
  auto slash = url.find('/', p + 3);
  return slash == std::string::npos ? url : url.substr(0, slash);
}

struct PushKeys {
  bool ok = false;
  std::string pub_b64;
  EVP_PKEY *pkey = nullptr;
};
static PushKeys &push_keys() {
  static PushKeys keys;
  static std::once_flag once;
  std::call_once(once, [] {
    std::string pub = push_b64url_dec(env("MAG_ROOM_VAPID_PUBLIC", ""));
    std::string priv = push_b64url_dec(env("MAG_ROOM_VAPID_PRIVATE", ""));
    if (pub.size() != 65 || (unsigned char)pub[0] != 0x04 || priv.size() != 32)
      return;
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
    EC_KEY *ec = EC_KEY_new_by_curve_name(NID_X9_62_prime256v1);
    BIGNUM *bn = BN_bin2bn(reinterpret_cast<const unsigned char *>(priv.data()),
                           32, nullptr);
    if (!ec || !bn || EC_KEY_set_private_key(ec, bn) != 1) {
      EC_KEY_free(ec);
      BN_free(bn);
      return;
    }
    const EC_GROUP *group = EC_KEY_get0_group(ec);
    EC_POINT *pt = EC_POINT_new(group);
    if (!pt || EC_POINT_mul(group, pt, bn, nullptr, nullptr, nullptr) != 1 ||
        EC_KEY_set_public_key(ec, pt) != 1) {
      EC_POINT_free(pt);
      EC_KEY_free(ec);
      BN_free(bn);
      return;
    }
    unsigned char derived[65];
    size_t dlen = EC_POINT_point2oct(group, pt, POINT_CONVERSION_UNCOMPRESSED,
                                     derived, sizeof derived, nullptr);
    EC_POINT_free(pt);
    BN_free(bn);
    if (dlen != 65 || memcmp(derived, pub.data(), 65) != 0) {
      EC_KEY_free(ec);
      std::cerr << "push: VAPID public key does not match the private key\n";
      return;
    }
    EVP_PKEY *pkey = EVP_PKEY_new();
    if (!pkey || EVP_PKEY_assign_EC_KEY(pkey, ec) != 1) {
      EVP_PKEY_free(pkey);
      EC_KEY_free(ec);
      return;
    }
    keys.pkey = pkey;
    keys.pub_b64 = push_b64url(derived, 65);
    keys.ok = true;
#pragma GCC diagnostic pop
  });
  return keys;
}
static bool push_configured() { return push_keys().ok; }
static std::string push_public_b64() { return push_keys().pub_b64; }
static std::string push_subject() {
  std::string s = env("MAG_ROOM_VAPID_SUBJECT", "mailto:mag@stardenburg.de");
  if (s.rfind("mailto:", 0) != 0 && s.rfind("https://", 0) != 0)
    s = "mailto:mag@stardenburg.de";
  if (s.size() > 200)
    s.resize(200);
  for (unsigned char c : s)
    if (c <= 32 || c == '"' || c == '\\')
      return "mailto:mag@stardenburg.de";
  return s;
}

static std::string push_file() {
  return STATE_DIR + "/push_subscriptions.json";
}
static Val push_load_locked() {
  bool ok = false;
  std::string t = read_file(push_file(), &ok);
  Val v;
  if (!ok || trim(t).empty() || !mj::parse(t, v) || !v.isArr())
    return Val::array();
  return v;
}
static void push_save_locked(const Val &subs) {
  write_file(push_file(), mj::dump(subs));
  chmod(push_file().c_str(), 0600);
}
static bool push_flag(const Val *types, const char *key) {
  if (!types || !types->isObj())
    return true;
  auto v = types->find(key);
  if (!v)
    return true;
  if (v->type == Val::BOOL)
    return v->asBool();
  return true;
}
static std::string push_subscribe_body(const std::string &raw, std::string &err,
                                       int &code) {
  Val body;
  if (raw.empty() || !mj::parse(raw, body) || !body.isObj()) {
    err = "Ungültige Anmeldung";
    code = 400;
    return {};
  }
  std::string endpoint = body.find("endpoint") ? trim(body.find("endpoint")->asStr()) : "";
  const Val *keys = body.find("keys");
  std::string p256 = keys && keys->find("p256dh") ? keys->find("p256dh")->asStr() : "";
  std::string auth = keys && keys->find("auth") ? keys->find("auth")->asStr() : "";
  if (!push_good_endpoint(endpoint)) {
    err = "Push-Endpunkt ungültig";
    code = 400;
    return {};
  }
  std::string pub = push_b64url_dec(p256), sec = push_b64url_dec(auth);
  if (pub.size() != 65 || (unsigned char)pub[0] != 0x04 || sec.size() != 16) {
    err = "Push-Schlüssel ungültig";
    code = 400;
    return {};
  }
  if (!push_configured()) {
    err = "Push ist auf diesem Server nicht eingerichtet";
    code = 503;
    return {};
  }
  const Val *types = body.find("types");
  bool job = push_flag(types, "job"), run = push_flag(types, "run"),
       error = push_flag(types, "error");
  std::lock_guard<std::mutex> lk(PUSH_MTX);
  Val subs = push_load_locked();
  bool found = false;
  for (Val &item : *subs.arr) {
    auto ep = item.find("endpoint");
    if (!ep || ep->asStr() != endpoint)
      continue;
    item["p256dh"] = p256;
    item["auth"] = auth;
    Val t = Val::object();
    t["job"] = job;
    t["run"] = run;
    t["error"] = error;
    item["types"] = t;
    found = true;
    break;
  }
  if (!found) {
    if (subs.asArr().size() >= 200) {
      err = "Zu viele Geräte";
      code = 400;
      return {};
    }
    Val item = Val::object();
    item["endpoint"] = endpoint;
    item["p256dh"] = p256;
    item["auth"] = auth;
    Val t = Val::object();
    t["job"] = job;
    t["run"] = run;
    t["error"] = error;
    item["types"] = t;
    subs.push(item);
  }
  push_save_locked(subs);
  return "{\"ok\":true}";
}
static void push_unsubscribe(const std::string &endpoint) {
  if (!push_good_endpoint(endpoint))
    return;
  std::lock_guard<std::mutex> lk(PUSH_MTX);
  Val subs = push_load_locked();
  Val next = Val::array();
  for (auto &item : subs.asArr()) {
    auto ep = item.find("endpoint");
    if (ep && ep->asStr() == endpoint)
      continue;
    next.push(item);
  }
  push_save_locked(next);
}
static bool push_set_prefs(const std::string &raw, std::string &err) {
  Val body;
  if (raw.empty() || !mj::parse(raw, body) || !body.isObj()) {
    err = "Ungültige Einstellung";
    return false;
  }
  std::string endpoint = body.find("endpoint") ? trim(body.find("endpoint")->asStr()) : "";
  if (!push_good_endpoint(endpoint)) {
    err = "Push-Endpunkt ungültig";
    return false;
  }
  const Val *types = body.find("types");
  std::lock_guard<std::mutex> lk(PUSH_MTX);
  Val subs = push_load_locked();
  bool found = false;
  for (Val &item : *subs.arr) {
    auto ep = item.find("endpoint");
    if (!ep || ep->asStr() != endpoint)
      continue;
    Val t = Val::object();
    t["job"] = push_flag(types, "job");
    t["run"] = push_flag(types, "run");
    t["error"] = push_flag(types, "error");
    item["types"] = t;
    found = true;
    break;
  }
  if (!found) {
    err = "Kein Gerät";
    return false;
  }
  push_save_locked(subs);
  return true;
}
static std::string push_subscription_json(const std::string &endpoint) {
  if (!push_good_endpoint(endpoint))
    return {};
  std::lock_guard<std::mutex> lk(PUSH_MTX);
  Val subs = push_load_locked();
  for (auto &item : subs.asArr()) {
    auto ep = item.find("endpoint");
    if (!ep || ep->asStr() != endpoint)
      continue;
    const Val *types = item.find("types");
    Val out = Val::object();
    out["subscribed"] = true;
    Val t = Val::object();
    t["job"] = push_flag(types, "job");
    t["run"] = push_flag(types, "run");
    t["error"] = push_flag(types, "error");
    out["types"] = t;
    return mj::dump(out);
  }
  return {};
}

static bool push_hkdf(const unsigned char *salt, size_t saltlen,
                      const unsigned char *ikm, size_t ikmlen,
                      const unsigned char *info, size_t infolen,
                      unsigned char *out, size_t outlen) {
  EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new_id(EVP_PKEY_HKDF, nullptr);
  if (!ctx)
    return false;
  size_t len = outlen;
  bool ok = EVP_PKEY_derive_init(ctx) == 1 &&
            EVP_PKEY_CTX_set_hkdf_md(ctx, EVP_sha256()) == 1 &&
            EVP_PKEY_CTX_set1_hkdf_salt(ctx, salt, (int)saltlen) == 1 &&
            EVP_PKEY_CTX_set1_hkdf_key(ctx, ikm, (int)ikmlen) == 1 &&
            EVP_PKEY_CTX_add1_hkdf_info(ctx, info, (int)infolen) == 1 &&
            EVP_PKEY_derive(ctx, out, &len) == 1 && len == outlen;
  EVP_PKEY_CTX_free(ctx);
  return ok;
}
static EVP_PKEY *push_peer(const unsigned char *pub) {
  OSSL_PARAM_BLD *bld = OSSL_PARAM_BLD_new();
  if (!bld)
    return nullptr;
  char group[] = "P-256";
  OSSL_PARAM_BLD_push_utf8_string(bld, "group", group, 0);
  OSSL_PARAM_BLD_push_octet_string(bld, "pub", pub, 65);
  OSSL_PARAM *params = OSSL_PARAM_BLD_to_param(bld);
  EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new_from_name(nullptr, "EC", nullptr);
  EVP_PKEY *key = nullptr;
  if (params && ctx && EVP_PKEY_fromdata_init(ctx) == 1)
    EVP_PKEY_fromdata(ctx, &key, EVP_PKEY_PUBLIC_KEY, params);
  EVP_PKEY_CTX_free(ctx);
  OSSL_PARAM_free(params);
  OSSL_PARAM_BLD_free(bld);
  return key;
}
static bool push_aes128gcm(const unsigned char *key, const unsigned char *nonce,
                           const unsigned char *pt, int ptlen,
                           std::string &ct) {
  EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
  if (!ctx)
    return false;
  ct.assign((size_t)ptlen + 16, '\0');
  int len = 0, fin = 0;
  bool ok = EVP_EncryptInit_ex(ctx, EVP_aes_128_gcm(), nullptr, nullptr, nullptr) == 1 &&
            EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, nullptr) == 1 &&
            EVP_EncryptInit_ex(ctx, nullptr, nullptr, key, nonce) == 1 &&
            EVP_EncryptUpdate(ctx, reinterpret_cast<unsigned char *>(&ct[0]), &len, pt, ptlen) == 1 &&
            EVP_EncryptFinal_ex(ctx, reinterpret_cast<unsigned char *>(&ct[0]) + len, &fin) == 1 &&
            EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16,
                                reinterpret_cast<unsigned char *>(&ct[0]) + len + fin) == 1;
  if (ok)
    ct.resize((size_t)len + (size_t)fin + 16);
  EVP_CIPHER_CTX_free(ctx);
  return ok;
}
static std::string push_es256(EVP_PKEY *key, const std::string &msg) {
  EVP_MD_CTX *ctx = EVP_MD_CTX_new();
  if (!ctx)
    return {};
  size_t siglen = 0;
  if (EVP_DigestSignInit(ctx, nullptr, EVP_sha256(), nullptr, key) != 1 ||
      EVP_DigestSignUpdate(ctx, msg.data(), msg.size()) != 1 ||
      EVP_DigestSignFinal(ctx, nullptr, &siglen) != 1) {
    EVP_MD_CTX_free(ctx);
    return {};
  }
  std::vector<unsigned char> der(siglen);
  if (EVP_DigestSignFinal(ctx, der.data(), &siglen) != 1) {
    EVP_MD_CTX_free(ctx);
    return {};
  }
  EVP_MD_CTX_free(ctx);
  der.resize(siglen);
  const unsigned char *p = der.data();
  ECDSA_SIG *sig = d2i_ECDSA_SIG(nullptr, &p, (long)der.size());
  if (!sig)
    return {};
  const BIGNUM *r = nullptr, *s = nullptr;
  ECDSA_SIG_get0(sig, &r, &s);
  unsigned char raw[64];
  bool ok = BN_bn2binpad(r, raw, 32) == 32 && BN_bn2binpad(s, raw + 32, 32) == 32;
  ECDSA_SIG_free(sig);
  if (!ok)
    return {};
  return push_b64url(raw, 64);
}
static std::string push_seal(const PushNote &note, const std::string &p256dh,
                             const std::string &auth) {
  std::string uapub = push_b64url_dec(p256dh), secret = push_b64url_dec(auth);
  if (uapub.size() != 65 || secret.size() != 16)
    return {};
  EVP_PKEY_CTX *kctx = EVP_PKEY_CTX_new_from_name(nullptr, "EC", nullptr);
  if (!kctx)
    return {};
  char group[] = "P-256";
  OSSL_PARAM gen[] = {OSSL_PARAM_construct_utf8_string("group", group, 0),
                      OSSL_PARAM_construct_end()};
  EVP_PKEY *eph = nullptr;
  bool gen_ok = EVP_PKEY_keygen_init(kctx) == 1 &&
                EVP_PKEY_CTX_set_params(kctx, gen) == 1 &&
                EVP_PKEY_generate(kctx, &eph) == 1;
  EVP_PKEY_CTX_free(kctx);
  if (!gen_ok || !eph)
    return {};
  unsigned char aspub[65];
  size_t aslen = 0;
  if (EVP_PKEY_get_octet_string_param(eph, "pub", aspub, sizeof aspub, &aslen) != 1 ||
      aslen != 65) {
    EVP_PKEY_free(eph);
    return {};
  }
  EVP_PKEY *peer = push_peer(reinterpret_cast<const unsigned char *>(uapub.data()));
  if (!peer) {
    EVP_PKEY_free(eph);
    return {};
  }
  EVP_PKEY_CTX *dctx = EVP_PKEY_CTX_new(eph, nullptr);
  std::vector<unsigned char> ecdh;
  bool derived = false;
  if (dctx && EVP_PKEY_derive_init(dctx) == 1 &&
      EVP_PKEY_derive_set_peer(dctx, peer) == 1) {
    size_t slen = 0;
    if (EVP_PKEY_derive(dctx, nullptr, &slen) == 1 && slen > 0) {
      ecdh.resize(slen);
      derived = EVP_PKEY_derive(dctx, ecdh.data(), &slen) == 1;
      ecdh.resize(slen);
    }
  }
  EVP_PKEY_CTX_free(dctx);
  EVP_PKEY_free(peer);
  EVP_PKEY_free(eph);
  if (!derived || ecdh.empty())
    return {};
  std::string key_info = "WebPush: info";
  key_info.push_back('\0');
  key_info.append(uapub);
  key_info.append(reinterpret_cast<char *>(aspub), 65);
  unsigned char ikm[32];
  if (!push_hkdf(reinterpret_cast<const unsigned char *>(secret.data()), secret.size(),
                 ecdh.data(), ecdh.size(),
                 reinterpret_cast<const unsigned char *>(key_info.data()), key_info.size(),
                 ikm, sizeof ikm))
    return {};
  unsigned char salt[16];
  if (RAND_bytes(salt, sizeof salt) != 1)
    return {};
  std::string cek_info = "Content-Encoding: aes128gcm";
  cek_info.push_back('\0');
  std::string nonce_info = "Content-Encoding: nonce";
  nonce_info.push_back('\0');
  unsigned char cek[16], nonce[12];
  if (!push_hkdf(salt, sizeof salt, ikm, sizeof ikm,
                 reinterpret_cast<const unsigned char *>(cek_info.data()), cek_info.size(),
                 cek, sizeof cek) ||
      !push_hkdf(salt, sizeof salt, ikm, sizeof ikm,
                 reinterpret_cast<const unsigned char *>(nonce_info.data()), nonce_info.size(),
                 nonce, sizeof nonce))
    return {};
  Val payload = Val::object();
  payload["kind"] = note.kind;
  payload["title"] = note.title;
  payload["body"] = note.body;
  payload["url"] = note.url;
  payload["tag"] = note.tag;
  std::string plain = mj::dump(payload);
  plain.push_back('\x02');
  std::string ct;
  if (!push_aes128gcm(cek, nonce, reinterpret_cast<const unsigned char *>(plain.data()),
                      (int)plain.size(), ct))
    return {};
  std::string body;
  body.reserve(16 + 4 + 1 + 65 + ct.size());
  body.append(reinterpret_cast<char *>(salt), 16);
  body.push_back(0);
  body.push_back(0);
  body.push_back(0x10);  // record size 4096
  body.push_back(0);
  body.push_back(65);
  body.append(reinterpret_cast<char *>(aspub), 65);
  body += ct;
  return body;
}
static std::string push_jwt(const std::string &endpoint) {
  PushKeys &keys = push_keys();
  if (!keys.ok)
    return {};
  std::string header = push_b64url(
      reinterpret_cast<const unsigned char *>("{\"typ\":\"JWT\",\"alg\":\"ES256\"}"),
      strlen("{\"typ\":\"JWT\",\"alg\":\"ES256\"}"));
  long exp = (long)time(nullptr) + 12 * 3600;
  std::string claims = "{\"aud\":\"" + push_origin(endpoint) +
                       "\",\"exp\":" + std::to_string(exp) + ",\"sub\":\"" +
                       push_subject() + "\"}";
  std::string signing = header + "." +
                        push_b64url(reinterpret_cast<const unsigned char *>(claims.data()),
                                    claims.size());
  std::string sig = push_es256(keys.pkey, signing);
  if (sig.empty())
    return {};
  return signing + "." + sig;
}
static int push_http_status(const std::string &out) {
  std::string last;
  std::string cur;
  for (char c : out) {
    if (c == '\n' || c == '\r') {
      if (!cur.empty())
        last = cur;
      cur.clear();
    } else
      cur.push_back(c);
  }
  if (!cur.empty())
    last = cur;
  if (last.size() != 3)
    return 0;
  for (char c : last)
    if (c < '0' || c > '9')
      return 0;
  return atoi(last.c_str());
}
static void push_deliver(const PushNote &note) {
  struct Sub {
    std::string endpoint, p256dh, auth;
  };
  std::vector<Sub> subs;
  {
    std::lock_guard<std::mutex> lk(PUSH_MTX);
    Val all = push_load_locked();
    for (auto &item : all.asArr()) {
      auto ep = item.find("endpoint");
      auto dh = item.find("p256dh");
      auto au = item.find("auth");
      if (!ep || !dh || !au)
        continue;
      const Val *types = item.find("types");
      bool want = (note.kind == "job" && push_flag(types, "job")) ||
                  (note.kind == "run" && push_flag(types, "run")) ||
                  (note.kind == "error" && push_flag(types, "error"));
      if (!want)
        continue;
      subs.push_back({ep->asStr(), dh->asStr(), au->asStr()});
    }
  }
  std::vector<std::string> gone;
  for (auto &sub : subs) {
    std::string body = push_seal(note, sub.p256dh, sub.auth);
    std::string jwt = push_jwt(sub.endpoint);
    if (body.empty() || jwt.empty()) {
      std::cerr << "push: encrypt failed\n";
      continue;
    }
    char path[] = "/tmp/mag-push-XXXXXX";
    int fd = mkstemp(path);
    if (fd < 0)
      continue;
    bool wrote = write(fd, body.data(), body.size()) == (ssize_t)body.size();
    close(fd);
    if (!wrote) {
      unlink(path);
      continue;
    }
    std::string authz = "Authorization: vapid t=" + jwt + ", k=" + push_public_b64();
    std::string urgency = note.kind == "error" ? "Urgency: high" : "Urgency: normal";
    CmdResult result;
    try {
      result = run_argv({"curl", "-sS", "--http1.1", "--connect-timeout", "8", "-m", "20",
                         "-o", "/dev/null", "-w", "\n%{http_code}", "-X", "POST",
                         "-H", "TTL: 86400", "-H", urgency,
                         "-H", "Content-Encoding: aes128gcm",
                         "-H", "Content-Type: application/octet-stream",
                         "-H", authz, "--data-binary", std::string("@") + path,
                         sub.endpoint},
                        25);
    } catch (const std::exception &e) {
      std::cerr << "push: " << e.what() << "\n";
      unlink(path);
      continue;
    }
    unlink(path);
    int status = push_http_status(result.out);
    if (status == 404 || status == 410)
      gone.push_back(sub.endpoint);
    else if (status < 200 || status >= 300)
      std::cerr << "push: delivery " << status << "\n";
  }
  if (!gone.empty()) {
    std::lock_guard<std::mutex> lk(PUSH_MTX);
    Val all = push_load_locked();
    Val next = Val::array();
    for (auto &item : all.asArr()) {
      auto ep = item.find("endpoint");
      bool drop = false;
      if (ep)
        for (auto &g : gone)
          if (g == ep->asStr())
            drop = true;
      if (!drop)
        next.push(item);
    }
    push_save_locked(next);
  }
}

// Sends one notification to every device that opted into note.kind.
// Safe to call from the scheduler: delivery runs on its own thread.
inline void notify(const PushNote &event) {
  try {
    if (!push_configured())
      return;
    PushNote note;
    if (event.kind != "job" && event.kind != "run" && event.kind != "error")
      return;
    note.kind = event.kind;
    note.title = push_collapse(event.title);
    if (note.title.empty())
      note.title = "MAG";
    if (note.title.size() > 120)
      note.title.resize(120);
    note.body = push_collapse(event.body);
    note.url = push_safe_url(event.url);
    note.tag = event.tag.empty() ? event.kind : event.tag;
    if (note.tag.size() > 80)
      note.tag.resize(80);
    for (char &c : note.tag)
      if (c <= 32 || c == '"')
        c = '-';
    {
      std::lock_guard<std::mutex> lk(PUSH_RECENT_MTX);
      auto now = steady_clock::now();
      auto it = PUSH_RECENT.find(note.kind + ":" + note.tag);
      if (it != PUSH_RECENT.end() && now - it->second < seconds(8))
        return;
      PUSH_RECENT[note.kind + ":" + note.tag] = now;
    }
    std::thread([note] {
      try {
        push_deliver(note);
      } catch (const std::exception &e) {
        std::cerr << "push: " << e.what() << "\n";
      }
    }).detach();
  } catch (const std::exception &e) {
    std::cerr << "push: " << e.what() << "\n";
  }
}
