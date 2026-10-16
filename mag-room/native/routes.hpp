// Room and authenticated VM bridge routing. Included after server helpers.
static void error_json(int fd, int code, const std::string &message) {
  Val e = Val::object();
  e["error"] = message;
  send_json(fd, code, mj::dump(e));
}
static std::vector<std::string> selected_ids(const Val &body) {
  std::vector<std::string> ids;
  std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
  if (!SINGLE_BOT.empty()) {  // exactly this one bot answers, whatever the client sent
    if (agent_index(SINGLE_BOT) >= 0)
      ids.push_back(SINGLE_BOT);
    return ids;
  }
  auto selected = body.find("bots");
  if (selected && selected->isArr()) {
    for (const auto &id : selected->asArr())
      if (agent_index(id.asStr()) >= 0)
        ids.push_back(id.asStr());
  } else
    for (auto &a : AGENTS)
      ids.push_back(a.find("id")->asStr());
  return ids;
}
static void frontend_event() {
  Val e = Val::object();
  e["type"] = "frontend";
  e["version"] = frontend_version();
  publish(mj::dump(e));
}
static void agents_event() {
  Val e = Val::object();
  e["type"] = "agents";
  publish(mj::dump(e));
}
static void route(int fd, Req &r, bool bridge = false) {
  const std::string &p = r.path;
  const std::string &m = r.method;
  if (bridge) {
    std::string token = qparam(r.query, "token"), expected = bridge_token();
    auto auth = r.hdrs.find("authorization");
    if (token.empty() && auth != r.hdrs.end() &&
        auth->second.rfind("Bearer ", 0) == 0)
      token = auth->second.substr(7);
    auto cookie = r.hdrs.find("cookie");
    if (token.empty() && cookie != r.hdrs.end()) {
      std::string c = cookie->second;
      auto pos = c.find("mag_bridge=");
      if (pos != std::string::npos) {
        token = c.substr(pos + 11);
        auto end = token.find(';');
        if (end != std::string::npos)
          token.resize(end);
      }
    }
    if (expected.empty() || token != expected) {
      error_json(fd, 401, "Bridge token required");
      return;
    }
    if (m == "GET" && p == "/tool/mag-browser") {
      bool ok;
      auto binary = read_file(
          env("MAG_BROWSER_BINARY", "/opt/mag-stack/current/bin/mag-browser"),
          &ok);
      if (!ok)
        error_json(fd, 404, "Browser binary missing");
      else
        send_response(fd, 200, "application/octet-stream", binary);
      return;
    }
    if (m == "POST" && p == "/ingest") {
      auto bot = qparam(r.query, "bot");
      auto idx = agent_index(bot);
      auto sender = idx < 0 ? "VM" : bot_field(idx, "name");
      Val f = add_file(qparam(r.query, "name"), r.body, sender);
      auto src = qparam(r.query, "src");
      if (!src.empty() && src[0] == '/' && idx >= 0)
        set_file_origin(f.find("stored")->asStr(), bot_field(idx, "vm"), src, &f);
      append_room("assistant", sender, "", bot, &f);
      emit_file(bot, f);
      send_json(fd, 201, mj::dump(f));
      return;
    }
    if (p.rfind("/files/", 0) == 0)
      r.path = "/api/file/" + p.substr(7);
    // Only the public UI/file routes and frontend publishing are exposed to
    // guests.
    if (r.path == "/" && m == "GET") {
      send_response(
          fd, 200, "text/html; charset=utf-8", current_frontend(),
          {{"Set-Cookie",
            "mag_bridge=" + expected + "; Path=/; HttpOnly; SameSite=Strict"}});
      return;
    }
    if (r.path != "/api/frontend" && r.path != "/api/events" &&
        r.path != "/api/state" && r.path != "/api/bots" &&
        r.path != "/api/files" && r.path.rfind("/api/file/", 0) != 0 &&
        r.path.rfind("/static/", 0) != 0) {
      error_json(fd, 403, "Route not available on VM bridge");
      return;
    }
  } else {
    auto origin = r.hdrs.find("origin");
    auto host = r.hdrs.find("host");
    if (origin != r.hdrs.end() && host != r.hdrs.end() &&
        origin->second != "https://" + host->second &&
        origin->second != "http://" + host->second) {
      error_json(fd, 403, "Wrong origin");
      return;
    }
    if (m != "GET" && m != "HEAD" && m != "OPTIONS") {
      // Accept the page token, or a browser-attested same-origin request
      // (Sec-Fetch-Site is set by the browser and cannot be forged by
      // another site). Scripts without either are rejected.
      auto tok = r.hdrs.find("x-mag-token");
      auto sfs = r.hdrs.find("sec-fetch-site");
      bool same_site = sfs != r.hdrs.end() && sfs->second == "same-origin";
      if (!same_site &&
          (tok == r.hdrs.end() || !ct_eq(tok->second, ui_token()))) {
        error_json(fd, 403, "UI token required (Seite neu laden)");
        return;
      }
    }
  }
  const std::string path = r.path;
  if (m == "GET" && (path == "/" || path == "/index.html")) {
    send_text(fd, 200, current_frontend(), "text/html; charset=utf-8");
    return;
  }
  // Legacy /bot/<slug>/ links (stable Tailnet path per bot) redirect to the
  // main room; no per-bot Tailscale proxy, token or VM re-provision.
  // Deleting the agent removes the route immediately, even if a guest disk
  // is left behind after a failed teardown.
  if (m == "GET" && path.rfind("/bot/", 0) == 0) {
    std::string id = path.substr(5);
    if (!id.empty() && id.back() == '/') id.pop_back();
    bool safe = id.size() >= 1 && id.size() <= 32 &&
                id[0] >= 'a' && id[0] <= 'z';
    for (char c : id)
      if (!((c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') || c == '-')) safe = false;
    if (!safe || agent_index(id) < 0) {
      send_response(fd, 404, "text/plain; charset=utf-8",
                    "Bot nicht vorhanden oder bereits entfernt.\n",
                    {{"Cache-Control", "no-store"}});
      return;
    }
    // One interface only: the old remote-desktop view is gone; the link
    // opens the main room with this bot's browser panel selected.
    send_response(fd, 302, "text/plain; charset=utf-8", "",
                  {{"Location", "/?bot=" + id}, {"Cache-Control", "no-store"}});
    return;
  }
  if (m == "GET" && (path == "/favicon.ico" || path == "/favicon.svg" ||
                     path == "/apple-touch-icon.png")) {
    bool ok;
    std::string data = read_file(STATIC_DIR + path, &ok);
    if (!ok)
      error_json(fd, 404, "Unknown asset");
    else
      send_response(fd, 200, mime_for(path), data,
                    {{"Cache-Control", "public, max-age=86400"}});
    return;
  }
  // Connectors: secrets stay server-side (STATE_DIR/connectors, 0600) and
  // are never returned to the browser or written to the repo.
  if (path == "/api/connectors" && m == "GET") {
    Val out = Val::object();
    for (const char *name : {"github"}) {
      bool ok = false;
      std::string tok = read_file(STATE_DIR + "/connectors/" + name + ".token", &ok);
      Val c = Val::object();
      c["connected"] = ok && !trim(tok).empty();
      out[name] = c;
    }
    send_json(fd, 200, mj::dump(out));
    return;
  }
  if (path == "/api/connectors/github" && (m == "POST" || m == "DELETE")) {
    const std::string dir = STATE_DIR + "/connectors";
    const std::string file = dir + "/github.token";
    if (m == "DELETE") {
      std::remove(file.c_str());
      send_json(fd, 200, "{\"ok\":true,\"connected\":false}");
      return;
    }
    Val body = Val::object();
    std::string tok;
    if (!r.body.empty() && mj::parse(r.body, body) && body.isObj() && body.find("token"))
      tok = trim(body.find("token")->asStr());
    bool valid = tok.size() >= 20 && tok.size() <= 255;
    for (char c : tok)
      if (!(isalnum((unsigned char)c) || c == '_')) valid = false;
    if (!valid) {
      error_json(fd, 400, "Ungültiges GitHub-Token (ghp_…/github_pat_…)");
      return;
    }
    std::filesystem::create_directories(dir);
    chmod(dir.c_str(), 0700);
    std::string tmp = file + ".tmp";
    int tfd = open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    bool wrote = tfd >= 0 && write(tfd, tok.data(), tok.size()) == (ssize_t)tok.size();
    if (tfd >= 0) close(tfd);
    if (!wrote || rename(tmp.c_str(), file.c_str()) != 0) {
      error_json(fd, 500, "Token konnte nicht gespeichert werden");
      return;
    }
    send_json(fd, 200, "{\"ok\":true,\"connected\":true}");
    return;
  }
  if (m == "GET" && path == "/healthz") {
    send_text(fd, 200, "ok\n");
    return;
  }
  // /readyz: the room itself is ready (deploy/rollback gate). A model that
  // is still downloading, being verified or loading must not roll back a
  // room release; its state is reported alongside. /readyz/model is strict.
  if (m == "GET" && (path == "/readyz" || path == "/readyz/model")) {
    ModelStatus st = model_status(true);
    Val out = Val::object();
    out["ready"] = path == "/readyz" ? true : st.ready;
    out["model"] = model_status_val(st);
    if (path == "/readyz/model" && !st.ready)
      send_response(fd, 503, "application/json; charset=utf-8", mj::dump(out));
    else
      send_json(fd, 200, mj::dump(out));
    return;
  }
  if (m == "GET" && path == "/api/model-status") {
    send_json(fd, 200, mj::dump(model_status_val(model_status(true))));
    return;
  }
  if (m == "GET" && path == "/api/events") {
    handle_sse(fd);
    return;
  }
  if (m == "GET" && path == "/api/state") {
    send_json(fd, 200, state_json());
    return;
  }
  if (m == "GET" &&
      (path == "/api/bots" || path == "/api/room" || path == "/api/files")) {
    Val state;
    mj::parse(state_json(), state);
    Val result = Val::object();
    std::string key = path == "/api/bots"   ? "bots"
                      : path == "/api/room" ? "messages"
                                            : "files";
    result[key] = *state.find(path == "/api/bots"   ? "agents"
                              : path == "/api/room" ? "room"
                                                    : "files");
    send_json(fd, 200, mj::dump(result));
    return;
  }
  if (m == "GET" && path.rfind("/static/", 0) == 0) {
    std::string name = url_decode(path.substr(8));
    if (name.empty() || name.find('/') != std::string::npos ||
        name.find("..") != std::string::npos ||
        name.find('\0') != std::string::npos) {
      error_json(fd, 404, "Unknown asset");
      return;
    }
    bool ok;
    std::string data = read_file(STATIC_DIR + "/" + name, &ok);
    if (!ok)
      error_json(fd, 404, "Unknown asset");
    else
      send_response(fd, 200, mime_for(name), data);
    return;
  }
  if (m == "GET" && path.rfind("/api/file/", 0) == 0) {
    std::string stored = url_decode(path.substr(10));
    Val record;
    bool found = false;
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      for (auto &f : FILES)
        if (f.find("stored") && f.find("stored")->asStr() == stored) {
          record = f;
          found = true;
          break;
        }
    }
    if (!found || stored != basename_of(stored)) {
      error_json(fd, 404, "Unknown file");
      return;
    }
    if (record.find("removed")) {
      error_json(fd, 404, "Datei entfernt");
      return;
    }
    bool ok;
    auto data = read_file(FILES_DIR + "/" + stored, &ok);
    if (!ok) {
      error_json(fd, 404, "File missing");
      return;
    }
    std::string mime = record.find("mime")->asStr();
    // Active documents download instead of executing with room privileges.
    std::string disposition = (mime == "text/html" || mime == "image/svg+xml" ||
                               mime == "text/javascript")
                                  ? "attachment"
                                  : "inline";
    // ?view=1 renders an HTML page in place (bot browser, preview), still in
    // a CSP sandbox: opaque origin, scripts only, no access to room cookies.
    std::string csp = "sandbox";
    if (mime == "text/html" && qparam(r.query, "view") == "1") {
      disposition = "inline";
      csp = "sandbox allow-scripts";
    }
    send_response(
        fd, 200, mime, data,
        {{"Content-Disposition",
          disposition + "; filename=\"" + record.find("name")->asStr() + "\""},
         {"Content-Security-Policy", csp}});
    return;
  }
  if (path == "/api/frontend") {
    if (m == "GET")
      send_text(fd, 200, current_frontend(), "text/html; charset=utf-8");
    else if (m == "POST") {
      save_frontend(r.body);
      frontend_event();
      send_json(fd, 200, "{\"ok\":true}");
    } else if (m == "DELETE") {
      clear_frontend();
      frontend_event();
      send_json(fd, 200, "{\"ok\":true}");
    } else
      error_json(fd, 405, "Method not allowed");
    return;
  }
  if (m == "GET" && path.rfind("/api/screen/", 0) == 0) {
    int idx = browser_agent_index(url_decode(path.substr(12)));
    if (idx < 0) {
      error_json(fd, 404, "Unknown bot ID or VM name");
      return;
    }
    // Chat-only bots (vm "") own no VM: no browser, screen or console.
    if (bot_field(idx, "vm").empty()) {
      error_json(fd, 400, "Nur-Chat-Bot ohne eigene VM: kein Browser und keine Konsole");
      return;
    }
    std::string vm = bot_field(idx, "vm");
    const bool jpeg = qparam(r.query, "format") == "jpeg";
    HttpResp shot = browser_call(ensure_browser(vm), BROWSERD_PORT, "GET",
                                 jpeg ? "/screenshot.jpg" : "/screenshot.png",
                                 "", 10000);
    if (!shot.ok || shot.status != 200)
      error_json(fd, 502, "Browser screenshot unavailable");
    else
      send_response(fd, 200, jpeg ? "image/jpeg" : "image/png", shot.body,
                    {{"Cache-Control", "no-store"}});
    return;
  }
  if (m == "GET" && path == "/api/browser-live/status") {
    std::string status;
    long code = 0;
    if (!http_simple("127.0.0.1", 18784, "GET", "/status", "", {},
                     status, &code, nullptr, 1500) || code != 200) {
      error_json(fd, 503, "Browser UDP gateway unavailable");
      return;
    }
    send_response(fd, 200, "application/json", status,
                  {{"Cache-Control", "no-store"}});
    return;
  }
  // Only the authenticated MAG origin can signal the local WebRTC gateway.
  // The gateway and the VM browser remain on the same persistent page.
  if (m == "POST" && path == "/api/browser-live/offer") {
    std::string requested = qparam(r.query, "bot");
    int idx = browser_agent_index(requested);
    if (idx < 0) {
      error_json(fd, 404, "Unknown browser or VM");
      return;
    }
    // Chat-only bots (vm "") own no VM: no browser, screen or console.
    if (bot_field(idx, "vm").empty()) {
      error_json(fd, 400, "Nur-Chat-Bot ohne eigene VM: kein Browser und keine Konsole");
      return;
    }
    if (r.body.empty() || r.body.size() > 65536) {
      error_json(fd, 400, "SDP offer required");
      return;
    }
    std::string vm = bot_field(idx, "vm");
    ensure_browser(vm); // triggers one VM-owned hot browser only if necessary
    std::string answer;
    long status = 0;
    if (!http_simple("127.0.0.1", 18784, "POST",
                     "/offer?bot=" + url_encode(vm), r.body,
                     {{"Content-Type", "application/json"}}, answer,
                     &status, nullptr, 15000)) {
      error_json(fd, 503, "WebRTC/UDP browser gateway unavailable");
      return;
    }
    if (status != 200) {
      error_json(fd, 502, "WebRTC/UDP gateway rejected offer");
      return;
    }
    send_response(fd, 200, "application/json", answer,
                  {{"Cache-Control", "no-store"}});
    return;
  }
  if (m == "POST" && path.rfind("/api/browser/", 0) == 0) {
    auto rest = path.substr(13);
    auto slash = rest.find('/');
    if (slash == std::string::npos) {
      error_json(fd, 400, "Browser action required");
      return;
    }
    auto id = rest.substr(0, slash), action = rest.substr(slash + 1);
    int idx = browser_agent_index(url_decode(id));
    if (idx < 0) {
      error_json(fd, 404, "Unknown bot ID or VM name");
      return;
    }
    // Chat-only bots (vm "") own no VM: no browser, screen or console.
    if (bot_field(idx, "vm").empty()) {
      error_json(fd, 400, "Nur-Chat-Bot ohne eigene VM: kein Browser und keine Konsole");
      return;
    }
    const std::vector<std::string> allowed{"navigate", "click", "type",
                                           "scroll",   "back",  "forward",
                                           "reload",   "key",   "read",
                                           "pointer"};
    if (std::find(allowed.begin(), allowed.end(), action) == allowed.end()) {
      error_json(fd, 400, "Unknown browser action");
      return;
    }
    Val args;
    if (!mj::parse(r.body, args) || !args.isObj()) {
      error_json(fd, 400, "JSON required");
      return;
    }
    auto out = browser_json(bot_field(idx, "vm"), "/" + action, "POST", r.body);
    send_json(fd, 200, mj::dump(out));
    return;
  }
  if (path.rfind("/api/agents/", 0) == 0) {
    std::string id = url_decode(path.substr(12));
    auto slash = id.find('/');
    if (slash != std::string::npos &&
        id.compare(slash + 1, 7, "memory/") == 0 &&
        (m == "PUT" || m == "DELETE")) {
      std::string tail = id.substr(slash + 8);
      id.resize(slash);
      char *end = nullptr;
      long pos = tail.empty() ? -1 : strtol(tail.c_str(), &end, 10);
      if (pos < 0 || !end || *end) {
        error_json(fd, 400, "Bad memory index");
        return;
      }
      Val body = Val::object();
      bool has = !r.body.empty() && mj::parse(r.body, body) && body.isObj();
      std::string expect = m == "PUT"
          ? (has && body.find("old") ? body.find("old")->asStr() : "")
          : qparam(r.query, "old");
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      int idx = agent_index(id);
      if (idx < 0) {
        error_json(fd, 404, "Unknown agent");
        return;
      }
      Val &mem = AGENTS[idx]["memory"];
      if (!mem.isArr() || (size_t)pos >= mem.arr->size()) {
        error_json(fd, 404, "Unknown memory entry");
        return;
      }
      Val &entry = (*mem.arr)[pos];
      std::string cur = entry.isObj() ? (entry.find("text") ? entry.find("text")->asStr() : "") : entry.asStr();
      // Optimistic check: the entry must still be the one the user saw.
      if (!expect.empty() && cur.substr(0, expect.size()) != expect) {
        error_json(fd, 409, "Erinnerung wurde inzwischen geändert – bitte neu öffnen.");
        return;
      }
      if (m == "DELETE") {
        mem.arr->erase(mem.arr->begin() + pos);
      } else {
        std::string text = has && body.find("text") ? trim(body.find("text")->asStr()) : "";
        if (text.empty() || text.size() > 500) {
          error_json(fd, 400, "Text required (max 500)");
          return;
        }
        if (!entry.isObj()) { Val o = Val::object(); entry = std::move(o); }
        entry["text"] = text;
        entry["edited"] = (long)now_s();
      }
      save_agents();
      Val out = Val::object();
      out["ok"] = true;
      out["memory"] = mem;
      send_json(fd, 200, mj::dump(out));
      return;
    }
    if (slash != std::string::npos && id.substr(slash + 1) == "memory" &&
        m == "GET") {
      id.resize(slash);
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      int idx = agent_index(id);
      if (idx < 0)
        error_json(fd, 404, "Unknown agent");
      else {
        auto mem = AGENTS[idx].find("memory");
        send_json(fd, 200, mem ? mj::dump(*mem) : "[]");
      }
      return;
    }
    if (m == "DELETE") {
      if (!SINGLE_BOT.empty() && id == SINGLE_BOT) {
        error_json(fd, 409, "Der einzige Bot des Raums kann nicht entfernt werden (MAG_ROOM_SINGLE_BOT).");
        return;
      }
      stop_autotalk();
      std::lock_guard<std::mutex> turn(TURN_MTX);
      int idx = agent_index(id);
      if (idx < 0) {
        error_json(fd, 404, "Unknown agent");
        return;
      }
      std::string vm = bot_field(idx, "vm");
      if (!vm.empty()) {
        auto result = run_argv({"sudo", "-n", AGENT_CTL, "despawn", vm}, 180);
        if (result.code != 0) {
          error_json(fd, 502, result.out);
          return;
        }
      }
      {
        std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
        AGENTS.erase(AGENTS.begin() + idx);
        save_agents();
      }
      {
        std::lock_guard<std::mutex> lk(VM_IP_MTX);
        VM_IP_CACHE.erase(vm);
      }
      agents_event();
      send_json(fd, 200, "{\"ok\":true}");
      return;
    }
  }
  Val body = Val::object();
  bool has_json = !r.body.empty() && mj::parse(r.body, body) && body.isObj();
  auto str = [&](const char *key, const std::string &fallback = "") {
    auto v = body.find(key);
    return v ? v->asStr(fallback) : fallback;
  };
  if (m == "POST" && path == "/api/agents") {
    if (!SINGLE_BOT.empty()) {
      error_json(fd, 409, "Nur ein Bot erlaubt: Der Raum laeuft fest mit '" +
                              SINGLE_BOT_NAME + "' (MAG_ROOM_SINGLE_BOT).");
      return;
    }
    if (!has_json || trim(str("name")).empty() || str("name").size() > 40) {
      error_json(fd, 400, "Agent name required (1-40 bytes)");
      return;
    }
    // "vm": false creates a chat-only bot (no KVM, no browser/terminal);
    // default remains a bot with its own isolated VM.
    const auto vm_flag = body.find("vm");
    const bool with_vm = !(vm_flag && vm_flag->type == Val::BOOL &&
                           !vm_flag->asBool());
    cancel_active();
    std::lock_guard<std::mutex> turn(TURN_MTX);
    std::string id = slug_id(str("name")), vm = with_vm ? next_vm() : "";
    if (with_vm) {
      auto result = run_argv({"sudo", "-n", AGENT_CTL, "spawn", vm}, 600);
      if (result.code != 0) {
        error_json(fd, 502, result.out);
        return;
      }
    }
    Val agent = Val::object();
    agent["id"] = id;
    agent["name"] = str("name");
    agent["vm"] = vm;
    agent["color"] = str("color", "#8fd0ff");
    agent["persona"] =
        str("persona", "Neugierig, freundlich und eigenstaendig.");
    agent["memory"] = Val::array();
    try {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      AGENTS.push_back(agent);
      save_agents();
    } catch (...) {
      if (with_vm)
        run_argv({"sudo", "-n", AGENT_CTL, "despawn", vm}, 180);
      throw;
    }
    agents_event();
    if (with_vm)
      warm_browser_async(vm);
    send_json(fd, 201, mj::dump(agent));
    return;
  }
  if (m == "POST" && (path == "/api/message" || path == "/api/stream")) {
    if (!has_json || trim(str("text")).empty() || str("text").size() > 32768) {
      error_json(fd, 400, "Message required (max 32 KiB)");
      return;
    }
    auto ids = selected_ids(body);
    if (ids.empty()) {
      error_json(fd, 400, "Select an agent");
      return;
    }
    if (body.find("web_search"))
      WEB_ENABLED = body.find("web_search")->asBool(true);
    int think = -1;
    if (auto th = body.find("think"); th && th->type == Val::BOOL)
      think = th->asBool() ? 1 : 0;
    bool interrupt = false;
    if (auto in = body.find("interrupt"); in && in->type == Val::BOOL)
      interrupt = in->asBool();
    Val msg = append_room("user", "Nutzer", str("text"));
    msg["type"] = "message";
    publish(mj::dump(msg));
    const bool queued = BUSY.load() && !interrupt;
    schedule(ids, think, interrupt);
    send_json(fd, 202, queued ? "{\"accepted\":true,\"queued\":true}"
                              : "{\"accepted\":true}");
    return;
  }
  if (m == "POST" && path == "/api/autotalk") {
    if (!has_json || !body.find("on") || body.find("on")->type != Val::BOOL) {
      error_json(fd, 400, "Boolean on required");
      return;
    }
    if (body.find("on")->asBool())
      start_autotalk();
    else
      stop_autotalk();
    Val e = Val::object();
    e["type"] = "autotalk";
    e["on"] = AUTOTALK.load();
    publish(mj::dump(e));
    send_json(fd, 200, mj::dump(e));
    return;
  }
  if (m == "POST" && path == "/api/stop") {
    stop_autotalk();
    Val e = Val::object();
    e["type"] = "stopped";
    publish(mj::dump(e));
    send_json(fd, 200, "{\"stopped\":true}");
    return;
  }
  if (m == "POST" && path == "/api/reset") {
    stop_autotalk();
    std::lock_guard<std::mutex> turn(TURN_MTX);
    std::vector<std::string> vms;
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      for (auto &a : AGENTS) {
        auto v = a.find("vm");
        if (v && !v->asStr().empty())
          vms.push_back(v->asStr());
      }
    }
    for (auto &vm : vms) {
      auto result = guest_command(
          vm, "rm -rf -- /home/bot/room && mkdir -p /home/bot/room", 20);
      if (result.code)
        throw std::runtime_error("Guest room cleanup failed: " + vm);
    }
    reset_room(true);
    clear_frontend();
    Val e = Val::object();
    e["type"] = "reset";
    publish(mj::dump(e));
    send_json(fd, 200, "{\"ok\":true,\"memory_preserved\":true}");
    return;
  }
  if (m == "POST" && path == "/api/upload") {
    std::string name = qparam(r.query, "name");
    if (name.empty())
      name = r.hdrs["x-filename"];
    if (r.body.empty()) {
      error_json(fd, 400, "Empty file");
      return;
    }
    Val f = add_file(name.empty() ? "upload" : name, r.body, "Nutzer");
    append_room("user", "Nutzer", "", "", &f);
    emit_file("", f);
    send_json(fd, 201, mj::dump(f));
    return;
  }
  if (m == "POST" && path == "/api/stt") {
    if (r.body.empty()) {
      error_json(fd, 400, "Audio required");
      return;
    }
    Val out = host_stt(r.body, qparam(r.query, "lang"));
    std::string raw = out.find("text") ? out.find("text")->asStr() : "";
    out["raw"] = raw;
    out["corrected"] = raw;
    // Correction uses the same single model slot and returns editable text.
    if (!raw.empty()) {
      std::unique_lock<std::mutex> turn(TURN_MTX, std::try_to_lock);
      if (turn.owns_lock()) {
        STOP = false;
        Val system = Val::object();
        system["role"] = "system";
        system["content"] =
            "Korrigiere ausschliesslich Erkennungsfehler, Grammatik und "
            "Selbstkorrekturen in der Transkription. Deutsch/Englisch und "
            "Sprachwechsel erhalten. Keine Zahlen, Befehle oder Bedeutungen "
            "erfinden. Gib nur den korrigierten Text aus. Kontext ist nur "
            "Hintergrund: " +
            build_transcript();
        Val user = Val::object();
        user["role"] = "user";
        user["content"] = raw;
        // Transcript correction: no reasoning needed (faster on CPU).
        Turn t = backend_turn({system, user}, "[]", 0,
                              [](const std::string &, const std::string &) {},
                              false);
        if (t.error.empty() && !trim(t.text).empty())
          out["corrected"] = trim(t.text);
        else
          out["correction_error"] = t.error;
      } else
        out["correction_pending"] = true;
    }
    send_json(fd, 200, mj::dump(out));
    return;
  }
  if (m == "POST" && path == "/api/speak") {
    if (!has_json) {
      error_json(fd, 400, "JSON required");
      return;
    }
    Val f = host_tts(str("text"));
    emit_file("", f);
    send_json(fd, 201, mj::dump(f));
    return;
  }
  if (m == "POST" && path.rfind("/api/agent/", 0) == 0) {
    std::string rest = path.substr(11);
    auto slash = rest.find('/');
    std::string id = rest.substr(0, slash),
                action =
                    slash == std::string::npos ? "" : rest.substr(slash + 1);
    int idx = agent_index(id);
    if (idx < 0) {
      error_json(fd, 404, "Unknown agent");
      return;
    }
    if (action == "turn") {
      schedule({id});
      send_json(fd, 202, "{\"accepted\":true}");
      return;
    }
    if (action == "remember" && has_json) {
      remember_fact(id, str("fact"), str("source"));
      send_json(fd, 200, "{\"ok\":true}");
      return;
    }
    if (action == "say" && has_json) {
      Val msg =
          append_room("assistant", bot_field(idx, "name"), str("text"), id);
      msg["type"] = "message";
      publish(mj::dump(msg));
      send_json(fd, 200, "{\"ok\":true}");
      return;
    }
  }
  error_json(fd, 404, "Unknown route");
}
static std::atomic<int> CONNECTIONS{0};
static void serve_connection(int fd, bool bridge) {
  if (++CONNECTIONS > 128) {
    error_json(fd, 503, "Connection limit");
    --CONNECTIONS;
    close(fd);
    return;
  }
  timeval timeout{30, 0};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  try {
    Req r;
    if (read_request(fd, r))
      route(fd, r, bridge);
  } catch (const std::exception &e) {
    error_json(fd, 500, e.what());
  }
  close(fd);
  --CONNECTIONS;
}
