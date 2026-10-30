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
  if (!room_is_glm()) {
    if (selected && selected->isArr()) {
      for (const auto &id : selected->asArr())
        if (agent_index(id.asStr()) >= 0)
          ids.push_back(id.asStr());
    } else
      for (auto &a : AGENTS) ids.push_back(a.find("id")->asStr());
    return ids;
  }
  // GLM host arbitration: never let unaddressed input wake both bots.
  auto add = [&](const std::string &id, bool explicit_address = false) {
    int idx = agent_index(id);
    if (idx >= 0 && (explicit_address || bot_enabled_locked(idx)) &&
        std::find(ids.begin(), ids.end(), id) == ids.end()) ids.push_back(id);
  };
  if (selected && selected->isArr() && !selected->asArr().empty()) {
    for (const auto &id : selected->asArr()) add(id.asStr(), true);
    return ids; // explicit UI selection, including explicit multiple bots
  }
  std::string input = mag::str(body, "text");
  std::transform(input.begin(), input.end(), input.begin(),
                 [](unsigned char c){ return (char)std::tolower(c); });
  auto token = [&](const std::string &word) {
    size_t p = input.find(word);
    while (p != std::string::npos) {
      const size_t e = p + word.size();
      const bool left = p == 0 || !std::isalnum((unsigned char)input[p-1]);
      const bool right = e == input.size() || !std::isalnum((unsigned char)input[e]);
      if (left && right) return true;
      p = input.find(word, p + 1);
    }
    return false;
  };
  if (token("beide") || token("alle")) {
    for (const auto &a : AGENTS) add(mag::str(a, "id"), true);
    return ids;
  }
  for (const auto &a : AGENTS) {
    std::string name = mag::str(a, "name");
    std::transform(name.begin(), name.end(), name.begin(),
                   [](unsigned char c){ return (char)std::tolower(c); });
    if (!name.empty() &&
        (token("@" + name) || input == name ||
         input.rfind(name + ":", 0) == 0 ||
         input.rfind(name + ",", 0) == 0 ||
         input.rfind(name + " ", 0) == 0))
      add(mag::str(a, "id"), true);
  }
  if (!ids.empty()) return ids;
  const std::string configured = env("MAG_ROOM_DEFAULT_BOT_ID", "");
  if (!configured.empty()) add(configured);
  if (ids.empty())
    for (const auto &a : AGENTS) {
      add(mag::str(a, "id"));
      if (!ids.empty()) break;
    }
  return ids;
}
static void agents_event() {
  Val e = Val::object();
  e["type"] = "agents";
  publish(mj::dump(e));
}
#include "mcp_routes.hpp"
// A VM spawn takes minutes. The bot is stored at once with "pending_vm" and
// works chat-only until its VM is ready; neither the HTTP request nor the
// turn lock waits for it. A restart during the spawn resumes it (resume=true
// adopts an already existing VM of that name, because it was recorded before
// the spawn started). A fresh spawn never adopts: an existing VM of that
// name is an orphan from an earlier failed run and the next free name is used.
static bool vm_owned_by_other(const std::string &id, const std::string &vm) {
  std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
  for (auto *list : {&AGENTS, &PARKED})
    for (auto &a : *list) {
      auto aid = a.find("id");
      if (aid && aid->asStr() == id) continue;
      for (const char *key : {"vm", "pending_vm"}) {
        auto v = a.find(key);
        if (v && v->asStr() == vm) return true;
      }
    }
  return false;
}
static void finish_spawn(const std::string &id, std::string vm, bool resume) {
  struct Reservation {
    std::set<std::string> names;
    ~Reservation() {
      std::lock_guard<std::recursive_mutex> lock(STATE_MTX);
      for (const auto &name : names) SPAWNING_VMS.erase(name);
    }
  } reservation{{vm}};
  std::vector<std::string> tried;
  std::string error;
  bool ok = false;
  for (int attempt = 0; attempt < 8 && !ok; ++attempt) {
    auto result = run_argv({"sudo", "-n", AGENT_CTL, "spawn", vm}, 600);
    if (result.code == 0) { ok = true; break; }
    error = trim(result.out).substr(0, 400);
    // A restart adopts the VM this same spawn already created.
    if (resume && error.find("already exists") != std::string::npos) { ok = true; break; }
    // Roll back this attempt. A name that belongs to another bot is left alone.
    if (!vm_owned_by_other(id, vm))
      run_argv({"sudo", "-n", AGENT_CTL, "despawn", vm}, 180);
    if (error.find("already exists") == std::string::npos) break;
    tried.push_back(vm);
    std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
    int idx = agent_index(id);
    if (idx < 0 || bot_field(idx, "pending_vm") != vm) return;
    vm = next_vm(tried);
    SPAWNING_VMS.insert(vm); reservation.names.insert(vm);
    AGENTS[idx]["pending_vm"] = vm;
    try { save_agents(); } catch (...) {}
  }
  bool orphan = false, detached = false, attach = false;
  {
    std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
    int idx = agent_index(id);
    if (idx < 0 || bot_field(idx, "pending_vm") != vm) {
      orphan = ok; detached = true;
    } else {
      // An existing chat-only bot that gets a VM stays a bot when it fails.
      attach = AGENTS[idx].find("vm_attach") != nullptr;
      drop_key(AGENTS[idx], "pending_vm");
      drop_key(AGENTS[idx], "vm_attach");
      if (ok) AGENTS[idx]["vm"] = vm;
      else if (!attach) AGENTS.erase(AGENTS.begin() + idx);
      try { save_agents(); } catch (...) {}
    }
  }
  if (orphan) {
    // Removed while its VM was being created: do not leave the VM behind.
    run_argv({"sudo", "-n", AGENT_CTL, "despawn", vm}, 180);
    return;
  }
  if (detached) return;
  if (ok) {
    agents_event();
    warm_browser_async(vm);
    return;
  }
  std::cerr << "spawn " << vm << " for " << id << " failed: " << error << std::endl;
  if (attach) {
    agents_event();
    emit_event("error", id, "", "VM für den Bot konnte nicht erstellt werden: " + error);
    return;
  }
  drop_bot_refs(id);
  jobs_drop_bot(id);
  agents_event();
  emit_event("error", id, "", "VM für den neuen Bot konnte nicht erstellt werden: " + error);
}
static void resume_pending_spawns() {
  struct Claim { std::string id, vm; bool resume; };
  std::vector<Claim> pending;
  {
    std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
    // The configured DeepSeek+Qwen room must have a real computer per Bot.
    // A bot minted by load_agents() used to persist vm="" forever; the UI
    // then showed a chat-only placeholder. Reconcile it on each cold start.
    // Never allocate one in legacy chat-only mode or steal a parked bot VM.
    const bool provision = CONFIGURED_MODEL &&
        env("MAG_ROOM_SINGLE_BOT_VM", "off") == "on";
    bool changed = false;
    for (size_t i = 0; i < AGENTS.size(); ++i) {
      const std::string id = bot_field((int)i, "id");
      if (id.empty()) continue;
      std::string vm = bot_field((int)i, "pending_vm");
      if (!vm.empty()) {
        SPAWNING_VMS.insert(vm);
        pending.push_back({id, vm, true});
      } else if (provision && bot_field((int)i, "vm").empty()) {
        // Existing identity, role and memories survive a failed spawn.
        // finish_spawn() knows vm_attach means "do not delete this bot".
        vm = next_vm();
        AGENTS[i]["pending_vm"] = vm;
        AGENTS[i]["vm_attach"] = true;
        SPAWNING_VMS.insert(vm);
        pending.push_back({id, vm, false});
        changed = true;
      }
    }
    if (changed) save_agents(); // atomic reservation survives a restart
  }
  for (const auto &p : pending) {
    std::thread([p] { finish_spawn(p.id, p.vm, p.resume); }).detach();
  }
}
// Clear/reset must never race a running turn: a cancelled turn still appends
// its partial answer and a queued job would answer into the emptied room.
// work_pending() is what the UI shows as "Ein Bot rechnet noch".
static bool work_pending() {
  if (BUSY.load() || tasks_running()) return true;
  std::lock_guard<std::mutex> lk(JOB_MTX);
  return !JOBS.empty() || !ACTIVE_JOBS.empty() || !PENDING.empty();
}
// Cancels every lane, task, prewarm and queued/scheduled turn, then waits
// until all turns have left. Returns with JOB_MTX and TURN_MTX (exclusive)
// held so the caller clears atomically; false if a turn did not stop in time.
static bool quiesce_all(std::unique_lock<std::mutex> &sched,
                        std::unique_lock<std::shared_mutex> &turn, bool force) {
  if (force) {
    cancel_active(true);
    task_cancel("", "");
    cancel_prewarm();
    std::lock_guard<std::mutex> lk(JOB_MTX);
    JOBS.clear(); ACTIVE_JOBS.clear(); PENDING.clear(); PARKED_JOBS.clear(); QUEUE_PAUSED = false;
    AUTO_READY = false; AUTO_OWNER.clear(); ++AUTO_GENERATION;
    AUTO_LAST_PROGRESS.clear(); AUTO_TURNS_LEFT = 0;
    save_queue_locked(); queue_event_locked();
  }
  for (int i = 0; i < (force ? 400 : 1); ++i) {
    if (i) std::this_thread::sleep_for(std::chrono::milliseconds(50));
    if (BUSY.load() || tasks_running()) continue;
    sched = std::unique_lock<std::mutex>(JOB_MTX);
    turn = std::unique_lock<std::shared_mutex>(TURN_MTX, std::try_to_lock);
    if (turn.owns_lock() && !BUSY.load() && !tasks_running() &&
        (force || (JOBS.empty() && ACTIVE_JOBS.empty() && PENDING.empty())))
      return true;
    if (turn.owns_lock()) turn.unlock();
    sched.unlock();
  }
  return false;
}
static void busy_json(int fd) {
  Val e = Val::object();
  e["error"] = "Ein Bot rechnet noch"; e["busy"] = true;
  send_json(fd, 409, mj::dump(e));
}
static void route(int fd, Req &r, bool bridge = false) {
  const std::string &p = r.path;
  const std::string &m = r.method;
  // Native nginx auth_request; no VM bridge or implicit local-owner fallback.
  if (!bridge && p == "/_internal/tailscale/whois") {
    native_tailscale_auth(fd, r);
    return;
  }
  Actor actor;
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
      // A saved copy of the room UI carries the live session token.
      if (!ui_token().empty() && r.body.find(ui_token()) != std::string::npos) {
        error_json(fd, 422, "Datei enthaelt das Raum-Token");
        return;
      }
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
            "mag_bridge=" + expected + "; Path=/; HttpOnly; SameSite=Strict"},
           {"Cross-Origin-Opener-Policy", "same-origin"},
           {"Cross-Origin-Embedder-Policy", "require-corp"}});
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
    if (m == "POST" && r.path.rfind("/hook/jobs/", 0) == 0) {
      // Same loopback rule as the UI. The per-job secret is the credential;
      // a non-loopback caller cannot reach it by spoofing headers.
      if (!is_loopback_ip(connection_peer(fd))) {
        error_json(fd, 403, "Kein Zugang");
        return;
      }
      // Webhook trigger: authenticated only by the per-job secret.
      std::string id = r.path.substr(11), secret;
      auto h = r.hdrs.find("x-mag-job-secret");
      if (h != r.hdrs.end()) secret = h->second;
      auto au = r.hdrs.find("authorization");
      if (secret.empty() && au != r.hdrs.end() && au->second.rfind("Bearer ", 0) == 0) secret = au->second.substr(7);
      std::lock_guard<std::mutex> lk(JOBDEF_MTX);
      JobDef *j = job_find_locked(id);
      if (!j || j->hook_hash.empty() || secret.empty() || !ct_eq(sha256_hex(secret), j->hook_hash)) {
        error_json(fd, 401, "Unauthorized");
        return;
      }
      if (j->paused) { error_json(fd, 409, "Job pausiert"); return; }
      std::string rid = j->id + ":hook:" + std::to_string(now_s()) + token_hex(3);
      if (!job_enqueue_locked(*j, rid, "Webhook")) { error_json(fd, 409, "läuft bereits"); return; }
      send_json(fd, 202, "{\"accepted\":true,\"run_id\":\"" + rid + "\"}");
      return;
    }
    auto origin = r.hdrs.find("origin");
    auto host = r.hdrs.find("host");
    if (origin != r.hdrs.end() && host != r.hdrs.end() &&
        origin->second != "https://" + host->second &&
        origin->second != "http://" + host->second) {
      error_json(fd, 403, "Wrong origin");
      return;
    }
    if (m != "GET" && m != "HEAD" && m != "OPTIONS" &&
        r.path != "/api/deploy-hold") {
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
    // Identity is the Tailscale user Caddy put on this loopback hop.
    // Non-loopback peers are denied here, including forged identity headers.
    if (!identify_actor(connection_peer(fd), r.hdrs, actor)) {
      error_json(fd, 403, "Kein Zugang");
      return;
    }
    if (!actor.login.empty() && !ts_proof_valid(r.hdrs)) {
      error_json(fd,403,"Whois-Nachweis fehlt"); return;
    }
    if (actor.role == "guest" && !actor.node.empty() && !guest_live(actor.node)) {
      if (m == "GET" && (r.path == "/" || r.path == "/index.html")) {
        // No room state, script or endpoints are exposed before a valid claim.
        std::string page = R"HTML(<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="font:16px system-ui;max-width:32rem;margin:5rem auto;padding:1rem"><h1>MAG · Gastzugang</h1><p>Zum Beitreten zuerst Tailscale installieren und den einmaligen Schlüssel in der Tailscale-App unter „Use an auth key“ eingeben. Danach diesen Einladungslink erneut öffnen.</p><p><a href="https://tailscale.com/download" rel="noopener">Tailscale installieren</a></p><p id="key" style="overflow-wrap:anywhere"></p><button id="copy" hidden>Schlüssel kopieren</button><p id="status">Einladung prüfen …</p><script>
const p=new URLSearchParams(location.hash.slice(1)), code=p.get('invite'), key=p.get('key');
if(key){document.getElementById('key').textContent=key;const b=document.getElementById('copy');b.hidden=false;b.onclick=()=>navigator.clipboard.writeText(key)}
if(code){fetch('/api/guest/claim',{method:'POST',headers:{'Content-Type':'application/json','X-Mag-Token':'%%TOKEN%%'},body:JSON.stringify({code})}).then(async r=>{if(!r.ok)throw Error((await r.json()).error||'Nicht bestätigt');location.replace('/')}).catch(e=>document.getElementById('status').textContent=e.message)}
else document.getElementById('status').textContent='Bitte den vollständigen Einladungslink öffnen.';
</script></body></html>)HTML";
        auto at = page.find("%%TOKEN%%");
        if (at != std::string::npos) page.replace(at, 9, ui_token());
        send_response(fd, 200, "text/html; charset=utf-8", page,
                      {{"Cache-Control","no-store"},{"Referrer-Policy","no-referrer"}});
        return;
      }
      if (!(m == "POST" && r.path == "/api/guest/claim")) {
        error_json(fd, 403, "Einladung noch nicht bestätigt oder widerrufen");
        return;
      }
    } else if (actor.role == "guest" && !actor.node.empty()) {
      actor.name = guest_name_for_node(actor.node);
      // Deliberately independent of configurable AccessCfg permissions: a
      // tagged guest may never gain Jobs, Pages, Admin or model privileges.
      const auto &q = r.path;
      const bool read = m == "GET" &&
          (q == "/" || q == "/index.html" || q == "/sw.js" ||
           q == "/manifest.webmanifest" || q == "/api/state" ||
           q == "/api/events" || q == "/api/bots" ||
           q == "/api/room" || q == "/api/files" ||
           q == "/api/version" || q == "/api/model-status" ||
           q.rfind("/static/",0)==0 || q.rfind("/api/file/",0)==0);
      const bool write = (m == "POST" &&
          (q == "/api/message" || q == "/api/upload" || q == "/api/upload/chunk" ||
           q == "/api/upload/chunk/commit")) ||
           (m == "GET" && q == "/api/upload/chunk/status");
      if (!read && !write) {
        error_json(fd,403,"Gäste dürfen diese Aktion nicht ausführen");
        return;
      }
    }
    if (!(m == "POST" && r.path == "/api/guest/claim" &&
          actor.role == "guest" && !actor.node.empty()) &&
        !permit_request(m, r.path, actor)) {
      error_json(fd, 403, "Nur der Besitzer");
      return;
    }
  }
  const std::string path = r.path;
  if (!bridge && path == "/api/guest/claim" && m == "POST") {
    if (actor.role != "guest" || actor.node.empty() || r.body.size() > 2048) {
      error_json(fd,403,"Kein Gastgerät"); return;
    }
    Val input;
    if (!mj::parse(r.body,input) || !input.isObj()) {
      error_json(fd,400,"Ungültige Anfrage"); return;
    }
    auto result = guest_claim(actor,input);
    send_json(fd,result.code,mj::dump(result.body));
    return;
  }
  if (!bridge && (path == "/api/guests" || path.rfind("/api/guests/",0)==0)) {
    // Only the owner whose identity came from the native nginx/whois gate.
    // Direct localhost without an attested LoginName never gains admin rights.
    if (actor.role != "owner" || actor.login.empty() || !ts_proof_valid(r.hdrs)) {
      error_json(fd,403,"Nur der Besitzer"); return;
    }
    if (m == "GET" && path == "/api/guests") {
      Val result=guest_list_live();
      send_json(fd,200,mj::dump(result)); return;
    }
    // Stricter than the normal UI: every mutation needs a fresh page token,
    // same-origin fetch metadata and exact HTTPS Origin.
    auto origin = r.hdrs.find("origin");
    auto token = r.hdrs.find("x-mag-token");
    auto site = r.hdrs.find("sec-fetch-site");
    if (origin == r.hdrs.end() ||
        origin->second != "https://mag.stardenburg.de" ||
        token == r.hdrs.end() || !ct_eq(token->second, ui_token()) ||
        site == r.hdrs.end() || site->second != "same-origin") {
      error_json(fd,403,"CSRF-Prüfung fehlgeschlagen"); return;
    }
    if (m == "POST" && path == "/api/guests") {
      if (r.body.size() > 4096) { error_json(fd,413,"Zu groß"); return; }
      Val input;
      if (!mj::parse(r.body,input) || !input.isObj()) {
        error_json(fd,400,"Ungültige Anfrage"); return;
      }
      auto issue=guest_issue(actor,input);
      send_json(fd,issue.code,mj::dump(issue.body)); return;
    }
    if (m == "DELETE" && path.rfind("/api/guests/",0)==0) {
      auto id=path.substr(std::string("/api/guests/").size());
      if (!guest_ident(id)) { error_json(fd,400,"Ungültige ID"); return; }
      bool done=guest_revoke(id,actor.login);
      if (done) send_json(fd,200,"{\"ok\":true}");
      else send_json(fd,202,"{\"ok\":true,\"pending_cleanup\":true}");
      return;
    }
    error_json(fd,405,"Methode nicht erlaubt");
    return;
  }
  if (path == "/mcp/jobs") {
    // Optional remote MCP transport: disabled until a long random server-only
    // token is explicitly configured. The model itself uses in-process tools.
    if (m != "POST") { error_json(fd, 405, "POST required"); return; }
    if (r.body.size() > 65536) { error_json(fd, 413, "Request too large"); return; }
    const std::string secret = env("MAG_JOBS_MCP_TOKEN", "");
    std::string bearer;
    auto it = r.hdrs.find("authorization");
    if (it != r.hdrs.end() && it->second.rfind("Bearer ", 0) == 0)
      bearer = it->second.substr(7);
    unsigned mismatch = 0;
    if (secret.size() != bearer.size()) mismatch = 1;
    else for (size_t i=0; i<secret.size(); ++i)
      mismatch |= (unsigned)(unsigned char)(secret[i] ^ bearer[i]);
    if (secret.size() < 32 || mismatch) {
      error_json(fd, 401, "MCP token required"); return;
    }
    Val req;
    if (!mj::parse(r.body, req) || !req.isObj()) {
      send_json(fd, 400, mj::dump(jobs_mcp_rpc_error(nullptr,-32700,"Parse error")));
      return;
    }
    // 2026-07-28 Streamable HTTP requires method/name headers and per-call
    // metadata. Older 2025-era MCP clients retain the initialize path.
    auto protocol = r.hdrs.find("mcp-protocol-version");
    if (protocol != r.hdrs.end() && protocol->second == "2026-07-28") {
      const std::string method = req.find("method") ? req.find("method")->asStr() : "";
      auto mh = r.hdrs.find("mcp-method");
      const Val *params = req.find("params");
      const Val *meta = params ? params->find("_meta") : nullptr;
      if (mh == r.hdrs.end() || mh->second != method ||
          !meta || !meta->find("io.modelcontextprotocol/protocolVersion") ||
          meta->find("io.modelcontextprotocol/protocolVersion")->asStr() != "2026-07-28" ||
          !meta->find("io.modelcontextprotocol/clientCapabilities")) {
        send_json(fd, 400, mj::dump(jobs_mcp_rpc_error(req.find("id"),-32602,
                       "MCP method/header/_meta mismatch"))); return;
      }
      if (method == "tools/call") {
        auto nh = r.hdrs.find("mcp-name");
        const std::string name = params && params->find("name") ? params->find("name")->asStr() : "";
        if (nh == r.hdrs.end() || nh->second != name) {
          send_json(fd, 400, mj::dump(jobs_mcp_rpc_error(req.find("id"),-32602,
                         "Mcp-Name mismatch"))); return;
        }
      }
    }
    if (!req.find("id")) {
      // JSON-RPC notifications never receive a JSON-RPC response.
      send_response(fd, 202, "application/json", "");
      return;
    }
    send_json(fd, 200, mj::dump(jobs_mcp_rpc(req, JobsActor{})));
    return;
  }
  if (m == "GET" && (path == "/" || path == "/index.html")) {
    send_response(fd, 200, "text/html; charset=utf-8", current_frontend(),
                  {{"Cache-Control", "no-store"},
                   {"Cross-Origin-Opener-Policy", "same-origin"},
                   {"Cross-Origin-Embedder-Policy", "require-corp"}});
    return;
  }
  if (m == "GET" && path == "/sw.js") {
    bool ok = false;
    std::string js = read_file(sw_file(), &ok);
    if (!ok) {
      error_json(fd, 404, "Service worker missing");
      return;
    }
    const std::string token = "%%MAG_VERSION%%";
    const std::string ver = build_version();
    for (size_t at = js.find(token); at != std::string::npos; at = js.find(token, at)) {
      js.replace(at, token.size(), ver);
      at += ver.size();
    }
    send_response(fd, 200, "text/javascript; charset=utf-8", js,
                  {{"Cache-Control", "no-store"},
                   {"Service-Worker-Allowed", "/"}});
    return;
  }
  if (m == "GET" && path == "/manifest.webmanifest") {
    send_response(fd, 200, "application/manifest+json; charset=utf-8",
                  manifest_body(), {{"Cache-Control", "no-store"}});
    return;
  }
  if (m == "GET" && path == "/api/version") {
    send_response(fd, 200, "application/json; charset=utf-8",
                  "{\"version\":\"" + build_version() + "\"}",
                  {{"Cache-Control", "no-store"}});
    return;
  }
  if (path == "/api/push/vapid" && m == "GET") {
    Val out = Val::object();
    out["configured"] = push_configured();
    out["publicKey"] = push_configured() ? push_public_b64() : "";
    out["subject"] = push_configured() ? push_subject() : "";
    send_response(fd, 200, "application/json; charset=utf-8", mj::dump(out),
                  {{"Cache-Control", "no-store"}});
    return;
  }
  if (path == "/api/push/subscription" && m == "GET") {
    std::string js = push_subscription_json(qparam(r.query, "endpoint"));
    if (js.empty())
      error_json(fd, 404, "Kein Gerät");
    else
      send_response(fd, 200, "application/json; charset=utf-8", js,
                    {{"Cache-Control", "no-store"}});
    return;
  }
  if (path == "/api/push/subscribe" && m == "POST") {
    std::string err;
    int code = 400;
    std::string js = push_subscribe_body(r.body, err, code);
    if (js.empty())
      error_json(fd, code, err.empty() ? "Ungültige Anmeldung" : err);
    else
      send_json(fd, 200, js);
    return;
  }
  if (path == "/api/push/subscribe" && m == "DELETE") {
    Val body;
    std::string endpoint;
    if (!r.body.empty() && mj::parse(r.body, body) && body.isObj() &&
        body.find("endpoint"))
      endpoint = trim(body.find("endpoint")->asStr());
    if (endpoint.empty())
      endpoint = qparam(r.query, "endpoint");
    if (!push_good_endpoint(endpoint)) {
      error_json(fd, 400, "Push-Endpunkt ungültig");
      return;
    }
    push_unsubscribe(endpoint);
    send_json(fd, 200, "{\"ok\":true}");
    return;
  }
  if (path == "/api/push/prefs" && m == "PUT") {
    std::string err;
    if (!push_set_prefs(r.body, err))
      error_json(fd, err == "Kein Gerät" ? 404 : 400,
                 err.empty() ? "Ungültige Einstellung" : err);
    else
      send_json(fd, 200, "{\"ok\":true}");
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
                    {{"Cache-Control", asset_cache_control(r.query)}});
    return;
  }
  if (mcp_route(fd, r, m, path)) return;
  if (path == "/api/bot-settings" && m == "PUT") {
    if (bridge || actor.role != "owner") { error_json(fd, 403, "Nur der Besitzer"); return; }
    Val body;
    if (!mj::parse(r.body, body) || !body.isObj()) { error_json(fd, 400, "Settings required"); return; }
    const auto think = body.find("thinking"), github = body.find("github_enabled"), pho = body.find("pho_compress"), enabled = body.find("enabled"), proaktiv = body.find("proaktiv");
    if ((!think && !github && !pho && !enabled && !proaktiv) || (think && think->type != Val::BOOL) || (github && github->type != Val::BOOL) || (pho && pho->type != Val::BOOL) || (enabled && enabled->type != Val::BOOL) || (proaktiv && proaktiv->type != Val::BOOL)) {
      error_json(fd, 400, "Boolean settings required"); return;
    }
    const bool room = mag::str(body, "scope") == "room";
    auto ids = selected_ids(body);
    if (ids.empty() && !room) { error_json(fd, 400, "Select a bot"); return; }
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      if (room) {
        if (think) THINKING_DEFAULT = think->asBool();
        if (github) GITHUB_DEFAULT = github->asBool();
        if (pho) PHO_DEFAULT = pho->asBool();
        Val prefs = Val::object(); prefs["thinking"] = THINKING_DEFAULT.load(); prefs["github_enabled"] = GITHUB_DEFAULT.load(); prefs["pho_compress"] = PHO_DEFAULT.load();
        if (!write_file_atomic(STATE_DIR + "/bot-defaults.json", mj::dump(prefs))) { error_json(fd, 500, "Speichern fehlgeschlagen"); return; }
      }
      for (auto &a : AGENTS) if (room || std::find(ids.begin(), ids.end(), mag::str(a, "id")) != ids.end()) {
        if (think) a["thinking"] = think->asBool();
        if (github) a["github_enabled"] = github->asBool();
        if (pho) a["pho_compress"] = pho->asBool();
        if (enabled && !room) a["enabled"] = enabled->asBool();
        if (proaktiv && !room) a["proaktiv"] = proaktiv->asBool();
      }
      save_agents();
    }
    if (enabled && !room && !enabled->asBool()) {
      // Pausing also cancels an active response and queued owner continuations.
      cancel_bots(ids, true);
      std::lock_guard<std::mutex> lk(JOB_MTX);
      while (dispatch_locked()) {}
      JOB_CV.notify_all();
    }
    agents_event();
    send_json(fd, 200, "{\"ok\":true}"); return;
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
  // Selects the model id on the next completion. Does not restart Q4, IQ3,
  // the gateway, or this process, and does not drop other clients.
  if (m == "GET" && path == "/api/model") {
    Val out = Val::object();
    out["model"] = active_room_model();
    out["models"] = room_model_catalog();
    send_json(fd, 200, mj::dump(out));
    return;
  }
  if (m == "POST" && path == "/api/model") {
    if (DUAL_NUMA) { error_json(fd, 409, "Q4 und Qwen sind fest ihren Bots zugeordnet."); return; }
    if (CONFIGURED_MODEL) { error_json(fd, 409, "Die Modelle sind je Bot und NUMA-Knoten fest zugeordnet."); return; }
    if (bridge || !actor.ok || actor.role != "owner") {
      error_json(fd, 403, "Nur der Besitzer"); return;
    }
    Val choice;
    if (!mj::parse(r.body, choice) || !choice.isObj()) {
      error_json(fd, 400, "Modell fehlt"); return;
    }
    std::string id;
    if (auto picked = choice.find("id")) id = picked->asStr();
    else if (auto picked = choice.find("model")) id = picked->asStr();
    if (!set_room_model(id)) {
      error_json(fd, 400, "Unbekanntes Modell"); return;
    }
    Val out = Val::object();
    out["ok"] = true;
    out["model"] = active_room_model();
    out["restart"] = false;
    send_json(fd, 200, mj::dump(out));
    return;
  }
  if (path == "/api/models") {
    error_json(fd, 410, "Modellwechsel deaktiviert. Q4 bleibt aktiv.");
    return;
  }
  // Owner-only, read-only summary without credentials or repository paths.
  if (m == "GET" && path == "/api/backup/status") {
    if (bridge || !actor.ok || actor.role != "owner" ||
        actor.login.empty() || !ts_proof_valid(r.hdrs)) {
      error_json(fd, 403, "Nur der Besitzer"); return;
    }
    bool ok = false;
    std::string raw = read_file(
        env("MAG_BACKUP_STATUS", "/var/lib/mag-backup/status.json"), &ok);
    Val out = Val::object();
    if (ok && raw.size() <= 8192) mj::parse(raw, out);
    if (!out.isObj()) out = Val::object();
    const Val *last = out.find("last_success");
    const bool stale = !last || last->asNum() < now_s() - 26 * 3600;
    const Val *reported = out.find("healthy");
    const bool healthy = !stale && reported && reported->asBool();
    out["stale"] = stale;
    out["healthy"] = healthy;
    send_json(fd, 200, mj::dump(out));
    return;
  }
  if (m == "GET" && path == "/api/model-status") {
    send_json(fd, 200, mj::dump(model_status_val(model_status(true))));
    return;
  }
  if (m == "GET" && path == "/api/events") {
    handle_sse(fd, actor.role == "guest" ? actor.node : "");
    return;
  }
  if (m == "GET" && path == "/api/state") {
    send_json(fd, 200, state_json(bridge ? nullptr : &actor));
    return;
  }
  if (m == "GET" &&
      (path == "/api/bots" || path == "/api/room" || path == "/api/files")) {
    Val state;
    mj::parse(state_json(bridge ? nullptr : &actor), state);
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
    if (name.empty() ||
        (name.find('/') != std::string::npos && !hashed_speech_asset(name)) ||
        name.find("..") != std::string::npos ||
        name.find(char(92)) != std::string::npos ||
        name.find('\0') != std::string::npos) {
      error_json(fd, 404, "Unknown asset");
      return;
    }
    const std::string full = STATIC_DIR + "/" + name;
    const auto headers = std::vector<std::pair<std::string, std::string>>{
        {"Cache-Control", asset_cache_control(r.query, name)},
        {"Cross-Origin-Embedder-Policy", "require-corp"},
        {"Cross-Origin-Resource-Policy", "same-origin"}};
    // Voices and STT weights stay on disk. Reading them into a string allocated
    // the whole model inside the room process on a cold download.
    if (hashed_speech_asset(name)) {
      if (!send_file_response(fd, full, mime_for(name), headers))
        error_json(fd, 404, "Unknown asset");
      return;
    }
    bool ok;
    std::string data = read_file(full, &ok);
    if (!ok)
      error_json(fd, 404, "Unknown asset");
    else
      send_response(fd, 200, mime_for(name), data, headers);
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
    std::string mime = record.find("mime")->asStr();
    std::string disposition = (mime == "text/html" || mime == "image/svg+xml" ||
                               mime == "text/javascript") ? "attachment" : "inline";
    std::string csp = "sandbox";
    if (mime == "text/html" && qparam(r.query,"view") == "1") {
      disposition = "inline"; csp = "sandbox allow-scripts";
    }
    std::vector<std::pair<std::string,std::string>> hdr = {
      {"Content-Disposition", disposition + "; filename=\"" +
                                record.find("name")->asStr() + "\""},
      {"Content-Security-Policy", csp},
      {"Cache-Control","private, max-age=31536000, immutable"},
      {"Accept-Ranges","bytes"}};
    if (!send_file_range_response(fd, FILES_DIR + "/" + stored, mime,
                                  r.hdrs["range"], hdr))
      error_json(fd,404,"File missing");
    return;
  }
  if (path == "/api/frontend") {
    if (m == "GET")
      send_text(fd, 200, current_frontend(), "text/html; charset=utf-8");
    else if (m == "POST" || m == "DELETE")
      error_json(fd, 410, "Die Raum-Oberflaeche kommt nur aus dem Repository");
    else
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
    try { ensure_browser(vm); }
    catch (const std::exception &e) {
      error_json(fd, 503, "Browser-Start: " + one_line(e.what()).substr(0, 300));
      return;
    }
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
                                           "pointer", "state"};
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
  // Gives an existing chat-only bot its own VM (shell, browser, git) through
  // the same spawn path as a new bot; a failure keeps the bot chat-only.
  if (m == "POST" && path.rfind("/api/agents/", 0) == 0 && path.size() > 15 &&
      path.compare(path.size() - 3, 3, "/vm") == 0) {
    if (bridge || !actor.ok || actor.role != "owner") {
      error_json(fd, 403, "Nur der Besitzer");
      return;
    }
    const std::string id = url_decode(path.substr(12, path.size() - 15));
    auto busy = [&](int idx) {
      return !bot_field(idx, "vm").empty() || !bot_field(idx, "pending_vm").empty();
    };
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      int idx = agent_index(id);
      if (idx < 0) { error_json(fd, 404, "Unknown agent"); return; }
      if (busy(idx)) { error_json(fd, 409, "Bot hat bereits eine VM"); return; }
    }
    auto pre = run_argv({"sudo", "-n", AGENT_CTL, "preflight"}, 60);
    if (pre.code != 0) {
      error_json(fd, 503, "Bot-VM Vorprüfung fehlgeschlagen: " + trim(pre.out).substr(0, 400));
      return;
    }
    std::string vm;
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      int idx = agent_index(id);
      if (idx < 0) { error_json(fd, 404, "Unknown agent"); return; }
      if (busy(idx)) { error_json(fd, 409, "Bot hat bereits eine VM"); return; }
      vm = next_vm();
      AGENTS[idx]["pending_vm"] = vm;
      AGENTS[idx]["vm_attach"] = true;
      SPAWNING_VMS.insert(vm);
      save_agents();
    }
    agents_event();
    std::thread([id, vm] { finish_spawn(id, vm, false); }).detach();
    send_json(fd, 202, "{\"ok\":true,\"spawning\":true}");
    return;
  }
  if (path.rfind("/api/agents/", 0) == 0) {
    std::string id = url_decode(path.substr(12));
    auto slash = id.find('/');
    // Names are mutable metadata; the VM and stable bot ID never change.
    if (slash != std::string::npos && id.substr(slash + 1) == "name" &&
        m == "PUT") {
      if (bridge || !actor.ok || actor.role != "owner") {
        error_json(fd, 403, "Nur der Besitzer"); return;
      }
      id.resize(slash);
      Val input;
      if (!mj::parse(r.body, input) || !input.isObj() ||
          !input.find("name")) {
        error_json(fd, 400, "Bot-Name fehlt");
        return;
      }
      const std::string new_name = trim(input.find("name")->asStr());
      if (new_name.empty() || new_name.size() > 40 ||
          new_name.find('\n') != std::string::npos ||
          new_name.find('\r') != std::string::npos) {
        error_json(fd, 400, "Bot-Name muss 1–40 Zeichen lang sein");
        return;
      }
      {
        std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
        int idx = agent_index(id);
        if (idx < 0) { error_json(fd, 404, "Unbekannter Bot"); return; }
        for (size_t i = 0; i < AGENTS.size(); ++i)
          if ((int)i != idx && bot_field((int)i, "name") == new_name) {
            error_json(fd, 409, "Bot-Name bereits vergeben");
            return;
          }
        AGENTS[idx]["name"] = new_name;
        save_agents();
      }
      agents_event();
      Val out = Val::object(); out["ok"] = true;
      out["id"] = id; out["name"] = new_name;
      send_json(fd, 200, mj::dump(out));
      return;
    }

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
      Val previous = entry;
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
      memory_git_sync(id, m == "DELETE" ? "delete" : "edit");
      std::string uid = undo_memory_snapshot(
          id, (int)pos, previous, m == "DELETE" ? "memory_one" : "memory_edit");
      Val out = Val::object();
      out["ok"] = true;
      out["memory"] = mem;
      out["undo"] = uid;
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
    if (slash != std::string::npos && id.substr(slash + 1) == "memory" &&
        m == "POST") {
      id.resize(slash);
      Val body;
      std::string text;
      if (mj::parse(r.body, body) && body.isObj() && body.find("text"))
        text = trim(body.find("text")->asStr());
      if (text.empty() || text.size() > 500) {
        error_json(fd, 400, "Text required (max 500)");
        return;
      }
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      int idx = agent_index(id);
      if (idx < 0) {
        error_json(fd, 404, "Unknown agent");
        return;
      }
      if (auto mem = AGENTS[idx].find("memory"); mem && mem->isArr())
        for (auto &e : *mem->arr)
          if (memory_text(e) == text) {
            error_json(fd, 409, "Diese Erinnerung gibt es schon.");
            return;
          }
      remember_fact(id, text, "Nutzer");
      Val out = Val::object();
      out["ok"] = true;
      out["memory"] = AGENTS[idx]["memory"];
      send_json(fd, 200, mj::dump(out));
      return;
    }
    if (slash != std::string::npos && id.substr(slash + 1) == "memory" &&
        m == "DELETE") {
      id.resize(slash);
      auto result = clear_bot_memory(id);
      send_json(fd, result.code, result.body);
      return;
    }
    if (slash != std::string::npos &&
        id.substr(slash + 1) == "workspace/reset" && m == "POST") {
      id.resize(slash);
      Val body; (void)mj::parse(r.body, body);
      const bool force = body.isObj() && body.find("force") && body.find("force")->truthy();
      if (!force && work_pending()) { busy_json(fd); return; }
      auto result = reset_bot_workspace(id, force);
      send_json(fd, result.code, result.body);
      return;
    }
    if (slash != std::string::npos && id.substr(slash + 1) == "model" &&
        m == "POST") {
      id.resize(slash);
      if (DUAL_NUMA || CONFIGURED_MODEL) {
        error_json(fd, 409, CONFIGURED_MODEL ? "Die Modelle sind je Bot und NUMA-Knoten fest zugeordnet."
                                        : "NUMA-Modelle sind je Bot fest zugeordnet und bleiben geladen.");
        return;
      }
      Val choice;
      if (!mj::parse(r.body, choice) || !choice.isObj()) {
        error_json(fd, 400, "Modell fehlt");
        return;
      }
      std::string wanted;
      if (auto picked = choice.find("id")) wanted = picked->asStr();
      else if (auto picked = choice.find("model")) wanted = picked->asStr();
      if (!known_room_model(wanted)) {
        error_json(fd, 400, "Unbekanntes Modell");
        return;
      }
      {
        std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
        int idx = agent_index(id);
        if (idx < 0) {
          error_json(fd, 404, "Unknown agent");
          return;
        }
        AGENTS[idx]["model"] = wanted;
        save_agents();
      }
      agents_event();
      Val out = Val::object();
      out["ok"] = true;
      out["id"] = id;
      out["model"] = wanted;
      out["restart"] = false;
      send_json(fd, 200, mj::dump(out));
      return;
    }
    if (m == "DELETE") {
      if (DUAL_NUMA) {
        error_json(fd, 409, "Beide NUMA-Bots sind fest eingerichtet; Umbenennen ist möglich.");
        return;
      }
      if (!SINGLE_BOT.empty() && id == SINGLE_BOT) {
        error_json(fd, 409, "Der einzige Bot des Raums kann nicht entfernt werden (MAG_ROOM_SINGLE_BOT).");
        return;
      }
      stop_autotalk();
      std::lock_guard<std::shared_mutex> turn(TURN_MTX);
      int idx = agent_index(id);
      if (idx < 0) {
        error_json(fd, 404, "Unknown agent");
        return;
      }
      std::string vm = bot_field(idx, "vm");
      {
        // Recoverable copy of the bot record before anything is removed;
        // the VM workspace itself is archived by magbot despawn.
        std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
        if (!archive_bot_record(AGENTS[idx])) {
          error_json(fd, 500, "Bot-Archiv konnte nicht geschrieben werden; nichts entfernt.");
          return;
        }
      }
      if (!vm.empty()) {
        auto result = run_argv({"sudo", "-n", AGENT_CTL, "despawn", vm}, 180);
        if (result.code != 0) {
          error_json(fd, 502, result.out);
          return;
        }
      }
      {
        std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
        idx = agent_index(id);
        if (idx < 0) { send_json(fd, 200, "{\"ok\":true}"); return; }
        Val removed = AGENTS[idx];
        AGENTS.erase(AGENTS.begin() + idx);
        try { save_agents(); }
        catch (...) { AGENTS.insert(AGENTS.begin() + idx, removed); throw; }
      }
      {
        std::lock_guard<std::mutex> lk(VM_IP_MTX);
        VM_IP_CACHE.erase(vm);
      }
      drop_bot_refs(id);
      jobs_drop_bot(id);
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
    if (DUAL_NUMA) {
      error_json(fd, 409, "Maximal zwei Bots: Q4 auf NUMA0 und Qwen auf NUMA1.");
      return;
    }
    if (!SINGLE_BOT.empty()) {
      error_json(fd, 409, "Nur ein Bot erlaubt: Der Raum laeuft fest mit '" +
                              SINGLE_BOT_NAME + "' (MAG_ROOM_SINGLE_BOT).");
      return;
    }
    if (!has_json || trim(str("name")).empty() || str("name").size() > 40) {
      error_json(fd, 400, "Agent name required (1-40 bytes)");
      return;
    }
    const std::string bots_full =
        "Maximum 2 Bots erreicht";
    if (CONFIGURED_MODEL) {
      bool preset_ok = false;
      for (const auto &p : *ROOM_PRESETS.arr) preset_ok = preset_ok || mag::str(p, "id") == str("preset");
      if (!preset_ok) { error_json(fd, 400, "Bitte eine gültige Bot-Konstellation wählen."); return; }
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      if (!free_model_slot()) { error_json(fd, 409, bots_full); return; }
    }
    if (!CONFIGURED_MODEL && !str("model").empty() && !known_room_model(str("model"))) {
      error_json(fd, 400, "Unbekanntes Modell");
      return;
    }
    // "vm": false creates a chat-only bot (no KVM, no browser/terminal);
    // default remains a bot with its own isolated VM.
    const auto vm_flag = body.find("vm");
    const bool with_vm = !(vm_flag && vm_flag->type == Val::BOOL &&
                           !vm_flag->asBool());
    // One creation path. Preflight validates KVM, the golden image, the
    // network, the data directory and the libvirt tools before any room
    // entry, directory or domain exists. A later failure rolls all of that back.
    if (with_vm) {
      auto pre = run_argv({"sudo", "-n", AGENT_CTL, "preflight"}, 60);
      if (pre.code != 0) {
        error_json(fd, 503, "Bot-VM Vorprüfung fehlgeschlagen: " +
                                trim(pre.out).substr(0, 400));
        return;
      }
    }
    Val agent = Val::object();
    std::string id, vm;
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      const int slot = CONFIGURED_MODEL ? free_model_slot() : 0;
      if (CONFIGURED_MODEL && !slot) { error_json(fd, 409, bots_full); return; }
      id = slug_id(str("name"));
      vm = with_vm ? next_vm() : "";
      agent["id"] = id;
      agent["name"] = str("name");
      agent["vm"] = "";
      if (with_vm) agent["pending_vm"] = vm;
      agent["color"] = str("color", "#8fd0ff");
      agent["persona"] =
          str("persona", "Neugierig, freundlich und eigenstaendig.");
      agent["memory"] = Val::array();
      if (known_room_model(str("model"))) agent["model"] = str("model");
      if (CONFIGURED_MODEL) { agent["model"] = ROOM_MODEL.backend(slot).id; agent["slot"] = slot; agent["numa"] = ROOM_MODEL.backends[slot - 1].numa; }
      AGENTS.push_back(agent);
      try {
        save_agents();
        if (with_vm) SPAWNING_VMS.insert(vm);
      } catch (...) {
        AGENTS.pop_back();
        throw;
      }
    }
    agents_event();
    mcp_bind_new_bot(id);
    if (with_vm)
      std::thread([id, vm] { finish_spawn(id, vm, false); }).detach();
    Val out = agent;
    if (with_vm) out["spawning"] = true;
    send_json(fd, with_vm ? 202 : 201, mj::dump(out));
    return;
  }
  if (m == "POST" && (path == "/api/message" || path == "/api/stream")) {
    if (!has_json || trim(str("text")).empty() || str("text").size() > 32768) {
      error_json(fd, 400, "Message required (max 32 KiB)");
      return;
    }
    auto ids = selected_ids(body);
    const std::string command = trim(str("text"));
    if (room_is_glm() &&
        (command.rfind("/merk ", 0) == 0 || command.rfind("/vergiss ", 0) == 0)) {
      if (actor.role != "owner") {
        error_json(fd, 403, "Nur der Owner darf Erinnerungen ändern"); return;
      }
      if (ids.size() != 1) {
        error_json(fd, 400, "Für Erinnerungen genau einen Bot auswählen"); return;
      }
      const std::string id = ids.front();
      if (command.rfind("/merk ", 0) == 0) {
        std::string fact = trim(command.substr(6));
        if (fact.empty() || fact.size() > 500) {
          error_json(fd, 400, "Fakt erforderlich (max 500 Bytes)"); return;
        }
        remember_fact(id, fact, "Raumbefehl");
      } else {
        const std::string n = trim(command.substr(8));
        if (n.empty() || n.find_first_not_of("0123456789") != std::string::npos ||
            n.size() > 2) {
          error_json(fd, 400, "Verwende /vergiss <n>"); return;
        }
        const size_t index = (size_t)std::stoul(n);
        std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
        const int idx = agent_index(id);
        if (idx < 0 || !AGENTS[idx]["memory"].isArr() ||
            index < 1 || index > AGENTS[idx]["memory"].arr->size()) {
          error_json(fd, 404, "Erinnerung nicht gefunden"); return;
        }
        auto &mem = *AGENTS[idx]["memory"].arr;
        mem.erase(mem.begin() + (std::ptrdiff_t)(index - 1));
        save_agents();
        memory_git_sync(id, "forget");
      }
      send_json(fd, 200, "{\"ok\":true,\"handled_by\":\"host\"}");
      return;
    }
    if (ids.empty()) {
      error_json(fd, 400, "Select an agent");
      return;
    }
    int think = -1;
    if (auto th = body.find("think"); th && th->type == Val::BOOL)
      think = th->asBool() ? 1 : 0;
    bool interrupt = false;
    if (auto in = body.find("interrupt"); in && in->type == Val::BOOL)
      interrupt = in->asBool();
    const std::string request_id = str("request_id");
    if (!request_id.empty() && (request_id.size() > 80 || request_id.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-") != std::string::npos)) {
      error_json(fd, 400, "Invalid request_id"); return;
    }
    send_json(fd, 202, mj::dump(enqueue_pending(str("text"), ids, think, actor.name, actor.login, interrupt, request_id)));
    return;
  }

  if (path == "/api/queue" || path.rfind("/api/queue/", 0) == 0) {
    std::string rest = path.size() > 11 ? path.substr(11) : "";
    if (path == "/api/queue" && m == "GET") {
      std::lock_guard<std::mutex> lk(JOB_MTX);
      send_json(fd, 200, mj::dump(queue_view_locked()));
      return;
    }
    if (path == "/api/queue" && m == "DELETE") { queue_clear(); send_json(fd, 200, "{\"ok\":true}"); return; }
    if (rest == "merge" && m == "POST") {
      if (!queue_merge()) { error_json(fd, 409, "Nichts zum Zusammenführen"); return; }
      send_json(fd, 200, "{\"ok\":true}"); return;
    }
    if (rest == "pause" && m == "POST") { queue_pause(); send_json(fd, 200, "{\"ok\":true}"); return; }
    if (rest == "resume" && m == "POST") { queue_resume(); send_json(fd, 200, "{\"ok\":true}"); return; }
    std::string qid = rest, action;
    if (auto sl = rest.find('/'); sl != std::string::npos) { qid = rest.substr(0, sl); action = rest.substr(sl + 1); }
    // Every queued mutation (including move/now) respects message ownership.
    // Otherwise a guest could forcibly interrupt another user's bot.
    if (!bridge && actor.role != "owner" &&
        ((action == "move" || action == "now") && m == "POST" ||
         action.empty() && (m == "PUT" || m == "DELETE"))) {
      std::string owner_login;
      if (!pending_sender(qid, owner_login) || owner_login.empty() ||
          owner_login != actor.login) {
        error_json(fd, 403, "Nur eigene Nachrichten");
        return;
      }
    }
    bool ok = false;
    if (action == "move" && m == "POST") {
      const Val *target = has_json ? body.find("before") : nullptr;
      if (!target || target->type != Val::STR ||
          target->asStr().size() > 128) {
        error_json(fd, 400, "before must be a pending ID or empty string"); return;
      }
      ok = queue_move(qid, target->asStr());
    }
    else if (action == "now" && m == "POST") ok = queue_send_now(qid);
    else if (action.empty() && m == "DELETE") ok = queue_delete(qid);
    else if (action.empty() && m == "PUT") {
      std::string text = has_json ? trim(str("text")) : "";
      if (text.empty() || text.size() > 32768) { error_json(fd, 400, "Text required (max 32 KiB)"); return; }
      ok = queue_edit(qid, text);
    } else { error_json(fd, 405, "Method not allowed"); return; }
    if (!ok) {
      // Queue items can be claimed by the scheduler between painting the
      // chip and a click. This is a state conflict, NOT an unknown resource
      // and must never trigger a second send.
      Val conflict = Val::object();
      conflict["error"] = "Warteschlange hat sich geändert";
      conflict["status"] = "stale";
      {
        std::lock_guard<std::mutex> lk(JOB_MTX);
        conflict["queue"] = queue_view_locked();
      }
      send_json(fd, 409, mj::dump(conflict));
      return;
    }
    Val result = Val::object();
    result["ok"] = true;
    result["status"] = action == "now" ? "prioritized" : "updated";
    {
      std::lock_guard<std::mutex> lk(JOB_MTX);
      result["queue"] = queue_view_locked();
    }
    send_json(fd, 200, mj::dump(result));
    return;
  }
  // Owner-controlled metadata only: stable model IDs and NUMA routing are untouched.
  if (m == "POST" && path == "/api/model-settings") {
    if (bridge || !actor.ok || actor.role != "owner") {
      error_json(fd, 403, "Nur der Besitzer"); return;
    }
    if (!has_json || !body.find("id") ||
        body.find("id")->type != Val::STR) {
      error_json(fd, 400, "Modell-ID fehlt"); return;
    }
    const std::string id = body.find("id")->asStr();
    if (!known_room_model(id)) {
      error_json(fd, 400, "Unbekanntes Modell"); return;
    }
    const Val *name_input = body.find("name");
    const Val *det_input = body.find("deterministic");
    if (!name_input && !det_input) {
      error_json(fd, 400, "Name oder deterministic erforderlich"); return;
    }
    std::string name;
    if (name_input) {
      if (name_input->type != Val::STR) {
        error_json(fd, 400, "Name muss Text sein"); return;
      }
      name = trim(name_input->asStr());
      bool invalid = name.empty() || name.size() > 40;
      for (unsigned char c : name)
        if (c < 32 || c == 127) invalid = true;
      if (invalid) {
        error_json(fd, 400, "Name muss 1–40 Zeichen lang sein (keine Steuerzeichen)"); return;
      }
    }
    bool deterministic = false;
    if (det_input) {
      if (det_input->type != Val::BOOL) {
        error_json(fd, 400, "Boolean deterministic erforderlich"); return;
      }
      deterministic = det_input->asBool();
    }
    if (!save_model_settings(id, name_input ? &name : nullptr,
                             det_input ? &deterministic : nullptr)) {
      error_json(fd, 500, "Modell-Einstellungen konnten nicht gespeichert werden"); return;
    }
    Val out = Val::object();
    out["ok"] = true;
    out["type"] = "model_settings";
    out["models"] = room_model_catalog();
    publish(mj::dump(out));
    send_json(fd, 200, mj::dump(out));
    return;
  }
  // Deterministic siblings: first-token alternatives within `radius` nats.
  if (m == "POST" && path == "/api/model-verify") {
    if (bridge || !actor.ok || actor.role != "owner") {
      error_json(fd, 403, "Nur der Besitzer"); return;
    }
    const Val *id = has_json ? body.find("model") : nullptr;
    if (!id || id->type != Val::STR || !known_room_model(id->asStr())) {
      error_json(fd, 400, "Unbekanntes Modell"); return;
    }
    std::string err;
    if (!start_model_verify(id->asStr(), err)) { error_json(fd, 409, err); return; }
    send_json(fd, 202, "{\"ok\":true}");
    return;
  }
  if (m == "POST" && path == "/api/model-radius") {
    if (bridge || !actor.ok || actor.role != "owner") {
      error_json(fd, 403, "Nur der Besitzer"); return;
    }
    const Val *id = has_json ? body.find("model") : nullptr;
    const Val *in = has_json ? body.find("input") : nullptr;
    if (!id || id->type != Val::STR || !known_room_model(id->asStr())) {
      error_json(fd, 400, "Unbekanntes Modell"); return;
    }
    if (!in || in->type != Val::STR || trim(in->asStr()).empty() ||
        in->asStr().size() > 8000) {
      error_json(fd, 400, "Eingabe muss 1–8000 Bytes Text sein"); return;
    }
    auto number = [&](const char *key, double def, double lo, double hi) {
      const Val *v = body.find(key);
      double x = v && v->type == Val::NUM ? v->asNum() : def;
      return std::isfinite(x) ? std::clamp(x, lo, hi) : def;
    };
    try {
      Val out = model_radius(id->asStr(), in->asStr(), number("radius", 2.0, 0.0, 10.0),
                             (int)number("k", 5, 2, 8), (int)number("max_tokens", 48, 8, 256),
                             (int)number("repeat", 2, 1, 3));
      send_json(fd, 200, mj::dump(out));
    } catch (const RadiusError &e) {
      error_json(fd, e.code, e.what());
    } catch (const std::exception &e) {
      error_json(fd, 502, e.what());
    }
    return;
  }
  if (path == "/api/sampling") {
    error_json(fd, 410, "Bitte Sampling pro Modell konfigurieren"); return;
  }
  if (m == "POST" && path == "/api/reflect") {
    // Owner room setting: optional reflection round after concurrent answers.
    if (bridge || !actor.ok || actor.role != "owner") {
      error_json(fd, 403, "Nur der Besitzer");
      return;
    }
    if (!has_json || !body.find("on") || body.find("on")->type != Val::BOOL) {
      error_json(fd, 400, "Boolean on required");
      return;
    }
    REFLECT = body.find("on")->asBool();
    write_file(STATE_DIR + "/reflect", REFLECT.load() ? "on" : "off");
    Val e = Val::object();
    e["type"] = "reflect";
    e["on"] = REFLECT.load();
    publish(mj::dump(e));
    send_json(fd, 200, mj::dump(e));
    return;
  }
  if (m == "POST" && (path == "/api/selftalk" || path == "/api/proaktiv")) {
    if (!has_json || !body.find("on") || body.find("on")->type != Val::BOOL) {
      error_json(fd, 400, "Boolean on required");
      return;
    }
    if (body.find("on")->asBool())
      start_selftalk();
    else
      stop_selftalk();
    Val e = Val::object();
    e["type"] = "proaktiv";
    e["on"] = SELFTALK.load();
    e["proaktiv"] = SELFTALK.load();
    e["selftalk"] = SELFTALK.load();
    publish(mj::dump(e));
    send_json(fd, 200, mj::dump(e));
    return;
  }
  if (m == "POST" && path == "/api/deploy-hold") {
    if (actor.role != "owner") {
      error_json(fd, 403, "owner required");
      return;
    }
    const bool on = !has_json || !body.find("on") || body.find("on")->asBool();
    if (on)
      hold_selftalk_for_deploy();
    else
      release_deploy_hold();
    Val e = Val::object();
    e["type"] = "deploy-hold";
    e["on"] = DEPLOY_HOLD.load();
    e["selftalk"] = SELFTALK.load();
    e["proaktiv"] = SELFTALK.load();
    e["held"] = SELF_HOLD.load();
    send_json(fd, 200, mj::dump(e));
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
  if (path == "/api/jobs" || path.rfind("/api/jobs/", 0) == 0) {
    // Same authoritative request path for UI and the in-process MCP tools.
    JobsResponse response = jobs_request(m, path, body, has_json, JobsActor{});
    send_json(fd, response.status, mj::dump(response.value));
    return;
  }
  if (m == "POST" && path == "/api/offline") {
    bool off = has_json && body.find("on") && body.find("on")->asBool(false);
    if (!set_offline(off)) {
      error_json(fd, 500, "Netzwerkregel konnte nicht gesetzt werden");
      return;
    }
    send_json(fd, 200, std::string("{\"offline\":") + (off ? "true" : "false") + "}");
    return;
  }
  if (m == "POST" && path == "/api/cancel") {
    // Composer Stop (Esc): every running turn ends, the llama slots are
    // cancelled and their saved prefix is restored, and the queue is cleared.
    // Optional "bot": stop only that bot's lane; the others keep answering.
    const std::string bot = has_json ? str("bot") : "";
    if (!bot.empty()) {
      const bool found = cancel_bots({bot}, true);
      const int idx = agent_index(bot);
      cancel_llama_slot(idx);
      reload_slot_after_cancel(idx);
      {
        std::lock_guard<std::mutex> lk(JOB_MTX);
        if (AUTO_OWNER == bot) { AUTO_READY = false; AUTO_TURNS_LEFT = 0; }
      }
      send_json(fd, 200, found ? "{\"cancelled\":true}" : "{\"cancelled\":false}");
      return;
    }
    send_json(fd, 200, mj::dump(user_stop(false)));
    return;
  }
  if (path == "/api/secrets" || path.rfind("/api/secrets/", 0) == 0) {
    // Owner only. Values go in, never out.
    if (actor.role != "owner") { error_json(fd, 403, "Nur der Besitzer"); return; }
    if (m == "GET" && path == "/api/secrets") {
      Val out = Val::object(); out["secrets"] = secrets_public();
      send_json(fd, 200, mj::dump(out)); return;
    }
    if (m == "POST" && path.rfind("/api/secrets/requests/", 0) == 0) {
      try {
        if (!secret_fulfil(path.substr(22), has_json ? str("value") : "")) { error_json(fd, 404, "Anfrage nicht mehr offen"); return; }
      } catch (const std::exception &e) { error_json(fd, 400, e.what()); return; }
      send_json(fd, 200, "{\"ok\":true}"); return;
    }
    std::string rest = path.size() > 13 ? path.substr(13) : "";
    const bool bots = rest.size() > 5 && rest.compare(rest.size() - 5, 5, "/bots") == 0;
    if (bots) rest.resize(rest.size() - 5);
    const std::string name = secret_name_ok(url_decode(rest));
    if (name.empty()) { error_json(fd, 400, "Ungültiger Name"); return; }
    if (m == "PUT" && bots) {
      auto b = has_json ? body.find("bots") : nullptr;
      if (!b || !b->isArr() || !secret_set_bots(name, *b)) { error_json(fd, 404, "Unbekanntes Secret"); return; }
      send_json(fd, 200, "{\"ok\":true}"); return;
    }
    if (m == "PUT") {
      try { secret_store(name, has_json ? str("value") : "", has_json ? str("purpose") : "", ""); }
      catch (const std::exception &e) { error_json(fd, 400, e.what()); return; }
      send_json(fd, 200, "{\"ok\":true}"); return;
    }
    if (m == "DELETE") {
      if (!secret_delete(name)) { error_json(fd, 404, "Unbekanntes Secret"); return; }
      send_json(fd, 200, "{\"ok\":true}"); return;
    }
    error_json(fd, 405, "Methode nicht erlaubt"); return;
  }
  if (m == "GET" && path == "/api/numa") {
    send_json(fd, 200, mj::dump(numa_public()));
    return;
  }
  if (m == "POST" && path == "/api/numa/swap") {
    // Explicit owner action after the confirm dialog; never automatic.
    if (actor.role != "owner") { error_json(fd, 403, "Nur der Besitzer"); return; }
    auto node = has_json ? body.find("node") : nullptr;
    auto r = numa_swap(node ? (int)node->asNum(-1) : -1, has_json ? str("model") : "",
                       has_json && body.find("confirm") && body.find("confirm")->asBool(false));
    if (r.code >= 400) { error_json(fd, r.code, r.error); return; }
    Val out = numa_public();
    out["accepted"] = r.code == 202;
    send_json(fd, r.code, mj::dump(out));
    return;
  }
  if (m == "PUT" && path.rfind("/api/agents/", 0) == 0 && path.size() > 17 &&
      path.compare(path.size() - 5, 5, "/numa") == 0) {
    if (actor.role != "owner") { error_json(fd, 403, "Nur der Besitzer"); return; }
    const std::string id = url_decode(path.substr(12, path.size() - 17));
    auto node = has_json ? body.find("node") : nullptr;
    if (!numa_assign(id, node ? (int)node->asNum(-1) : -1)) { error_json(fd, 400, "Ungültige Zuordnung"); return; }
    send_json(fd, 200, mj::dump(numa_public()));
    return;
  }
  if (m == "GET" && path == "/api/tasks") {
    Val out = Val::object();
    out["tasks"] = tasks_public(qparam(r.query, "bot"));
    send_json(fd, 200, mj::dump(out));
    return;
  }
  if (m == "POST" && path.rfind("/api/tasks/", 0) == 0 &&
      path.size() > 18 && path.compare(path.size() - 7, 7, "/resume") == 0) {
    if (actor.role != "owner") { error_json(fd, 403, "Nur der Besitzer darf Aufgaben fortsetzen"); return; }
    const std::string id = url_decode(path.substr(11, path.size() - 18));
    send_json(fd, 200, task_resume(id) ? "{\"resumed\":true}" : "{\"resumed\":false}");
    return;
  }
  if (m == "POST" && path.rfind("/api/tasks/", 0) == 0 &&
      path.size() > 18 && path.compare(path.size() - 7, 7, "/cancel") == 0) {
    const std::string id = path.substr(11, path.size() - 18);
    send_json(fd, 200, task_cancel(id) ? "{\"cancelled\":true}" : "{\"cancelled\":false}");
    return;
  }
  if (m == "GET" && path.rfind("/api/todo/", 0) == 0) {
    send_json(fd, 200, mj::dump(todo_public(url_decode(path.substr(10)))));
    return;
  }
  if (m == "GET" && path.rfind("/api/journal/", 0) == 0) {
    const std::string id = url_decode(path.substr(13));
    if (agent_index(id) < 0) { error_json(fd, 404, "Unknown agent"); return; }
    Val arr = Val::array();
    {
      std::lock_guard<std::mutex> lk(JOURNAL_MTX);
      for (auto &row : journal_read(id)) arr.push(row);
    }
    Val out = Val::object();
    out["journal"] = arr;
    send_json(fd, 200, mj::dump(out));
    return;
  }
  if (m == "POST" && path == "/api/stop") {
    // "Alles anhalten": like Stop, and scheduled jobs and the autonomy
    // switch stay off until the user resumes.
    Val out = user_stop(true);
    out["stopped"] = true;
    send_json(fd, 200, mj::dump(out));
    return;
  }
  if (m == "POST" && path == "/api/reset") {
    if (actor.role != "owner") { error_json(fd,403,"Nur der Besitzer"); return; }
    stop_autotalk();
    std::lock_guard<std::shared_mutex> turn(TURN_MTX);
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
    retire_frontend_override();
    undo_drop_all();
    Val e = Val::object();
    e["type"] = "reset";
    publish(mj::dump(e));
    send_json(fd, 200, "{\"ok\":true,\"memory_preserved\":true}");
    return;
  }
  if (!bridge && (path == "/api/room/snapshots" ||
      path.rfind("/api/room/snapshots/", 0) == 0)) {
    // Direct local HTTP without a whois/HMAC attestation is NOT an Owner
    // capability for durable history restore.
    if (actor.role != "owner" || actor.login.empty() || !ts_proof_valid(r.hdrs)) {
      error_json(fd,403,"Nur der Besitzer"); return;
    }
    if (path == "/api/room/snapshots" && m == "GET") {
      send_json(fd,200,mj::dump(snapshot_overview())); return;
    }
    const std::string root="/api/room/snapshots/", suffix="/restore";
    if (m == "POST" && path.rfind(root,0)==0 &&
        path.size()>root.size()+suffix.size() &&
        path.compare(path.size()-suffix.size(),suffix.size(),suffix)==0) {
      auto id=path.substr(root.size(),path.size()-root.size()-suffix.size());
      auto tok=r.hdrs.find("x-mag-token");
      auto origin=r.hdrs.find("origin");
      auto fetch_site=r.hdrs.find("sec-fetch-site");
      if (tok==r.hdrs.end() || !ct_eq(tok->second,ui_token()) ||
          origin==r.hdrs.end() || origin->second!="https://mag.stardenburg.de" ||
          fetch_site==r.hdrs.end() || fetch_site->second!="same-origin") {
        error_json(fd,403,"CSRF-Prüfung fehlgeschlagen"); return;
      }
      if (BUSY.load()) { error_json(fd,409,"Bot arbeitet noch"); return; }
      std::lock_guard<std::mutex> scheduling(JOB_MTX);
      std::unique_lock<std::shared_mutex> turn(TURN_MTX, std::try_to_lock);
      if (!turn.owns_lock() || BUSY.load()) { error_json(fd,409,"Bot arbeitet noch"); return; }
      auto result=snapshot_restore(id);
      if (result.code == 200) {
        JOBS.clear(); PENDING.clear(); QUEUE_PAUSED = false;
        AUTO_READY = false; AUTO_OWNER.clear(); ++AUTO_GENERATION;
        save_queue_locked(); queue_event_locked();
        guest_audit("room_restore",actor.login,id);
      }
      send_json(fd,result.code,result.body);
      return;
    }
    error_json(fd,405,"Methode nicht erlaubt");
    return;
  }
  if (m == "POST" && path == "/api/room/clear") {
    if (actor.role != "owner" || actor.login.empty() || !ts_proof_valid(r.hdrs)) {
      error_json(fd, 403, "Nur der Besitzer"); return;
    }
    // Every clear is forced: abort all in-flight turns before touching data.
    // Fail closed if a worker has not released its lane; never send false 200.
    std::unique_lock<std::mutex> scheduling;
    std::unique_lock<std::shared_mutex> turn;
    if (!quiesce_all(scheduling, turn, true)) {
      error_json(fd, 503, "Bots werden noch gestoppt – Raum nicht geleert");
      return;
    }
    JOBS.clear(); PENDING.clear(); PARKED_JOBS.clear(); JOBRUNS.clear();
    ACTIVE_JOBS.clear(); QUEUE_PAUSED = false;
    AUTO_READY = false; AUTO_OWNER.clear(); ++AUTO_GENERATION;
    AUTO_LAST_PROGRESS.clear(); AUTO_TURNS_LEFT = 0;
    save_queue_locked(); queue_event_locked();
    clear_runs();
    auto result = clear_room_data();
    if (result.code == 200) {
      guest_audit("room_clear", actor.login, "hard");
      publish("{\"type\":\"room_cleared\"}");
    }
    send_json(fd, result.code, result.body);
    return;
  }
  if ((m == "POST" || m == "DELETE") && path.rfind("/api/messages/", 0) == 0 &&
      path.size() > std::string("/api/messages//reactions").size() &&
      path.compare(path.size() - 10, 10, "/reactions") == 0) {
    std::string id = url_decode(path.substr(
        std::string("/api/messages/").size(),
        path.size() - std::string("/api/messages/").size() - 10));
    std::string emoji = has_json ? str("emoji") : "";
    if (emoji.empty())
      emoji = qparam(r.query, "emoji");
    ReactResult result = apply_reaction(
        id, "user", emoji, m == "DELETE" ? ReactMode::Remove : ReactMode::Toggle);
    if (result.code != 200) {
      error_json(fd, result.code, result.error);
      return;
    }
    // Default: a human reaction does not start a model turn. MAG_ROOM_REACT_WAKE
    // opts into spending one run so the bot sees the note immediately.
    if (result.added && !result.wake_bot.empty() && react_wake_enabled())
      schedule({result.wake_bot});
    send_json(fd, 200, result.json);
    return;
  }
  if (m == "DELETE" && path.rfind("/api/room/messages/", 0) == 0) {
    auto result = delete_room_message(url_decode(path.substr(std::string("/api/room/messages/").size())));
    send_json(fd, result.code, result.body);
    return;
  }
  if (m == "DELETE" && path.rfind("/api/room/files/", 0) == 0) {
    if (actor.role != "owner" || actor.login.empty() || !ts_proof_valid(r.hdrs)) {
      error_json(fd, 403, "Nur der Besitzer"); return;
    }
    auto result = delete_room_file(url_decode(path.substr(std::string("/api/room/files/").size())));
    send_json(fd, result.code, result.body);
    return;
  }
  if (m == "POST" && path.rfind("/api/undo/", 0) == 0) {
    auto result = undo_action(url_decode(path.substr(std::string("/api/undo/").size())));
    send_json(fd, result.code, result.body);
    return;
  }
  // Large file protocol: chunks are independent requests below the HTTP
  // body limit. A session is owned by the authenticated room identity.
  // The file enters FILES/ROOM exactly once, only after commit succeeds.
  if (path == "/api/upload/chunk" ||
      path == "/api/upload/chunk/status" ||
      path == "/api/upload/chunk/commit") {
    const std::string owner = actor.role + ":" + actor.login + ":" + actor.node;
    const std::string id = qparam(r.query, "id");
    if (!upload_valid_id(id)) { error_json(fd, 400, "Ungültige Upload-ID"); return; }
    auto number = [&](const char *key) -> uint64_t {
      const std::string v = qparam(r.query, key);
      if (v.empty() || v.size() > 20 ||
          v.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("Ungültige Upload-Länge");
      return std::stoull(v);
    };
    try {
      if (m == "GET" && path == "/api/upload/chunk/status") {
        send_json(fd, 200, mj::dump(upload_status(id,owner))); return;
      }
      if (m == "POST" && path == "/api/upload/chunk") {
        const uint64_t total = number("total"), offset = number("offset");
        const std::string who = actor.name.empty() ? "Nutzer" : actor.name;
        send_json(fd, 200, mj::dump(upload_chunk(id, qparam(r.query,"name"),
          total, offset, r.body, owner, who)));
        return;
      }
      if (m == "POST" && path == "/api/upload/chunk/commit") {
        if (!r.body.empty()) { error_json(fd,400,"Commit darf keinen Body haben");return; }
        bool fresh = false;
        const Val file = upload_commit(id, owner, fresh);
        if (fresh) {
          const std::string who = actor.name.empty() ? "Nutzer" : actor.name;
          append_room("user", who, "", "", &file, actor.login);
          emit_file("", file);
        }
        send_json(fd, 201, mj::dump(file)); return;
      }
      error_json(fd,405,"Method not allowed"); return;
    } catch (const std::exception &e) {
      const std::string msg = e.what();
      const int code = msg.find("nicht gefunden") != std::string::npos ? 404 :
                       msg.find("Maximal") != std::string::npos ? 429 :
                       msg.find("unvollständig") != std::string::npos ||
                       msg.find("Offset") != std::string::npos ||
                       msg.find("andere Anfrage") != std::string::npos ||
                       msg.find("abweicht") != std::string::npos ||
                       msg.find("weicht ab") != std::string::npos ||
                       msg.find("Überlappender") != std::string::npos ? 409 :
                       msg.find("Schreibfehler") != std::string::npos ||
                       msg.find("Sicherung") != std::string::npos ? 500 : 400;
      error_json(fd,code,msg); return;
    }
  }
  if (m == "POST" && path == "/api/upload") {
    std::string name = qparam(r.query, "name");
    if (name.empty())
      name = r.hdrs["x-filename"];
    if (r.body.empty()) {
      error_json(fd, 400, "Empty file");
      return;
    }
    std::string who = actor.name.empty() ? "Nutzer" : actor.name;
    Val f = add_file(name.empty() ? "upload" : name, r.body, who);
    append_room("user", who, "", "", &f, actor.login);
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
      std::unique_lock<std::shared_mutex> turn(TURN_MTX, std::try_to_lock);
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
                              false, slot_for_key("stt"));
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
    if (trim(str("text")).empty()) {
      error_json(fd, 400, "Text required");
      return;
    }
    Val msg = voice_message("Sprachnachricht", "", str("text"));
    emit_voice(msg);
    send_json(fd, 201, mj::dump(msg));
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
// The nginx auth_request runs on THIS native server. If SSE and slow VM
// requests exhaust the normal connection pool, rejecting whois with HTTP 503
// denies the entire Tailnet UI even though the service and model are healthy.
// Reserve independent capacity for the local, security-checked whois route.
// The global cap still bounds pre-header sockets and prevents unlimited threads.
static std::atomic<int> CONNECTIONS{0}, NORMAL_CONNECTIONS{0}, WHOIS_CONNECTIONS{0};
static void serve_connection(int fd, bool bridge) {
  const int normal_limit = std::clamp(envi("MAG_ROOM_HTTP_CONNECTIONS", 256), 8, 512);
  constexpr int WHOIS_LIMIT = 48;
  constexpr int HEADER_RESERVE = 48;
  const int accepted = ++CONNECTIONS;
  if (accepted > normal_limit + WHOIS_LIMIT + HEADER_RESERVE) {
    error_json(fd, 503, "Connection limit");
    --CONNECTIONS;
    close(fd);
    return;
  }
  timeval timeout{accepted > normal_limit ? 5 : 30, 0};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  try {
    Req r;
    if (read_request(fd, r)) {
      const bool whois = !bridge && r.path == "/_internal/tailscale/whois";
      std::atomic<int> &pool = whois ? WHOIS_CONNECTIONS : NORMAL_CONNECTIONS;
      const int active = ++pool;
      if (active > (whois ? WHOIS_LIMIT : normal_limit)) {
        error_json(fd, 503, whois ? "Whois capacity exceeded" : "Connection limit");
        if (!whois) std::cerr << "http regular connection limit reached: "
                              << active << "/" << normal_limit
                              << "; reserved whois=" << WHOIS_CONNECTIONS.load() << "\n";
      } else {
        route(fd, r, bridge); // native auth still validates real Tailnet identity
      }
      --pool;
    }
  } catch (const std::exception &e) {
    error_json(fd, 500, e.what());
  }
  close(fd);
  --CONNECTIONS;
}


