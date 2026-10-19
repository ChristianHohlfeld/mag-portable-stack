// /api/plugins: the owner's MCP server catalog and the per-bot bindings
// (see mcp.hpp). Included by routes.hpp; every path here is owner-only in
// access.hpp. Responses never contain a secret, only has_secret.
#pragma once

struct McpPreset {
  const char *preset, *name, *url, *auth, *hint, *token_hint;
  bool token_required;
  const char *guide = "";
  bool auto_approve = false;
  std::vector<const char *> off = {}; // tools switched off on first binding
  bool auto_bind = false;             // connect and attach to every bot, no click
};
static const McpPreset MCP_PRESETS[] = {
    {"digitalisierungsplanung", "Digitalisierungsplanung",
     "https://accounts.digitalisierungsplanung.de/mcp/free", "none",
     "Prozessbilder: Die KI modelliert den Prozess und antwortet mit einem öffentlichen Link zum Diagramm.",
     "", false,
     "Wünscht jemand ein Prozessbild, Prozessdiagramm, einen Ablauf oder Workflow, "
     "erstelle es vollständig mit diesem Plugin: zustand_contract und zustand_snapshot lesen, "
     "dann EIN zustand_apply mit expectedRevision = snapshot.revision und allen Ops: setName, "
     "jeder Schritt als addState mit eindeutiger id und kurzem Titel, setInitial, jede Verbindung "
     "als addTransition mit sprechendem label (Entscheidungen als mehrere Übergänge, Freigaben "
     "durch Menschen mit decision \"human\", Ende als eigener Schritt), zuletzt {\"op\":\"layout\"}. "
     "Steht im Snapshot schon ein anderer Prozess und es geht um ein neues Bild, entferne zuerst "
     "dessen Schritte mit removeState; Änderungswünsche am selben Bild nur patchen. Danach "
     "zustand_share mit passendem title aufrufen und genau den gelieferten url als Markdown-Link "
     "antworten, z. B. [Prozess ansehen](url), mit 1-2 Sätzen zum Inhalt. Nie einen Link "
     "erfinden; schlägt ein Schritt fehl, den Fehler nennen.",
     true,
     {"zustand_export", "zustand_export_svg", "zustand_export_pdf", "zustand_appearance",
      "zustand_set_appearance"},
     true},
    {"github", "GitHub", "https://api.githubcopilot.com/mcp/", "bearer",
     "Issues, Pull Requests, Code-Suche und Repos.", "ghp_… oder github_pat_…", true},
    {"context7", "Context7", "https://mcp.context7.com/mcp", "none",
     "Aktuelle Doku und Codebeispiele zu Bibliotheken.", "", false},
    {"deepwiki", "DeepWiki", "https://mcp.deepwiki.com/mcp", "none",
     "Fragen zu öffentlichen GitHub-Repos.", "", false},
    {"huggingface", "Hugging Face", "https://huggingface.co/mcp", "bearer",
     "Modelle, Datensätze, Spaces und Papers.", "hf_… (optional)", false},
    {"linear", "Linear", "https://mcp.linear.app/mcp", "bearer",
     "Issues, Projekte und Zyklen.", "lin_api_…", true},
    {"stripe", "Stripe", "https://mcp.stripe.com", "bearer",
     "Zahlungen, Kunden und Abos.", "rk_… (eingeschränkter Schlüssel)", true},
    {"cloudflare-docs", "Cloudflare Docs", "https://docs.mcp.cloudflare.com/mcp", "none",
     "Dokumentation zu Cloudflare.", "", false},
};

static const McpPreset *mcp_preset(const std::string &key) {
  for (auto &p : MCP_PRESETS)
    if (key == p.preset) return &p;
  return nullptr;
}
// Tests point MAG_ROOM_MCP_DP_URL at a loopback fake; production leaves it unset.
static std::string mcp_preset_url(const McpPreset &p) {
  if (std::string(p.preset) == "digitalisierungsplanung") {
    const std::string over = env("MAG_ROOM_MCP_DP_URL", "");
    if (!over.empty()) return over;
  }
  return p.url;
}
static bool mcp_token_ok(const std::string &t) {
  if (t.size() < 8 || t.size() > 4096) return false;
  for (unsigned char c : t)
    if (c <= 0x20 || c >= 0x7f) return false;
  return true;
}
static bool mcp_header_ok(const std::string &h) {
  if (h.empty() || h.size() > 64) return false;
  for (unsigned char c : h)
    if (!(isalnum(c) || c == '-')) return false;
  std::string k = h;
  std::transform(k.begin(), k.end(), k.begin(), ::tolower);
  for (const char *bad : {"host", "content-type", "content-length", "accept",
                          "connection", "transfer-encoding", "mcp-session-id",
                          "mcp-protocol-version", "cookie"})
    if (k == bad) return false;
  return true;
}
static std::string mcp_slug(const std::string &name) {
  std::string s;
  for (unsigned char c : name) {
    if (isalnum(c)) s += (char)tolower(c);
    else if (!s.empty() && s.back() != '-') s += '-';
    if (s.size() >= 24) break;
  }
  while (!s.empty() && s.back() == '-') s.pop_back();
  if (s.empty()) s = "mcp";
  std::string id = s;
  for (int n = 2; mcp_find_locked(id); n++) id = s + "-" + std::to_string(n);
  return id;
}
static long mcp_tool_tokens(const Val &srv) {
  size_t bytes = 0;
  if (auto t = srv.find("tools"); t && t->isArr())
    for (auto &x : t->asArr()) bytes += mj::dump(x).size();
  return (long)(bytes / 4);
}
static Val mcp_public_server(const Val &srv) {
  Val o = Val::object();
  for (const char *k : {"id", "name", "url", "auth", "header", "preset", "status", "error"})
    o[k] = mcp_str(srv, k);
  o["checked"] = srv.find("checked") ? srv.find("checked")->asNum() : 0;
  if (auto si = srv.find("server")) o["server"] = *si;
  o["has_secret"] = !mcp_secret(srv).empty();
  o["context_tokens"] = mcp_tool_tokens(srv);
  Val tools = Val::array();
  if (auto t = srv.find("tools"); t && t->isArr())
    for (auto &x : t->asArr()) {
      Val v = Val::object();
      v["name"] = mcp_str(x, "name");
      v["title"] = mcp_str(x, "title");
      v["description"] = mj::utf8_cut(mcp_str(x, "description"), 240);
      v["read_only"] = x.find("read_only") && x.find("read_only")->truthy();
      tools.push(v);
    }
  o["tools"] = tools;
  return o;
}
static bool mcp_github_connected() {
  bool ok = false;
  return !trim(read_file(STATE_DIR + "/connectors/github.token", &ok)).empty() && ok;
}
static std::string mcp_plugins_json() {
  Val out = Val::object();
  Val servers = Val::array();
  {
    std::lock_guard<std::mutex> lk(MCP_MTX);
    mcp_load_locked();
    for (auto &s : MCP_SERVERS) servers.push(mcp_public_server(s));
  }
  out["servers"] = servers;
  Val binds = Val::object();
  {
    std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
    for (size_t i = 0; i < AGENTS.size(); i++) {
      auto p = AGENTS[i].find("plugins");
      binds[bot_field((int)i, "id")] = p && p->isObj() ? *p : Val::object();
    }
  }
  out["bindings"] = binds;
  Val cat = Val::array();
  for (auto &p : MCP_PRESETS) {
    Val c = Val::object();
    c["preset"] = p.preset;
    c["name"] = p.name;
    c["url"] = p.url;
    c["auth"] = p.auth;
    c["hint"] = p.hint;
    c["token_hint"] = p.token_hint;
    c["token_required"] = p.token_required;
    c["auto_approve"] = p.auto_approve;
    c["auto_bind"] = p.auto_bind;
    cat.push(c);
  }
  out["catalog"] = cat;
  out["github_connector"] = mcp_github_connected();
  out["max_tools"] = (long)MCP_MAX_BOT_TOOLS;
  out["approvals"] = mcp_pending_approvals();
  return mj::dump(out);
}
static void plugins_event() {
  Val e = Val::object();
  e["type"] = "plugins";
  publish(mj::dump(e));
}
static bool mcp_bind_locked(const std::string &bot, const std::string &sid,
                            bool on, const Val *approval, const Val *off) {
  int idx = agent_index(bot);
  if (idx < 0) return false;
  Val &plugins = AGENTS[idx]["plugins"];
  if (!plugins.isObj()) plugins = Val::object();
  if (!on) {
    drop_key(plugins, sid);
    if (plugins.obj->empty()) drop_key(AGENTS[idx], "plugins");
    return true;
  }
  Val *b = plugins.find(sid);
  if (!b) {
    Val nb = Val::object();
    nb["approval"] = "ask";
    nb["off"] = Val::array();
    nb["always"] = Val::array();
    plugins[sid] = nb;
    b = plugins.find(sid);
  }
  if (approval) {
    const std::string a = approval->asStr();
    if (a == "ask" || a == "auto") (*b)["approval"] = a;
    if (a == "ask") (*b)["always"] = Val::array();
  }
  if (off && off->isArr()) {
    Val list = Val::array();
    for (auto &x : off->asArr())
      if (!x.asStr().empty() && x.asStr().size() <= 128 && list.arr->size() < 200)
        list.push(Val(x.asStr()));
    (*b)["off"] = list;
  }
  return true;
}
static void mcp_bind_preset_bots_locked(const std::string &sid, const McpPreset *pre,
                                        const std::string &only_bot = "") {
  const Val autov("auto");
  Val off = Val::array();
  if (pre)
    for (const char *t : pre->off) off.push(Val(t));
  const Val *ap = pre && pre->auto_approve ? &autov : nullptr;
  const Val *op = off.size() ? &off : nullptr;
  if (!only_bot.empty()) {
    mcp_bind_locked(only_bot, sid, true, ap, op);
    return;
  }
  for (size_t i = 0; i < AGENTS.size(); i++)
    mcp_bind_locked(bot_field((int)i, "id"), sid, true, ap, op);
}
static Val mcp_probe_store(const std::string &sid);
static std::string mcp_sid_for_preset_locked(const std::string &preset) {
  mcp_load_locked();
  for (auto &s : MCP_SERVERS)
    if (mcp_str(s, "preset") == preset) return mcp_str(s, "id");
  return "";
}
static bool mcp_server_ready_locked(const std::string &sid) {
  Val *s = mcp_find_locked(sid);
  if (!s || mcp_str(*s, "status") != "ok") return false;
  auto t = s->find("tools");
  return t && t->isArr() && !t->arr->empty();
}
static bool mcp_any_bot_bound_locked(const std::string &sid) {
  for (auto &a : AGENTS) {
    auto p = a.find("plugins");
    if (p && p->isObj() && p->find(sid)) return true;
  }
  return false;
}
// Bind every bot if nobody has this server yet. Owner unbind of one bot is
// kept: some-bound means we do not reattach the others.
static void mcp_auto_bind_if_unbound(const std::string &sid, const McpPreset &p) {
  bool wrote = false;
  {
    std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
    if (!mcp_any_bot_bound_locked(sid)) {
      mcp_bind_preset_bots_locked(sid, &p);
      save_agents();
      wrote = true;
    }
  }
  if (wrote) {
    plugins_event();
    agents_event();
    std::cerr << "MAG plugins: auto " << p.name << " bound to all bots\n";
  }
}
// Connect auth-none presets that should be on every bot (Digitalisierungsplanung).
// MAG_ROOM_MCP_AUTO=0 skips this (tests). A leftover servers.json with no
// bindings (catalog-only connect, or crash after probe) still attaches every
// bot. Owner unbind of one bot is kept. Later bots pick it up via
// mcp_bind_new_bot. A failed probe (boot before the network is up) is retried
// after 30s.
static void mcp_ensure_auto_plugins() {
  if (env("MAG_ROOM_MCP_AUTO", "1") == "0") return;
  static std::mutex mu;
  static std::set<std::string> done;
  static std::map<std::string, long> fail_s;
  std::lock_guard<std::mutex> g(mu);
  const long now = (long)now_s();
  for (auto &p : MCP_PRESETS) {
    if (!p.auto_bind || done.count(p.preset)) continue;
    std::string sid;
    bool ready = false;
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      sid = mcp_sid_for_preset_locked(p.preset);
      ready = !sid.empty() && mcp_server_ready_locked(sid);
    }
    if (!sid.empty() && ready) {
      mcp_auto_bind_if_unbound(sid, p);
      done.insert(p.preset);
      continue;
    }
    if (fail_s[p.preset] && now - fail_s[p.preset] < 30) continue;
    if (!sid.empty()) {
      Val probed = mcp_probe_store(sid);
      if (mcp_str(probed, "status") != "ok") {
        fail_s[p.preset] = now;
        std::cerr << "MAG plugins: auto " << p.name << ": " << mcp_str(probed, "error")
                  << "\n";
        continue;
      }
      mcp_auto_bind_if_unbound(sid, p);
      done.insert(p.preset);
      continue;
    }
    Val srv = Val::object();
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      srv["id"] = mcp_slug(p.name);
    }
    srv["name"] = p.name;
    srv["url"] = mcp_preset_url(p);
    srv["auth"] = p.auth;
    srv["preset"] = p.preset;
    if (*p.guide) srv["guide"] = p.guide;
    Val probed = mcp_probe(srv);
    if (mcp_str(probed, "status") != "ok") {
      fail_s[p.preset] = now;
      std::cerr << "MAG plugins: auto " << p.name << ": " << mcp_str(probed, "error")
                << "\n";
      continue;
    }
    sid = mcp_str(probed, "id");
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      if (!mcp_find_locked(sid)) {
        MCP_SERVERS.push_back(probed);
        mcp_save_locked();
      }
    }
    mcp_auto_bind_if_unbound(sid, p);
    done.insert(p.preset);
  }
}
static void mcp_auto_once() { mcp_ensure_auto_plugins(); }
static void mcp_bind_new_bot(const std::string &bot) {
  if (bot.empty() || env("MAG_ROOM_MCP_AUTO", "1") == "0") return;
  mcp_auto_once();
  std::vector<std::pair<std::string, const McpPreset *>> want;
  {
    std::lock_guard<std::mutex> lk(MCP_MTX);
    mcp_load_locked();
    for (auto &s : MCP_SERVERS) {
      const McpPreset *pre = mcp_preset(mcp_str(s, "preset"));
      if (pre && pre->auto_bind) want.push_back({mcp_str(s, "id"), pre});
    }
  }
  if (want.empty()) return;
  std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
  for (auto &w : want) mcp_bind_preset_bots_locked(w.first, w.second, bot);
  save_agents();
}
// Probes outside the lock and stores the result if the server still exists.
static Val mcp_probe_store(const std::string &sid) {
  Val srv;
  {
    std::lock_guard<std::mutex> lk(MCP_MTX);
    Val *s = mcp_find_locked(sid);
    if (!s) return Val();
    srv = *s;
  }
  Val probed = mcp_probe(srv);
  std::lock_guard<std::mutex> lk(MCP_MTX);
  if (Val *s = mcp_find_locked(sid)) {
    *s = probed;
    mcp_save_locked();
  }
  return probed;
}

static bool mcp_route(int fd, Req &r, const std::string &m, const std::string &path) {
  if (path != "/api/plugins" && path.rfind("/api/plugins/", 0) != 0) return false;
  Val body = Val::object();
  if (!r.body.empty() && (!mj::parse(r.body, body) || !body.isObj())) {
    error_json(fd, 400, "Ungültige Anfrage");
    return true;
  }
  auto s = [&](const char *k) { return trim(mcp_str(body, k)); };
  const std::string rest = path.size() > 13 ? path.substr(13) : "";

  if (path == "/api/plugins" && m == "GET") {
    mcp_auto_once();
    send_json(fd, 200, mcp_plugins_json());
    return true;
  }
  if (path == "/api/plugins" && m == "POST") {
    const McpPreset *pre = mcp_preset(s("preset"));
    std::string name = s("name"), url = s("url"), auth = s("auth"), header = s("header");
    const std::string token = s("token"), bot = s("bot");
    if (pre) {
      if (name.empty()) name = pre->name;
      url = mcp_preset_url(*pre);
      auth = pre->auth;
      if (s("auth") == "connector" && std::string(pre->preset) == "github") auth = "connector";
      if (pre->token_required && auth != "connector" && token.empty()) {
        error_json(fd, 400, std::string(pre->name) + " braucht ein Token.");
        return true;
      }
    }
    if (auth.empty()) auth = token.empty() ? "none" : "bearer";
    name = mj::utf8_cut(name, 60);
    if (name.empty()) { error_json(fd, 400, "Name fehlt"); return true; }
    if (auth != "none" && auth != "bearer" && auth != "header" && auth != "connector") {
      error_json(fd, 400, "Unbekannte Anmeldung");
      return true;
    }
    if (auth == "connector" && !mcp_github_connected()) {
      error_json(fd, 400, "GitHub-Connector ist nicht verbunden.");
      return true;
    }
    if (auth == "header" && !mcp_header_ok(header)) {
      error_json(fd, 400, "Header-Name ungültig");
      return true;
    }
    if (auth == "bearer" && token.empty() && !pre) {
      error_json(fd, 400, "Token fehlt");
      return true;
    }
    if (auth == "bearer" && token.empty()) auth = "none";
    if ((auth == "bearer" || auth == "header") && !mcp_token_ok(token)) {
      error_json(fd, 400, "Token ungültig");
      return true;
    }
    if (const std::string bad = mcp_check_url(url); !bad.empty()) {
      error_json(fd, 400, bad);
      return true;
    }
    if (!bot.empty()) {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      if (agent_index(bot) < 0) { error_json(fd, 404, "Bot nicht gefunden"); return true; }
    }
    Val srv = Val::object();
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      mcp_load_locked();
      if (MCP_SERVERS.size() >= 24) { error_json(fd, 409, "Höchstens 24 Plugins"); return true; }
      srv["id"] = mcp_slug(name);
    }
    const std::string sid = mcp_str(srv, "id");
    srv["name"] = name;
    srv["url"] = url;
    srv["auth"] = auth;
    if (auth == "header") srv["header"] = header;
    if (pre) srv["preset"] = pre->preset;
    if (pre && *pre->guide) srv["guide"] = pre->guide;
    if ((auth == "bearer" || auth == "header") && !mcp_write_secret(sid, token)) {
      error_json(fd, 500, "Token konnte nicht gespeichert werden");
      return true;
    }
    Val probed = mcp_probe(srv);
    if (mcp_str(probed, "status") != "ok") {
      std::remove(mcp_secret_path(sid).c_str());
      {
        std::lock_guard<std::mutex> lk(MCP_MTX);
        mcp_forget_sessions_locked(sid);
      }
      error_json(fd, 502, mcp_str(probed, "error"));
      return true;
    }
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      if (mcp_find_locked(sid)) probed["id"] = sid + "-" + token_hex(2);
      MCP_SERVERS.push_back(probed);
      mcp_save_locked();
    }
    if (!bot.empty() || (pre && pre->auto_bind)) {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      mcp_bind_preset_bots_locked(mcp_str(probed, "id"), pre, bot);
      save_agents();
    }
    plugins_event();
    agents_event();
    Val out = Val::object();
    out["ok"] = true;
    out["server"] = mcp_public_server(probed);
    send_json(fd, 200, mj::dump(out));
    return true;
  }
  if (rest.rfind("approvals/", 0) == 0 && m == "POST") {
    if (!mcp_decide(rest.substr(10), s("decision"))) {
      error_json(fd, 404, "Freigabe nicht mehr offen");
      return true;
    }
    send_json(fd, 200, "{\"ok\":true}");
    return true;
  }
  if (rest.rfind("bots/", 0) == 0 && m == "POST") {
    const std::string bot = rest.substr(5), sid = s("server");
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      if (!mcp_find_locked(sid)) { error_json(fd, 404, "Plugin nicht gefunden"); return true; }
    }
    auto on = body.find("on");
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      if (!mcp_bind_locked(bot, sid, !on || on->truthy(), body.find("approval"),
                           body.find("off"))) {
        error_json(fd, 404, "Bot nicht gefunden");
        return true;
      }
      save_agents();
    }
    plugins_event();
    agents_event();
    Val out = Val::object();
    out["ok"] = true;
    int idx;
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      idx = agent_index(bot);
    }
    out["tools"] = (long)mcp_bot_tools(idx).size();
    send_json(fd, 200, mj::dump(out));
    return true;
  }
  const std::string sid = rest.substr(0, rest.find('/'));
  const std::string action = rest.size() > sid.size() ? rest.substr(sid.size() + 1) : "";
  if (!mcp_safe_id(sid)) { error_json(fd, 404, "Plugin nicht gefunden"); return true; }
  {
    std::lock_guard<std::mutex> lk(MCP_MTX);
    if (!mcp_find_locked(sid)) { error_json(fd, 404, "Plugin nicht gefunden"); return true; }
  }
  if (action == "refresh" && m == "POST") {
    Val probed = mcp_probe_store(sid);
    plugins_event();
    Val out = Val::object();
    out["ok"] = mcp_str(probed, "status") == "ok";
    out["server"] = mcp_public_server(probed);
    send_json(fd, 200, mj::dump(out));
    return true;
  }
  if (action.empty() && m == "PUT") {
    const std::string name = mj::utf8_cut(s("name"), 60), token = s("token");
    std::string auth;
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      Val *srv = mcp_find_locked(sid);
      auth = mcp_str(*srv, "auth");
      if (!name.empty()) (*srv)["name"] = name;
      mcp_save_locked();
    }
    if (!token.empty()) {
      if (auth != "bearer" && auth != "header") {
        error_json(fd, 400, "Dieses Plugin nutzt kein eigenes Token");
        return true;
      }
      if (!mcp_token_ok(token) || !mcp_write_secret(sid, token)) {
        error_json(fd, 400, "Token ungültig");
        return true;
      }
      {
        std::lock_guard<std::mutex> lk(MCP_MTX);
        mcp_forget_sessions_locked(sid);
      }
      mcp_probe_store(sid);
    }
    plugins_event();
    agents_event();
    Val srv;
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      srv = *mcp_find_locked(sid);
    }
    Val out = Val::object();
    out["ok"] = true;
    out["server"] = mcp_public_server(srv);
    send_json(fd, 200, mj::dump(out));
    return true;
  }
  if (action.empty() && m == "DELETE") {
    {
      std::lock_guard<std::mutex> lk(MCP_MTX);
      MCP_SERVERS.erase(std::remove_if(MCP_SERVERS.begin(), MCP_SERVERS.end(),
                                       [&](const Val &v) { return mcp_str(v, "id") == sid; }),
                        MCP_SERVERS.end());
      mcp_forget_sessions_locked(sid);
      mcp_save_locked();
    }
    std::remove(mcp_secret_path(sid).c_str());
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      for (auto *list : {&AGENTS, &PARKED})
        for (auto &a : *list)
          if (Val *p = a.find("plugins"); p && p->isObj()) {
            drop_key(*p, sid);
            if (p->obj->empty()) drop_key(a, "plugins");
          }
      save_agents();
    }
    {
      std::lock_guard<std::mutex> lk(APPROVAL_MTX);
      for (auto &kv : APPROVALS)
        if (kv.second.server == sid && kv.second.decision.empty()) kv.second.decision = "deny";
    }
    APPROVAL_CV.notify_all();
    plugins_event();
    agents_event();
    send_json(fd, 200, "{\"ok\":true}");
    return true;
  }
  error_json(fd, 405, "Methode nicht erlaubt");
  return true;
}
