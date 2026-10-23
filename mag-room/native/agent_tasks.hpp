// ---------------------------------------------------------------- tasks
// Background sub-agents (docs/agent-architecture.md). A task runs run_turn
// on its own Lane and thread: its own stop flag and backend socket give a
// real cancel (Stop, /api/tasks/<id>/cancel, task cancel). Depth 1: a task
// has no task tool. Between model steps a task waits while a user message
// for its bot is queued, so user messages always come first.
static TurnReport run_turn(int bot_idx, bool autotalk, const std::string &other);
static void cancel_lane(Lane &l, bool user);
// A user message for this bot is queued (not yet answered).
static bool user_turn_waiting(const std::string &bot_id) {
  std::lock_guard<std::mutex> lk(JOB_MTX);
  for (auto &j : JOBS)
    if (std::find(j.ids.begin(), j.ids.end(), bot_id) != j.ids.end()) return true;
  for (auto &p : PENDING)
    if (!p.held && std::find(p.ids.begin(), p.ids.end(), bot_id) != p.ids.end()) return true;
  return false;
}

struct BgTask {
  std::string id, bot, title, prompt, status = "running", result;
  int64_t started = 0, ended = 0;
  Lane lane;
};
static std::mutex TASKS_MTX;
static std::vector<std::shared_ptr<BgTask>> TASKS; // newest last, max 40 kept
static std::string task_id_of(const BgTask *t) { return t->id; }

static Val task_val_locked(const BgTask &t) {
  Val v = Val::object();
  v["id"] = t.id;
  v["bot"] = t.bot;
  v["title"] = t.title;
  v["status"] = t.status;
  v["started"] = (double)t.started;
  if (t.ended) v["ended"] = (double)t.ended;
  if (!t.result.empty()) v["result"] = t.result.substr(0, 400);
  v["todo"] = todo_public(t.bot + "#" + t.id);
  return v;
}
static Val tasks_public(const std::string &bot = "") {
  std::lock_guard<std::mutex> lk(TASKS_MTX);
  Val arr = Val::array();
  for (auto &t : TASKS)
    if (bot.empty() || t->bot == bot) arr.push(task_val_locked(*t));
  return arr;
}
static void tasks_event() {
  Val e = Val::object();
  e["type"] = "tasks";
  e["tasks"] = tasks_public();
  publish(mj::dump(e));
}
static int task_limit() { return std::clamp(envi("MAG_ROOM_MAX_TASKS", 2), 0, 8); }

static void task_yield_point() {
  if (!TL_TASK) return;
  while (!TL_TASK->lane.stop.load() && !SHUTDOWN.load() && user_turn_waiting(TL_TASK->bot))
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
}

static void task_main(std::shared_ptr<BgTask> t) {
  int idx = agent_index(t->bot);
  std::string name = idx >= 0 ? bot_field(idx, "name", "Bot") : "Bot";
  TurnReport rep;
  {
    std::shared_lock<std::shared_mutex> turn(TURN_MTX);
    TL_TASK = t.get();
    TL_LANE = &t->lane;
    JobRun jr;
    jr.job_id = "task:" + t->id;
    jr.run_id = t->id;
    jr.name = t->title;
    jr.prompt = t->prompt;
    jr.bot = t->bot;
    CURRENT_JOB = &jr;
    run_begin(t->bot + "#" + t->id, name + " · " + t->title);
    try {
      if (idx >= 0) rep = run_turn(idx, false, "");
      else rep.error = "Bot entfernt";
    } catch (const std::exception &e) { rep.error = e.what(); }
    run_end();
    CURRENT_JOB = nullptr;
    TL_LANE = nullptr;
    TL_TASK = nullptr;
  }
  const bool cancelled = t->lane.stop.load();
  std::string text = trim(rep.text);
  {
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    t->status = cancelled ? "cancelled" : !rep.error.empty() ? "error" : "done";
    t->result = cancelled ? "abgebrochen" : text.empty() ? rep.error : text;
    t->ended = now_s();
  }
  t->lane.busy = false;
  if (idx >= 0 && !SHUTDOWN.load()) {
    const std::string head = "↳ Aufgabe „" + t->title + "“" +
        (cancelled ? " · abgebrochen" : !rep.error.empty() ? " · Fehler" : " · fertig");
    Val msg = append_room("assistant", name,
        cancelled ? head : head + "\n\n" + (text.empty() ? rep.error : text), t->bot);
    msg["type"] = "bot_done";
    publish(mj::dump(msg));
  }
  tasks_event();
}

static std::string task_start(int bot_idx, const std::string &title_in, const std::string &prompt) {
  if (TL_TASK) throw std::runtime_error("Unteraufgaben koennen keine weiteren Aufgaben starten.");
  const std::string bot = bot_field(bot_idx, "id");
  const std::string p = trim(prompt);
  if (p.empty() || p.size() > 16000) throw std::runtime_error("prompt fehlt oder ist zu lang.");
  std::string title = trim(title_in).substr(0, 80);
  if (title.empty()) title = one_line(p).substr(0, 60);
  auto t = std::make_shared<BgTask>();
  {
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    int running = 0;
    for (auto &x : TASKS) if (x->bot == bot && x->status == "running") ++running;
    if (running >= task_limit())
      throw std::runtime_error("Schon " + std::to_string(running) +
                               " Aufgaben aktiv (Limit). Warte oder brich eine ab.");
    t->id = "t" + token_hex(4);
    t->bot = bot;
    t->title = title;
    t->prompt = p;
    t->started = now_s();
    t->lane.model = "task:" + t->id;
    t->lane.busy = true;
    TASKS.push_back(t);
    while (TASKS.size() > 40) {
      auto it = std::find_if(TASKS.begin(), TASKS.end(), [](auto &x) { return x->status != "running"; });
      if (it == TASKS.end()) break;
      TASKS.erase(it);
    }
  }
  std::thread(task_main, t).detach();
  tasks_event();
  return "Aufgabe " + t->id + " „" + title + "“ gestartet; Ergebnis kommt in den Raum.";
}
// "" bot = any; returns true if a running task was found.
static bool task_cancel(const std::string &id, const std::string &bot = "") {
  bool found = false;
  {
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    for (auto &t : TASKS)
      if ((id.empty() || t->id == id) && (bot.empty() || t->bot == bot) && t->status == "running") {
        cancel_lane(t->lane, true);
        found = true;
      }
  }
  if (found) tasks_event();
  return found;
}
static bool tasks_running() {
  std::lock_guard<std::mutex> lk(TASKS_MTX);
  for (auto &t : TASKS) if (t->status == "running") return true;
  return false;
}
static std::string task_tool(int bot_idx, const Val &a) {
  auto s = [&](const char *k) { auto v = a.find(k); return v ? trim(v->asStr()) : std::string(); };
  const std::string action = s("action"), bot = bot_field(bot_idx, "id");
  if (action == "start") return task_start(bot_idx, s("title"), s("prompt"));
  if (action == "cancel") {
    if (s("id").empty()) throw std::runtime_error("id fehlt.");
    return task_cancel(s("id"), bot) ? "Abgebrochen." : "Keine laufende Aufgabe mit dieser id.";
  }
  if (action == "status") {
    std::string out;
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    for (auto &t : TASKS)
      if (t->bot == bot)
        out += t->id + " · " + t->status + " · " + t->title +
               (t->result.empty() ? "" : " → " + one_line(t->result).substr(0, 200)) + "\n";
    return out.empty() ? "Keine Aufgaben." : out;
  }
  throw std::runtime_error("Unbekannte task-Aktion: " + action);
}
