// Plugins: remote MCP servers that a bot may use, chosen per bot in the chat
// ("+" > Plugins & MCP, or /mcp). Streamable HTTP transport only: the room
// speaks JSON-RPC over HTTPS from the host. Nothing is installed on the host
// or in a guest, and no server process is started. Secrets stay in
// STATE_DIR/mcp/<id>.secret (0600) and never reach the browser.
//
// Tool lists are cached in servers.json and only change on connect/refresh,
// so a bot's tool set (and its prompt cache) changes only when the owner
// changes it. Tools without readOnlyHint need the owner's approval in the
// chat unless the bot's binding says "auto" or the tool was "always" allowed.
#pragma once
#include <curl/curl.h>
#include <unistd.h>
#include <atomic>
#include <condition_variable>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

static const char *MCP_PROTOCOL = "2025-06-18";
static const size_t MCP_MAX_BOT_TOOLS = 64;

static std::mutex MCP_MTX;
static std::vector<Val> MCP_SERVERS; // MCP_MTX
static bool MCP_LOADED = false;      // MCP_MTX
static std::map<std::string, std::string> MCP_SESSIONS; // MCP_MTX

static std::string mcp_dir() { return STATE_DIR + "/mcp"; }
// Sessions are kept per server and bot: servers like Digitalisierungsplanung
// hold one working project per session, so bots must not share it.
static void mcp_forget_sessions_locked(const std::string &sid) {
  for (auto it = MCP_SESSIONS.begin(); it != MCP_SESSIONS.end();)
    if (it->first == sid || it->first.rfind(sid + "|", 0) == 0) it = MCP_SESSIONS.erase(it);
    else ++it;
}
static std::string mcp_str(const Val &v, const char *k) {
  auto x = v.find(k);
  return x ? x->asStr() : std::string();
}
static void mcp_load_locked() {
  if (MCP_LOADED) return;
  MCP_LOADED = true;
  try {
    MCP_SERVERS = load_json_array(mcp_dir() + "/servers.json");
  } catch (const std::exception &e) {
    std::cerr << "MAG plugins: " << e.what() << "\n";
    MCP_SERVERS.clear();
  }
}
static void mcp_save_locked() {
  std::filesystem::create_directories(mcp_dir());
  chmod(mcp_dir().c_str(), 0700);
  std::vector<Val> copy = MCP_SERVERS;
  save_json_array(mcp_dir() + "/servers.json", copy);
}
static Val *mcp_find_locked(const std::string &id) {
  mcp_load_locked();
  for (auto &s : MCP_SERVERS)
    if (mcp_str(s, "id") == id) return &s;
  return nullptr;
}
static bool mcp_safe_id(const std::string &id) {
  if (id.empty() || id.size() > 32) return false;
  for (char c : id)
    if (!(islower((unsigned char)c) || isdigit((unsigned char)c) || c == '-'))
      return false;
  return true;
}
static std::string mcp_secret_path(const std::string &id) {
  return mcp_dir() + "/" + id + ".secret";
}
static bool mcp_write_secret(const std::string &id, const std::string &secret) {
  if (!mcp_safe_id(id)) return false;
  std::filesystem::create_directories(mcp_dir());
  chmod(mcp_dir().c_str(), 0700);
  const std::string file = mcp_secret_path(id), tmp = file + ".tmp";
  int fd = open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
  bool ok = fd >= 0 &&
            write(fd, secret.data(), secret.size()) == (ssize_t)secret.size();
  if (fd >= 0) close(fd);
  return ok && rename(tmp.c_str(), file.c_str()) == 0;
}
static std::string mcp_secret(const Val &srv) {
  const std::string auth = mcp_str(srv, "auth");
  bool ok = false;
  if (auth == "connector") // reuse the room's GitHub connector token
    return trim(read_file(STATE_DIR + "/connectors/github.token", &ok));
  if (auth != "bearer" && auth != "header") return "";
  const std::string id = mcp_str(srv, "id");
  if (!mcp_safe_id(id)) return "";
  return trim(read_file(mcp_secret_path(id), &ok));
}

// ------------------------------------------------------------- URL policy
static std::string mcp_url_host(const std::string &url) {
  auto p = url.find("://");
  if (p == std::string::npos) return "";
  std::string rest = url.substr(p + 3);
  rest = rest.substr(0, rest.find_first_of("/?#"));
  if (rest.find('@') != std::string::npos) return ""; // no userinfo
  if (!rest.empty() && rest[0] == '[') return rest.substr(0, rest.find(']') + 1);
  return rest.substr(0, rest.find(':'));
}
// Loopback and the tailnet (100.64.0.0/10) may use plain HTTP and stay
// reachable in offline mode; everything else needs HTTPS and the internet.
static bool mcp_private_host(const std::string &host) {
  if (host == "127.0.0.1" || host == "localhost" || host == "[::1]") return true;
  unsigned a = 0, b = 0, c = 0, d = 0;
  char tail = 0;
  if (sscanf(host.c_str(), "%u.%u.%u.%u%c", &a, &b, &c, &d, &tail) == 4)
    return a == 100 && b >= 64 && b <= 127 && c < 256 && d < 256;
  return false;
}
static std::string mcp_check_url(const std::string &url) {
  if (url.empty() || url.size() > 500) return "URL fehlt oder ist zu lang";
  for (unsigned char ch : url)
    if (ch <= 0x20 || ch == 0x7f) return "URL enthält Leer- oder Steuerzeichen";
  const std::string host = mcp_url_host(url);
  if (host.empty()) return "URL ungültig";
  if (url.rfind("https://", 0) == 0) return "";
  if (url.rfind("http://", 0) == 0 && mcp_private_host(host)) return "";
  return "Nur https:// (http:// nur für localhost und das Tailnet)";
}

// ------------------------------------------------------------- transport
struct McpHttp {
  long status = 0;
  std::string body, ctype, session, error;
};
struct McpSessionGone : std::runtime_error {
  McpSessionGone() : std::runtime_error("MCP-Sitzung abgelaufen") {}
};
static size_t mcp_write_cb(char *p, size_t s, size_t n, void *u) {
  auto *out = static_cast<std::string *>(u);
  const size_t k = s * n;
  if (out->size() + k > 8u * 1024 * 1024) return 0;
  out->append(p, k);
  return k;
}
static size_t mcp_header_cb(char *p, size_t s, size_t n, void *u) {
  auto *r = static_cast<McpHttp *>(u);
  std::string h(p, s * n);
  auto colon = h.find(':');
  if (colon != std::string::npos) {
    std::string key = h.substr(0, colon), val = trim(h.substr(colon + 1));
    std::transform(key.begin(), key.end(), key.begin(), ::tolower);
    if (key == "mcp-session-id" && val.size() <= 256) r->session = val;
    if (key == "content-type") {
      std::transform(val.begin(), val.end(), val.begin(), ::tolower);
      r->ctype = val;
    }
  }
  return s * n;
}
// Only a bot turn can be stopped; HTTP threads (connect, refresh) never are.
static int mcp_progress_cb(void *, curl_off_t, curl_off_t, curl_off_t, curl_off_t) {
  return TL_LANE && TL_LANE->stop.load() ? 1 : 0;
}
static McpHttp mcp_post(const Val &srv, const std::string &secret,
                        const std::string &session, const std::string &payload,
                        long timeout_s, bool initialized) {
  McpHttp res;
  const std::string url = mcp_str(srv, "url");
  const std::string bad = mcp_check_url(url);
  if (!bad.empty()) { res.error = bad; return res; }
  CURL *c = curl_easy_init();
  if (!c) { res.error = "curl nicht verfügbar"; return res; }
  curl_slist *h = nullptr;
  h = curl_slist_append(h, "Content-Type: application/json");
  h = curl_slist_append(h, "Accept: application/json, text/event-stream");
  if (initialized)
    h = curl_slist_append(h, (std::string("MCP-Protocol-Version: ") + MCP_PROTOCOL).c_str());
  if (!session.empty()) h = curl_slist_append(h, ("Mcp-Session-Id: " + session).c_str());
  const std::string auth = mcp_str(srv, "auth");
  if (!secret.empty() && secret.find_first_of("\r\n") == std::string::npos) {
    if (auth == "header") {
      const std::string name = mcp_str(srv, "header");
      if (!name.empty()) h = curl_slist_append(h, (name + ": " + secret).c_str());
    } else if (auth == "bearer" || auth == "connector") {
      h = curl_slist_append(h, ("Authorization: Bearer " + secret).c_str());
    }
  }
  const bool plain = url.rfind("http://", 0) == 0;
  // MAG's room traffic is intercepted by the local audit proxy (systemd
  // drop-in 20-deepseek-audit.conf). The proxy presents certificates signed
  // by /etc/mag-audit/mitmproxy-ca-cert.pem. The previous hard-coded system
  // bundle DISCARDED SSL_CERT_FILE=/etc/mag-audit/ca-bundle.pem and caused
  // CURLE_PEER_FAILED_VERIFICATION for every remote MCP server. Trust the
  // combined host + audit CA ONLY while using that particular local proxy.
  // Keep strict libcurl TLS peer/host verification; never use -k.
  const std::string proxy = env("https_proxy", env("HTTPS_PROXY", ""));
  const bool audited = proxy == "http://127.0.0.1:8888" ||
                       proxy == "http://localhost:8888";
  const char *cafile = audited ? "/etc/mag-audit/ca-bundle.pem"
                               : "/etc/ssl/certs/ca-certificates.crt";
  if (!plain && access(cafile, R_OK) != 0) {
    curl_slist_free_all(h);
    curl_easy_cleanup(c);
    res.error = std::string("MCP TLS CA bundle fehlt: ") + cafile;
    return res;
  }
  curl_easy_setopt(c, CURLOPT_URL, url.c_str());
  if (!plain)
    curl_easy_setopt(c, CURLOPT_CAINFO, cafile);
  curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 1L);
  curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST, 2L);
  curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
  curl_easy_setopt(c, CURLOPT_PROTOCOLS_STR, plain ? "http" : "https");
  curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 0L);
  curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 8L);
  curl_easy_setopt(c, CURLOPT_TIMEOUT, timeout_s);
  curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(c, CURLOPT_USERAGENT, "MAG-Raum/1 (MCP)");
  curl_easy_setopt(c, CURLOPT_POST, 1L);
  curl_easy_setopt(c, CURLOPT_POSTFIELDS, payload.c_str());
  curl_easy_setopt(c, CURLOPT_POSTFIELDSIZE, (long)payload.size());
  curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, mcp_write_cb);
  curl_easy_setopt(c, CURLOPT_WRITEDATA, &res.body);
  curl_easy_setopt(c, CURLOPT_HEADERFUNCTION, mcp_header_cb);
  curl_easy_setopt(c, CURLOPT_HEADERDATA, &res);
  curl_easy_setopt(c, CURLOPT_NOPROGRESS, 0L);
  curl_easy_setopt(c, CURLOPT_XFERINFOFUNCTION, mcp_progress_cb);
  char errbuf[CURL_ERROR_SIZE];
  errbuf[0] = 0;
  curl_easy_setopt(c, CURLOPT_ERRORBUFFER, errbuf);
  CURLcode rc = curl_easy_perform(c);
  curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &res.status);
  curl_slist_free_all(h);
  curl_easy_cleanup(c);
  if (rc == CURLE_ABORTED_BY_CALLBACK) res.error = "abgebrochen";
  else if (rc != CURLE_OK)
    res.error = errbuf[0] ? std::string(errbuf) : curl_easy_strerror(rc);
  return res;
}
// The JSON-RPC response with this id, from a JSON body or an SSE stream.
static bool mcp_pick(const McpHttp &h, double id, Val &out) {
  auto take = [&](const std::string &json) {
    Val v;
    if (!mj::parse(json, v)) return false;
    auto match = [&](const Val &m) {
      auto i = m.find("id");
      if (!i || i->asNum(-1) != id) return false;
      out = m;
      return true;
    };
    if (v.isArr()) {
      for (auto &m : v.asArr())
        if (match(m)) return true;
      return false;
    }
    return match(v);
  };
  if (h.ctype.find("text/event-stream") == std::string::npos) return take(h.body);
  std::istringstream in(h.body);
  std::string line, data;
  auto flush = [&] {
    const bool ok = !data.empty() && take(data);
    data.clear();
    return ok;
  };
  while (std::getline(in, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty()) {
      if (flush()) return true;
      continue;
    }
    if (line.rfind("data:", 0) == 0) {
      std::string d = line.substr(5);
      if (!d.empty() && d[0] == ' ') d.erase(0, 1);
      if (!data.empty()) data += "\n";
      data += d;
    }
  }
  return flush();
}
static std::string mcp_http_error(const McpHttp &h) {
  if (!h.error.empty()) return "Verbindung fehlgeschlagen: " + h.error;
  if (h.status == 401 || h.status == 403)
    return "Anmeldung abgelehnt (HTTP " + std::to_string(h.status) +
           "). Token oder Rechte prüfen.";
  std::string msg;
  Val v;
  if (mj::parse(h.body, v)) {
    if (auto e = v.find("error")) msg = e->isObj() ? mcp_str(*e, "message") : e->asStr();
    if (msg.empty()) msg = mcp_str(v, "message");
  }
  if (msg.empty()) {
    msg = h.body.substr(0, 160);
    for (char &ch : msg)
      if (ch == '\n' || ch == '\r' || ch == '\t') ch = ' ';
    msg = trim(msg);
  }
  return "HTTP " + std::to_string(h.status) + (msg.empty() ? "" : ": " + msg);
}
static std::atomic<long> MCP_RPC_ID{1};
static Val mcp_rpc(const Val &srv, const std::string &secret,
                   const std::string &session, const std::string &method,
                   const Val &params, long timeout_s, McpHttp *raw = nullptr) {
  const double id = (double)MCP_RPC_ID++;
  Val req = Val::object();
  req["jsonrpc"] = "2.0";
  req["id"] = id;
  req["method"] = method;
  req["params"] = params;
  McpHttp h = mcp_post(srv, secret, session, mj::dump(req), timeout_s,
                       method != "initialize");
  if (raw) *raw = h;
  if (h.error.empty() && h.status == 404 && !session.empty()) throw McpSessionGone();
  if (!h.error.empty() || h.status < 200 || h.status >= 300)
    throw std::runtime_error(mcp_http_error(h));
  Val msg;
  if (!mcp_pick(h, id, msg)) throw std::runtime_error("Keine gültige MCP-Antwort");
  if (auto e = msg.find("error")) {
    std::string m = e->isObj() ? mcp_str(*e, "message") : e->asStr();
    throw std::runtime_error("MCP-Fehler: " + (m.empty() ? std::string("unbekannt") : m.substr(0, 300)));
  }
  auto r = msg.find("result");
  return r ? *r : Val::object();
}
static void mcp_notify(const Val &srv, const std::string &secret,
                       const std::string &session, const std::string &method) {
  Val n = Val::object();
  n["jsonrpc"] = "2.0";
  n["method"] = method;
  mcp_post(srv, secret, session, mj::dump(n), 10, true);
}
// initialize + notifications/initialized; returns the session id (may be empty).
static std::string mcp_initialize(const Val &srv, const std::string &secret,
                                  Val *server_info = nullptr) {
  Val p = Val::object();
  p["protocolVersion"] = MCP_PROTOCOL;
  p["capabilities"] = Val::object();
  Val ci = Val::object();
  ci["name"] = "mag-room";
  ci["title"] = "MAG Raum";
  ci["version"] = "1";
  p["clientInfo"] = ci;
  McpHttp raw;
  Val r = mcp_rpc(srv, secret, "", "initialize", p, 20, &raw);
  if (server_info) {
    Val info = Val::object();
    if (auto si = r.find("serverInfo")) {
      info["name"] = mcp_str(*si, "name").substr(0, 80);
      info["version"] = mcp_str(*si, "version").substr(0, 40);
    }
    info["protocol"] = mcp_str(r, "protocolVersion").substr(0, 20);
    const std::string how = mcp_str(r, "instructions");
    if (!how.empty()) info["instructions"] = mj::utf8_cut(mj::utf8_fix(how), 2000);
    *server_info = info;
  }
  mcp_notify(srv, secret, raw.session, "notifications/initialized");
  return raw.session;
}
static Val mcp_fetch_tools(const Val &srv, const std::string &secret,
                           const std::string &session) {
  Val tools = Val::array();
  std::string cursor;
  for (int page = 0; page < 20; page++) {
    Val p = Val::object();
    if (!cursor.empty()) p["cursor"] = cursor;
    Val r = mcp_rpc(srv, secret, session, "tools/list", p, 30);
    if (auto list = r.find("tools"); list && list->isArr())
      for (auto &t : list->asArr()) {
        const std::string name = mcp_str(t, "name");
        if (name.empty() || name.size() > 128 || tools.arr->size() >= 200) continue;
        Val o = Val::object();
        o["name"] = name;
        std::string title = mcp_str(t, "title");
        if (title.empty())
          if (auto an = t.find("annotations")) title = mcp_str(*an, "title");
        if (!title.empty()) o["title"] = title.substr(0, 120);
        o["description"] = mj::utf8_cut(mcp_str(t, "description"), 1024);
        auto schema = t.find("inputSchema");
        if (schema && schema->isObj()) o["input_schema"] = *schema;
        else {
          Val s = Val::object();
          s["type"] = "object";
          s["properties"] = Val::object();
          o["input_schema"] = s;
        }
        bool ro = false;
        if (auto an = t.find("annotations"))
          if (auto h = an->find("readOnlyHint")) ro = h->truthy();
        o["read_only"] = ro;
        tools.push(o);
      }
    cursor = mcp_str(r, "nextCursor");
    if (cursor.empty()) break;
  }
  return tools;
}
// Connects and refreshes the cached tool list of one server (copy in, copy
// out; no lock is held during the network round trips).
static Val mcp_probe(Val srv) {
  const std::string id = mcp_str(srv, "id");
  try {
    const std::string secret = mcp_secret(srv);
    Val info;
    std::string session = mcp_initialize(srv, secret, &info);
    srv["tools"] = mcp_fetch_tools(srv, secret, session);
    srv["server"] = info;
    srv["status"] = "ok";
    srv["error"] = "";
  } catch (const std::exception &e) {
    srv["status"] = "error";
    srv["error"] = std::string(e.what()).substr(0, 300);
  }
  {
    std::lock_guard<std::mutex> lk(MCP_MTX);
    mcp_forget_sessions_locked(id);
  }
  srv["checked"] = (long)now_s();
  return srv;
}
static std::string mcp_format_result(const Val &r) {
  std::string out;
  if (auto content = r.find("content"); content && content->isArr())
    for (auto &part : content->asArr()) {
      const std::string type = mcp_str(part, "type");
      std::string piece;
      if (type == "text") piece = mcp_str(part, "text");
      else if (type == "image" || type == "audio")
        piece = "[" + type + " " + mcp_str(part, "mimeType") + ", " +
                std::to_string(mcp_str(part, "data").size() * 3 / 4) + " Bytes]";
      else if (type == "resource") {
        if (auto res = part.find("resource")) {
          piece = mcp_str(*res, "text");
          if (piece.empty()) piece = "[Ressource " + mcp_str(*res, "uri") + "]";
        }
      } else if (type == "resource_link")
        piece = "[Link " + mcp_str(part, "name") + " " + mcp_str(part, "uri") + "]";
      if (piece.empty()) continue;
      if (!out.empty()) out += "\n";
      out += piece;
    }
  if (out.empty())
    if (auto sc = r.find("structuredContent")) out = mj::dump(*sc);
  if (out.empty()) out = "(leeres Ergebnis)";
  if (auto err = r.find("isError"); err && err->truthy()) out = "Fehler: " + out;
  return mj::utf8_cut(mj::utf8_fix(out), (size_t)TOOL_OUTPUT_LIMIT);
}
static std::string mcp_call(const Val &srv, const std::string &tool, const Val &args,
                            const std::string &bot_id) {
  const std::string id = mcp_str(srv, "id") + "|" + bot_id;
  const std::string secret = mcp_secret(srv);
  const long timeout = std::clamp(envi("MAG_MCP_TIMEOUT_S", 120), 5, 900);
  Val p = Val::object();
  p["name"] = tool;
  p["arguments"] = args.isObj() ? args : Val::object();
  for (int attempt = 0; attempt < 2; attempt++) {
    std::string session;
    bool fresh = false;
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      auto it = MCP_SESSIONS.find(id);
      if (it != MCP_SESSIONS.end()) session = it->second;
      else fresh = true;
    }
    if (fresh) {
      session = mcp_initialize(srv, secret);
      std::lock_guard<std::mutex> lk(MCP_MTX);
      MCP_SESSIONS[id] = session;
    }
    try {
      return mcp_format_result(mcp_rpc(srv, secret, session, "tools/call", p, timeout));
    } catch (const McpSessionGone &) {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      MCP_SESSIONS.erase(id);
    }
  }
  throw std::runtime_error("MCP-Sitzung ließ sich nicht erneuern");
}

// ------------------------------------------------------------- bot binding
// agents.json: bot["plugins"] = {"<server id>": {"approval": "ask"|"auto",
// "off": [tool...], "always": [tool...]}}. Present = enabled for that bot.
static std::string mcp_clean(const std::string &s) {
  std::string o;
  for (char c : s) o += (isalnum((unsigned char)c) || c == '_' || c == '-') ? c : '_';
  return o;
}
static std::string mcp_fn_name(const std::string &sid, const std::string &tool) {
  std::string n = "mcp__" + mcp_clean(sid) + "__" + mcp_clean(tool);
  return n.size() > 64 ? n.substr(0, 64) : n;
}
static bool mcp_list_has(const Val *list, const std::string &x) {
  if (!list || !list->isArr()) return false;
  for (auto &v : list->asArr())
    if (v.asStr() == x) return true;
  return false;
}
static Val mcp_bindings(int bot_idx) {
  std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
  if (bot_idx < 0 || (size_t)bot_idx >= AGENTS.size()) return Val::object();
  auto p = AGENTS[bot_idx].find("plugins");
  return p && p->isObj() ? *p : Val::object();
}
struct McpBotTool {
  std::string fn, server_id, server_name, tool, title;
  bool read_only = false;
  Val def;
};
// The bot's plugin tools in stable order (server order, then tool order).
static std::vector<McpBotTool> mcp_bot_tools(int bot_idx) {
  std::vector<McpBotTool> out;
  Val binds = mcp_bindings(bot_idx);
  if (!binds.isObj() || binds.obj->empty()) return out;
  std::vector<Val> servers;
  {
    std::lock_guard<std::mutex> lk(MCP_MTX);
    mcp_load_locked();
    servers = MCP_SERVERS;
  }
  std::set<std::string> seen;
  for (auto &srv : servers) {
    const std::string sid = mcp_str(srv, "id"), sname = mcp_str(srv, "name");
    const Val *b = binds.find(sid);
    if (!b) continue;
    auto tools = srv.find("tools");
    if (!tools || !tools->isArr()) continue;
    for (auto &t : tools->asArr()) {
      if (out.size() >= MCP_MAX_BOT_TOOLS) return out;
      const std::string tname = mcp_str(t, "name");
      if (mcp_list_has(b->find("off"), tname)) continue;
      McpBotTool bt;
      bt.fn = mcp_fn_name(sid, tname);
      if (!seen.insert(bt.fn).second) continue;
      bt.server_id = sid;
      bt.server_name = sname;
      bt.tool = tname;
      bt.title = mcp_str(t, "title");
      bt.read_only = t.find("read_only") && t.find("read_only")->truthy();
      Val fn = Val::object();
      fn["name"] = bt.fn;
      fn["description"] = "[Plugin " + sname + "] " +
                          mj::utf8_cut(mcp_str(t, "description"),
                                       (size_t)std::clamp(envi("MAG_MCP_TOOL_DESC_BYTES", 700), 120, 700));
      auto schema = t.find("input_schema");
      fn["parameters"] = schema && schema->isObj() ? *schema : Val::object();
      Val def = Val::object();
      def["type"] = "function";
      def["function"] = fn;
      bt.def = def;
      out.push_back(bt);
    }
  }
  return out;
}

// ------------------------------------------------------------- approvals
struct McpApproval {
  std::string id, bot, bot_name, server, server_name, tool, args, decision;
  std::string kind; // "" = plugin tool, "routine", "secret"
  long ts = 0;
};
static std::mutex APPROVAL_MTX;
static std::condition_variable APPROVAL_CV;
static std::map<std::string, McpApproval> APPROVALS; // APPROVAL_MTX
static Val mcp_approval_val(const McpApproval &a) {
  Val o = Val::object();
  o["id"] = a.id;
  o["bot"] = a.bot;
  o["bot_name"] = a.bot_name;
  o["server"] = a.server;
  o["server_name"] = a.server_name;
  o["tool"] = a.tool;
  o["args"] = a.args;
  o["ts"] = a.ts;
  if (!a.kind.empty()) o["kind"] = a.kind;
  return o;
}
static Val mcp_pending_approvals() {
  std::lock_guard<std::mutex> lk(APPROVAL_MTX);
  Val out = Val::array();
  for (auto &kv : APPROVALS)
    if (kv.second.decision.empty()) out.push(mcp_approval_val(kv.second));
  return out;
}
static bool mcp_decide(const std::string &id, const std::string &decision) {
  if (decision != "allow" && decision != "always" && decision != "deny") return false;
  {
    std::lock_guard<std::mutex> lk(APPROVAL_MTX);
    auto it = APPROVALS.find(id);
    if (it == APPROVALS.end() || !it->second.decision.empty()) return false;
    it->second.decision = decision;
  }
  APPROVAL_CV.notify_all();
  return true;
}
// Blocks the bot's turn until the owner decides, the turn is stopped or
// MAG_MCP_APPROVAL_S (default 15 min) pass.
static std::string mcp_await_approval(const McpApproval &req) {
  {
    std::lock_guard<std::mutex> lk(APPROVAL_MTX);
    APPROVALS[req.id] = req;
  }
  Val e = Val::object();
  e["type"] = "mcp_approval";
  e["bot"] = req.bot;
  e["approval"] = mcp_approval_val(req);
  publish(mj::dump(e));
  const auto until = steady_clock::now() + seconds(std::clamp(envi("MAG_MCP_APPROVAL_S", 900), 5, 86400));
  auto next_ping = steady_clock::now();
  std::string decision;
  {
    std::unique_lock<std::mutex> lk(APPROVAL_MTX);
    for (;;) {
      auto &cur = APPROVALS[req.id];
      if (!cur.decision.empty()) { decision = cur.decision; break; }
      if (stop_flag().load() || SHUTDOWN.load()) { decision = "stopped"; break; }
      if (steady_clock::now() >= until) { decision = "timeout"; break; }
      if (steady_clock::now() >= next_ping) {
        next_ping = steady_clock::now() + seconds(20);
        lk.unlock();
        Val s = Val::object();
        s["type"] = "bot_status";
        s["bot"] = req.bot;
        s["name"] = req.bot_name;
        s["text"] = "Wartet auf Freigabe: " + req.server_name + " · " + req.tool;
        publish(mj::dump(s));
        lk.lock();
        continue;
      }
      APPROVAL_CV.wait_for(lk, milliseconds(250));
    }
    APPROVALS.erase(req.id);
  }
  Val d = Val::object();
  d["type"] = "mcp_approval_done";
  d["bot"] = req.bot;
  d["id"] = req.id;
  d["decision"] = decision;
  publish(mj::dump(d));
  return decision;
}
static void mcp_bind_always(const std::string &bot_id, const std::string &sid,
                            const std::string &tool) {
  std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
  int idx = agent_index(bot_id);
  if (idx < 0) return;
  Val &plugins = AGENTS[idx]["plugins"];
  Val *b = plugins.find(sid);
  if (!b) return;
  Val &always = (*b)["always"];
  if (!always.isArr()) always = Val::array();
  if (!mcp_list_has(&always, tool)) always.push(Val(tool));
  save_agents();
}

static bool is_mcp_tool(const std::string &name) { return name.rfind("mcp__", 0) == 0; }
static std::string mcp_dispatch_tool(int bot_idx, const std::string &name, const Val &args) {
  const std::string bot_id = bot_field(bot_idx, "id"), bot_name = bot_field(bot_idx, "name", "Bot");
  McpBotTool hit;
  bool found = false;
  for (auto &t : mcp_bot_tools(bot_idx))
    if (t.fn == name) { hit = t; found = true; break; }
  if (!found)
    throw std::runtime_error("Plugin-Werkzeug ist für diesen Bot nicht aktiviert: " + name);
  Val srv;
  {
    std::lock_guard<std::mutex> lk(MCP_MTX);
    Val *s = mcp_find_locked(hit.server_id);
    if (!s) throw std::runtime_error("Plugin wurde entfernt.");
    srv = *s;
  }
  if (OFFLINE.load() && !mcp_private_host(mcp_url_host(mcp_str(srv, "url"))))
    throw std::runtime_error("Offline-Modus: Plugin " + hit.server_name + " ist nicht erreichbar.");
  Val binds = mcp_bindings(bot_idx);
  const Val *b = binds.find(hit.server_id);
  const bool auto_ok = (b && mcp_str(*b, "approval") == "auto") || hit.read_only ||
                       (b && mcp_list_has(b->find("always"), hit.tool));
  if (!auto_ok) {
    McpApproval req;
    req.id = token_hex(8);
    req.bot = bot_id;
    req.bot_name = bot_name;
    req.server = hit.server_id;
    req.server_name = hit.server_name;
    req.tool = hit.tool;
    req.args = mj::utf8_cut(mj::dump(args), 4000);
    req.ts = (long)now_s();
    const std::string d = mcp_await_approval(req);
    if (d == "stopped") throw std::runtime_error("Abgebrochen, bevor der Aufruf freigegeben wurde.");
    if (d == "timeout")
      return "Keine Freigabe erhalten. Der Aufruf von " + hit.tool +
             " wurde nicht ausgeführt; frage den Nutzer, ob er ihn noch möchte.";
    if (d == "deny")
      return "Der Nutzer hat den Aufruf von " + hit.tool +
             " abgelehnt. Nicht erneut versuchen, außer er bittet ausdrücklich darum.";
    if (d == "always") mcp_bind_always(bot_id, hit.server_id, hit.tool);
  }
  return mcp_call(srv, hit.tool, args, bot_id);
}
// Stable per binding (part of the cached system prompt): the room's guide for
// preset servers plus the server's own initialize instructions.
static std::string mcp_prompt_notes(int bot_idx) {
  Val binds = mcp_bindings(bot_idx);
  if (!binds.isObj() || binds.obj->empty()) return "";
  std::vector<Val> servers;
  {
    std::lock_guard<std::mutex> lk(MCP_MTX);
    mcp_load_locked();
    servers = MCP_SERVERS;
  }
  std::string out;
  for (auto &srv : servers) {
    if (!binds.find(mcp_str(srv, "id"))) continue;
    std::string how;
    if (auto si = srv.find("server")) how = mcp_str(*si, "instructions");
    const std::string guide = mcp_str(srv, "guide");
    if (guide.empty() && how.empty()) continue;
    out += "\n[Plugin " + mcp_str(srv, "name") + "]";
    if (!guide.empty()) out += "\n" + guide;
    if (!how.empty()) out += "\nHinweise des Servers: " + mj::utf8_cut(how, 1500);
  }
  return out.empty() ? "" : "\n\nPLUGINS" + out + "\n";
}
static std::string mcp_step_label(const std::string &fn) {
  // mcp__<server>__<tool>
  auto rest = fn.substr(5);
  auto sep = rest.find("__");
  if (sep == std::string::npos) return "Nutzt ein Plugin";
  const std::string sid = rest.substr(0, sep), tool = rest.substr(sep + 2);
  std::string name = sid;
  {
    std::lock_guard<std::mutex> lk(MCP_MTX);
    if (Val *s = mcp_find_locked(sid)) name = mcp_str(*s, "name");
  }
  return name + " · " + tool;
}
