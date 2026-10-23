// ---------------------------------------------------------------- secrets
// Passwords/tokens a bot needs. The value goes from a masked card straight
// into STATE_DIR/secrets.json, AES-256-GCM encrypted with a key that lives
// outside the state (systemd credential mag-secrets-key). The bot only gets
// a handle {{secret:NAME}} that the room expands inside browser_type /
// run_command arguments; every tool result is scrubbed before it reaches
// the model, the room or the logs. Values are never returned by any API.
#include <openssl/rand.h>

static std::mutex SECRETS_MTX;
static std::string secrets_file() { return STATE_DIR + "/secrets.json"; }

static std::string hex_of(const unsigned char *p, size_t n) {
  static const char *hx = "0123456789abcdef";
  std::string o;
  for (size_t i = 0; i < n; i++) { o += hx[p[i] >> 4]; o += hx[p[i] & 15]; }
  return o;
}
static std::string unhex(const std::string &h) {
  std::string o;
  for (size_t i = 0; i + 1 < h.size(); i += 2) o += (char)std::stoi(h.substr(i, 2), nullptr, 16);
  return o;
}
// 32-byte key = SHA-256 of the credential file.
static std::string secrets_key() {
  std::string path = env("MAG_SECRETS_KEY_FILE", "");
  if (path.empty())
    if (const char *cd = std::getenv("CREDENTIALS_DIRECTORY"))
      if (std::filesystem::exists(std::string(cd) + "/mag-secrets-key")) path = std::string(cd) + "/mag-secrets-key";
  if (path.empty()) {
    // Development fallback only; production uses LoadCredential (ops/secrets/install.sh).
    path = STATE_DIR + "/.secrets.key";
    if (!std::filesystem::exists(path)) {
      unsigned char k[32];
      RAND_bytes(k, sizeof k);
      write_file(path, hex_of(k, sizeof k));
      chmod(path.c_str(), 0600);
      std::cerr << "secrets: no mag-secrets-key credential, generated " << path << std::endl;
    }
  }
  const std::string raw = read_file(path);
  if (raw.empty()) throw std::runtime_error("Secret-Schlüssel fehlt.");
  unsigned char md[32]; unsigned int n = 0;
  EVP_Digest(raw.data(), raw.size(), md, &n, EVP_sha256(), nullptr);
  return std::string((char *)md, 32);
}
static Val secret_encrypt(const std::string &plain) {
  const std::string key = secrets_key();
  unsigned char iv[12], tag[16];
  RAND_bytes(iv, sizeof iv);
  std::string ct(plain.size(), '\0');
  int len = 0;
  EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
  EVP_EncryptInit_ex(c, EVP_aes_256_gcm(), nullptr, (const unsigned char *)key.data(), iv);
  EVP_EncryptUpdate(c, (unsigned char *)ct.data(), &len, (const unsigned char *)plain.data(), (int)plain.size());
  EVP_EncryptFinal_ex(c, nullptr, &len);
  EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_GCM_GET_TAG, 16, tag);
  EVP_CIPHER_CTX_free(c);
  Val v = Val::object();
  v["iv"] = hex_of(iv, 12);
  v["ct"] = hex_of((const unsigned char *)ct.data(), ct.size());
  v["tag"] = hex_of(tag, 16);
  return v;
}
static std::string secret_decrypt(const Val &e) {
  const std::string key = secrets_key(), iv = unhex(e.find("iv")->asStr()),
                    ct = unhex(e.find("ct")->asStr()), tag = unhex(e.find("tag")->asStr());
  std::string out(ct.size(), '\0');
  int len = 0;
  EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
  EVP_DecryptInit_ex(c, EVP_aes_256_gcm(), nullptr, (const unsigned char *)key.data(), (const unsigned char *)iv.data());
  EVP_DecryptUpdate(c, (unsigned char *)out.data(), &len, (const unsigned char *)ct.data(), (int)ct.size());
  EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_GCM_SET_TAG, 16, (void *)tag.data());
  const int ok = EVP_DecryptFinal_ex(c, nullptr, &len);
  EVP_CIPHER_CTX_free(c);
  if (ok <= 0) throw std::runtime_error("Secret nicht entschlüsselbar (falscher Schlüssel?)");
  return out;
}
static Val secrets_load_locked() {
  Val v;
  if (!mj::parse(read_file(secrets_file()), v) || !v.isObj()) v = Val::object();
  if (!v.find("secrets") || !v["secrets"].isObj()) v["secrets"] = Val::object();
  return v;
}
static void secrets_save_locked(const Val &v) {
  write_file_atomic(secrets_file(), mj::dump(v));
  chmod(secrets_file().c_str(), 0600);
}
static std::string secret_name_ok(std::string n) {
  for (auto &c : n) c = (char)toupper((unsigned char)c);
  if (n.empty() || n.size() > 48) return "";
  for (char c : n) if (!(isalnum((unsigned char)c) || c == '_')) return "";
  return n;
}
static bool secret_allowed(const Val &s, const std::string &bot) {
  for (auto &b : s.find("bots") ? s.find("bots")->asArr() : Val().asArr())
    if (b.asStr() == bot) return true;
  return false;
}
static void secret_store(const std::string &name, const std::string &value, const std::string &purpose,
                         const std::string &grant_bot) {
  if (value.empty() || value.size() > 8192) throw std::runtime_error("Wert leer oder zu lang.");
  std::lock_guard<std::mutex> lk(SECRETS_MTX);
  Val all = secrets_load_locked();
  Val &s = all["secrets"][name];
  if (!s.isObj()) { s = Val::object(); s["created"] = (double)now_s(); s["bots"] = Val::array(); }
  s["enc"] = secret_encrypt(value);
  s["updated"] = (double)now_s();
  if (!purpose.empty()) s["purpose"] = purpose.substr(0, 200);
  if (!grant_bot.empty() && !secret_allowed(s, grant_bot)) s["bots"].push(grant_bot);
  secrets_save_locked(all);
}
static Val secrets_public() {
  std::lock_guard<std::mutex> lk(SECRETS_MTX);
  Val all = secrets_load_locked(), out = Val::array();
  for (auto &kv : *all["secrets"].obj) {
    Val v = Val::object();
    v["name"] = kv.first;
    for (const char *k : {"purpose", "bots", "created", "updated", "last_used"})
      if (auto f = kv.second.find(k)) v[k] = *f;
    out.push(v);
  }
  return out;
}
static bool secret_set_bots(const std::string &name, const Val &bots) {
  std::lock_guard<std::mutex> lk(SECRETS_MTX);
  Val all = secrets_load_locked();
  Val *s = const_cast<Val *>(all["secrets"].find(name));
  if (!s) return false;
  Val arr = Val::array();
  for (auto &b : bots.asArr()) if (agent_index(b.asStr()) >= 0) arr.push(b.asStr());
  (*s)["bots"] = arr;
  secrets_save_locked(all);
  return true;
}
static bool secret_delete(const std::string &name) {
  std::lock_guard<std::mutex> lk(SECRETS_MTX);
  Val all = secrets_load_locked();
  auto &o = *all["secrets"].obj;
  for (auto it = o.begin(); it != o.end(); ++it)
    if (it->first == name) { o.erase(it); secrets_save_locked(all); return true; }
  return false;
}
// name -> value for every secret this bot may use.
static std::map<std::string, std::string> secrets_for(const std::string &bot) {
  std::map<std::string, std::string> out;
  std::lock_guard<std::mutex> lk(SECRETS_MTX);
  Val all = secrets_load_locked();
  for (auto &kv : *all["secrets"].obj)
    if (secret_allowed(kv.second, bot) && kv.second.find("enc"))
      try { out[kv.first] = secret_decrypt(*kv.second.find("enc")); } catch (...) {}
  return out;
}
static void secret_touch(const std::string &name) {
  std::lock_guard<std::mutex> lk(SECRETS_MTX);
  Val all = secrets_load_locked();
  if (Val *s = const_cast<Val *>(all["secrets"].find(name))) { (*s)["last_used"] = (double)now_s(); secrets_save_locked(all); }
}
// Expands {{secret:NAME}} in the string arguments of tools that fill forms
// or run commands. Unknown/forbidden handles are an error, never a leak.
static bool secret_expand(const std::string &bot, const std::string &tool, Val &args) {
  static const std::set<std::string> allowed = {"browser_type", "run_command"};
  const std::string dumped = mj::dump(args);
  if (dumped.find("{{secret:") == std::string::npos) return false;
  if (!allowed.count(tool))
    throw std::runtime_error("Secret-Handles nur in browser_type und run_command.");
  auto vals = secrets_for(bot);
  bool used = false;
  for (auto &kv : *args.obj) {
    if (kv.second.type != Val::STR) continue;
    std::string &s = kv.second.str;
    size_t p;
    while ((p = s.find("{{secret:")) != std::string::npos) {
      size_t e = s.find("}}", p);
      if (e == std::string::npos) break;
      const std::string name = secret_name_ok(s.substr(p + 9, e - p - 9));
      auto it = vals.find(name);
      if (name.empty() || it == vals.end())
        throw std::runtime_error("Secret " + s.substr(p + 9, e - p - 9) + " fehlt oder ist für diesen Bot nicht freigegeben. Mit secret(request) anfragen.");
      s.replace(p, e + 2 - p, it->second);
      secret_touch(name);
      used = true;
    }
  }
  return used;
}
static std::string secret_scrub(const std::string &bot, std::string text) {
  for (auto &kv : secrets_for(bot))
    if (kv.second.size() >= 4) replaceAll(text, kv.second, "[secret:" + kv.first + "]");
  return text;
}

// ---- request card (approval kind "secret")
struct SecretReq { std::string bot, name, purpose; };
static std::map<std::string, SecretReq> SECRET_REQS; // SECRETS_MTX
static const char *T_SECRET =
    R"({"type":"function","function":{"name":"secret","description":"Passwords, tokens, logins. request asks the user for a secret through a masked card (name: UPPER_SNAKE, purpose: why); you never see the value. Then write the handle {{secret:NAME}} in browser_type text or run_command to use it. list shows the secrets you may use. Never ask for passwords in chat.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["request","list"]},"name":{"type":"string"},"purpose":{"type":"string"}},"required":["action"],"additionalProperties":false}}})";
static std::string secret_tool(int bot_idx, const Val &a) {
  auto s = [&](const char *k) { auto v = a.find(k); return v ? trim(v->asStr()) : std::string(); };
  const std::string bot = bot_field(bot_idx, "id");
  if (s("action") == "list") {
    std::string out;
    for (auto &v : secrets_public().asArr())
      if (secret_allowed(v, bot)) out += "{{secret:" + v.find("name")->asStr() + "}}" +
          (v.find("purpose") ? " – " + v.find("purpose")->asStr() : "") + "\n";
    return out.empty() ? "Keine Secrets freigegeben." : out;
  }
  if (s("action") != "request") throw std::runtime_error("Unbekannte secret-Aktion.");
  const std::string name = secret_name_ok(s("name"));
  if (name.empty()) throw std::runtime_error("name: 1-48 Zeichen A-Z, 0-9, _");
  if (secrets_for(bot).count(name)) return "Schon vorhanden: nutze {{secret:" + name + "}}.";
  McpApproval req;
  req.id = token_hex(8);
  req.bot = bot;
  req.bot_name = bot_field(bot_idx, "name", "Bot");
  req.server = "secrets";
  req.server_name = "Secrets";
  req.tool = "secret";
  req.kind = "secret";
  Val info = Val::object();
  info["name"] = name;
  info["purpose"] = s("purpose").substr(0, 200);
  req.args = mj::dump(info);
  req.ts = (long)now_s();
  {
    std::lock_guard<std::mutex> lk(SECRETS_MTX);
    SECRET_REQS[req.id] = SecretReq{bot, name, s("purpose").substr(0, 200)};
  }
  const std::string d = mcp_await_approval(req);
  {
    std::lock_guard<std::mutex> lk(SECRETS_MTX);
    SECRET_REQS.erase(req.id);
  }
  if (d == "allow") return "Gespeichert. Nutze {{secret:" + name + "}} in browser_type oder run_command; der Wert bleibt verborgen.";
  if (d == "deny") return "Der Nutzer hat abgelehnt. Nicht erneut fragen.";
  return "Kein Secret erhalten (" + d + ").";
}
// The masked card posts here; the value never touches the room or the model.
static bool secret_fulfil(const std::string &id, const std::string &value) {
  SecretReq r;
  {
    std::lock_guard<std::mutex> lk(SECRETS_MTX);
    auto it = SECRET_REQS.find(id);
    if (it == SECRET_REQS.end()) return false;
    r = it->second;
  }
  secret_store(r.name, value, r.purpose, r.bot);
  return mcp_decide(id, "allow");
}
