// ---------------------------------------------------------------- NUMA models
// Each NUMA node serves exactly one catalog model (ops/numa/catalog.json);
// bots are assigned to nodes (persisted). The owner swaps a node's model
// explicitly after a confirm dialog; never automatically. The privileged
// mag-numa-swap tool does unload -> load -> health -> commit/rollback and
// writes the node state file that the room and the gateway read. While a
// node swaps, its bots are not dispatched: their messages queue.
static std::mutex NUMA_MTX;
static Val NUMA_CATALOG;            // parsed catalog (NUMA_MTX)
static std::map<std::string, int> NUMA_ASSIGN; // bot id -> node (NUMA_MTX)
static int NUMA_SWAP_NODE = -1;     // node being swapped (NUMA_MTX)
static std::string NUMA_SWAP_TARGET, NUMA_LAST_ERROR;
static Val NUMA_STATE_CACHE;
static int64_t NUMA_STATE_AT = 0;

static std::string numa_state_path() { return env("MAG_NUMA_STATE", "/var/lib/mag-numa/state.json"); }
static std::string numa_assign_path() { return STATE_DIR + "/numa-assign.json"; }

static void numa_load() {
  const std::string path = env("MAG_NUMA_CATALOG", "");
  Val c;
  if (path.empty() || !mj::parse(read_file(path), c) || !c.isObj() || !c.find("nodes") ||
      !c.find("models") || c["nodes"].asArr().size() != 2)
    return;
  std::lock_guard<std::mutex> lk(NUMA_MTX);
  NUMA_CATALOG = c;
  NUMA_ON = true;
  Val a;
  if (mj::parse(read_file(numa_assign_path()), a) && a.isObj())
    for (auto &kv : *a.obj) NUMA_ASSIGN[kv.first] = (int)kv.second.asNum(0) == 1 ? 1 : 0;
}
static const Val *numa_model_locked(const std::string &id) {
  for (auto &m : NUMA_CATALOG["models"].asArr())
    if (m.find("id") && m.find("id")->asStr() == id) return &m;
  return nullptr;
}
static const Val *numa_node_locked(int n) {
  for (auto &x : NUMA_CATALOG["nodes"].asArr())
    if ((int)x.find("node")->asNum(-1) == n) return &x;
  return nullptr;
}
// Node state from mag-numa-swap (re-read at most every second).
static Val numa_node_state_locked(int n) {
  if (now_s() != NUMA_STATE_AT) {
    Val s;
    NUMA_STATE_CACHE = mj::parse(read_file(numa_state_path()), s) && s.isObj() ? s : Val::object();
    NUMA_STATE_AT = now_s();
  }
  Val out = Val::object();
  if (auto nodes = NUMA_STATE_CACHE.find("nodes"))
    if (auto ns = nodes->find(std::to_string(n)); ns && ns->isObj()) out = *ns;
  if (!out.find("model") || out["model"].asStr().empty()) {
    const Val *nc = numa_node_locked(n);
    out["model"] = nc && nc->find("default") ? nc->find("default")->asStr() : "";
    if (!out.find("state")) out["state"] = "serving";
  }
  return out;
}
static std::string numa_node_model(int n) {
  std::lock_guard<std::mutex> lk(NUMA_MTX);
  return numa_node_state_locked(n)["model"].asStr();
}
static int numa_bot_node(int idx) {
  const std::string id = bot_field(idx, "id");
  std::lock_guard<std::mutex> lk(NUMA_MTX);
  auto it = NUMA_ASSIGN.find(id);
  return it != NUMA_ASSIGN.end() ? it->second : (idx % 2);
}
static std::string numa_bot_model(int idx) { return numa_node_model(numa_bot_node(idx)); }
static bool numa_bot_swapping(const std::string &bot_id) {
  if (!NUMA_ON) return false;
  int idx = agent_index(bot_id);
  if (idx < 0) return false;
  const int node = numa_bot_node(idx);
  std::lock_guard<std::mutex> lk(NUMA_MTX);
  return NUMA_SWAP_NODE == node;
}
static bool numa_known_model(const std::string &id) {
  std::lock_guard<std::mutex> lk(NUMA_MTX);
  return numa_model_locked(id) != nullptr;
}
static std::string numa_label(const std::string &id) {
  std::lock_guard<std::mutex> lk(NUMA_MTX);
  for (int n = 0; n < 2; n++)
    if (numa_node_state_locked(n)["model"].asStr() == id)
      if (const Val *m = numa_model_locked(id))
        return std::to_string(n) + " · " + m->find("short")->asStr();
  const Val *m = numa_model_locked(id);
  return m ? m->find("short")->asStr() : id;
}
// "" = fits; otherwise a refusal for the user.
static std::string numa_admit_locked(int n, const Val &m) {
  const Val *nc = numa_node_locked(n);
  const double ram = m.find("ram_gib")->asNum(), budget = nc->find("budget_gib")->asNum();
  const double reserve = nc->find("reserve_gib") ? nc->find("reserve_gib")->asNum() : 0;
  auto g = [](double v) { return std::to_string((long)v) + " GB"; };
  if (ram + reserve > budget)
    return m.find("label")->asStr() + " braucht " + g(ram) + " (+" + g(reserve) + " Reserve), NUMA " +
           std::to_string(n) + " hat " + g(budget) + ". Passt nicht.";
  double other = 0;
  if (const Val *om = numa_model_locked(numa_node_state_locked(1 - n)["model"].asStr()))
    other = om->find("ram_gib")->asNum();
  const double limit = NUMA_CATALOG["host"].find("limit_gib")->asNum();
  if (ram + other > limit)
    return "Zusammen mit dem Modell auf NUMA " + std::to_string(1 - n) + " (" + g(other) +
           ") wäre das Host-Limit von " + g(limit) + " überschritten.";
  return "";
}
static Val numa_public() {
  Val out = Val::object();
  out["enabled"] = NUMA_ON;
  if (!NUMA_ON) return out;
  std::lock_guard<std::mutex> lk(NUMA_MTX);
  Val nodes = Val::array();
  for (int n = 0; n < 2; n++) {
    Val st = numa_node_state_locked(n);
    Val v = Val::object();
    v["node"] = n;
    v["model"] = st["model"];
    const Val *m = numa_model_locked(st["model"].asStr());
    v["label"] = m ? m->find("label")->asStr() : st["model"].asStr();
    v["short"] = m ? m->find("short")->asStr() : "?";
    std::string state = st.find("state") ? st["state"].asStr() : "serving";
    if (NUMA_SWAP_NODE == n && state == "serving") state = "swapping";
    v["state"] = state;
    v["progress"] = st.find("progress") ? st["progress"].asNum(0) : 100;
    if (NUMA_SWAP_NODE == n) v["target"] = NUMA_SWAP_TARGET;
    if (st.find("error") && !st["error"].asStr().empty()) v["error"] = st["error"];
    v["budget_gib"] = numa_node_locked(n)->find("budget_gib")->asNum();
    nodes.push(v);
  }
  out["nodes"] = nodes;
  Val cat = Val::array();
  for (auto &m : NUMA_CATALOG["models"].asArr()) {
    Val v = Val::object();
    for (const char *k : {"id", "label", "short", "ram_gib"}) if (m.find(k)) v[k] = *m.find(k);
    Val fits = Val::array();
    for (int n = 0; n < 2; n++) fits.push(numa_admit_locked(n, m).empty());
    v["fits"] = fits;
    cat.push(v);
  }
  out["catalog"] = cat;
  Val assign = Val::object();
  for (auto &kv : NUMA_ASSIGN) assign[kv.first] = kv.second;
  out["assign"] = assign;
  out["swapping"] = NUMA_SWAP_NODE;
  if (!NUMA_LAST_ERROR.empty()) out["last_error"] = NUMA_LAST_ERROR;
  return out;
}
static void numa_event() {
  Val e = Val::object();
  e["type"] = "numa";
  e["numa"] = numa_public();
  publish(mj::dump(e));
}
static bool numa_assign(const std::string &bot, int node) {
  if (!NUMA_ON || (node != 0 && node != 1) || agent_index(bot) < 0) return false;
  {
    std::lock_guard<std::mutex> lk(NUMA_MTX);
    NUMA_ASSIGN[bot] = node;
    Val a = Val::object();
    for (auto &kv : NUMA_ASSIGN) a[kv.first] = kv.second;
    write_file_atomic(numa_assign_path(), mj::dump(a));
  }
  numa_event();
  JOB_CV.notify_all();
  return true;
}
struct NumaSwapResult { int code = 200; std::string error; };
static NumaSwapResult numa_swap(int node, const std::string &model, bool confirm) {
  if (!NUMA_ON) return {404, "NUMA-Modellwechsel ist nicht eingerichtet."};
  if (node != 0 && node != 1) return {400, "Ungültiger NUMA-Knoten."};
  if (!confirm) return {400, "Bestätigung fehlt."};
  {
    std::lock_guard<std::mutex> lk(NUMA_MTX);
    const Val *m = numa_model_locked(model);
    if (!m) return {400, "Unbekanntes Modell."};
    if (NUMA_SWAP_NODE >= 0) return {409, "Auf NUMA " + std::to_string(NUMA_SWAP_NODE) + " läuft schon ein Wechsel."};
    Val st = numa_node_state_locked(node);
    if (st["model"].asStr() == model && st["state"].asStr() == "serving")
      return {200, ""}; // idempotent
    if (std::string why = numa_admit_locked(node, *m); !why.empty()) return {409, why};
    NUMA_SWAP_NODE = node;
    NUMA_SWAP_TARGET = model;
    NUMA_LAST_ERROR.clear();
  }
  numa_event();
  std::thread([node, model]() {
    std::vector<std::string> argv;
    {
      std::istringstream is(env("MAG_NUMA_SWAP_CMD", "sudo -n /usr/local/lib/mag-numa/mag-numa-swap"));
      std::string w;
      while (is >> w) argv.push_back(w);
    }
    argv.insert(argv.end(), {"swap", std::to_string(node), model});
    std::atomic<bool> done{false};
    std::thread poll([&]() {
      while (!done.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1000));
        if (!done.load()) numa_event();
      }
    });
    CmdResult r = run_argv(argv, 4 * 3600);
    done = true;
    poll.join();
    {
      std::lock_guard<std::mutex> lk(NUMA_MTX);
      NUMA_SWAP_NODE = -1;
      NUMA_SWAP_TARGET.clear();
      NUMA_STATE_AT = 0; // re-read the committed state now
      if (r.code != 0) NUMA_LAST_ERROR = trim(r.out).empty() ? "Wechsel fehlgeschlagen (" + std::to_string(r.code) + ")" : trim(r.out).substr(0, 300);
    }
    std::cerr << "numa-swap node=" << node << " model=" << model << " code=" << r.code << std::endl;
    numa_event();
    publish("{\"type\":\"agents\"}");
    JOB_CV.notify_all(); // queued messages for that node's bots go now
  }).detach();
  return {202, ""};
}
