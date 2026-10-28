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
  std::string id, bot, requester, title, prompt, status = "running", result;
  int64_t started = 0, ended = 0;
  int attempts = 1;
  bool recovering = false;
  bool handoff_pending = false; // remains set until owner verifies result
  std::string handoff_status;
  Lane lane;
};
static std::mutex TASKS_MTX;
static std::vector<std::shared_ptr<BgTask>> TASKS; // newest last, max 40 kept
static std::string task_id_of(const BgTask *t) { return t->id; }
static void task_handoff_enqueue(const std::shared_ptr<BgTask> &t);

// Durable task registry. Write-before-execute means an unexpected shutdown
// never loses ownership, the original goal, or a task's last known state.
// A restarted process cannot know which external side effects completed, so
// it NEVER replays an interrupted task without an explicit owner resume.
static bool tasks_save_locked() {
  Val rows = Val::array();
  for (const auto &t : TASKS) {
    Val v = Val::object();
    v["id"] = t->id; v["bot"] = t->bot; v["requester"] = t->requester;
    v["title"] = t->title; v["prompt"] = t->prompt;
    v["status"] = t->status; v["result"] = t->result;
    v["started"] = (double)t->started; v["ended"] = (double)t->ended;
    v["attempts"] = (double)t->attempts;
    v["handoff_pending"] = t->handoff_pending;
    v["handoff_status"] = t->handoff_status;
    rows.push(v);
  }
  return write_file_atomic(STATE_DIR + "/tasks.json", mj::dump(rows));
}
static void tasks_restore() {
  bool exists = false;
  const std::string raw = read_file(STATE_DIR + "/tasks.json", &exists);
  if (!exists) return;
  Val rows;
  if (!mj::parse(raw, rows) || !rows.isArr())
    throw std::runtime_error("tasks.json ist beschädigt; vorhandene Aufgaben nicht überschreiben");
  std::vector<std::shared_ptr<BgTask>> pending_handoffs;
  {
  std::lock_guard<std::mutex> lk(TASKS_MTX);
  TASKS.clear();
  bool interrupted = false;
  std::set<std::string> seen;
  for (const Val &v : *rows.arr) {
    if (!v.isObj()) continue;
    auto field = [&](const char *key) -> std::string {
      auto p = v.find(key); return p ? p->asStr() : "";
    };
    auto t = std::make_shared<BgTask>();
    t->id = field("id"); t->bot = field("bot"); t->requester = field("requester");
    t->title = field("title"); t->prompt = field("prompt");
    t->status = field("status"); t->result = field("result");
    t->started = (int64_t)(v.find("started") ? v.find("started")->asNum(0) : 0);
    t->ended = (int64_t)(v.find("ended") ? v.find("ended")->asNum(0) : 0);
    t->attempts = std::clamp((int)(v.find("attempts") ? v.find("attempts")->asNum(1) : 1), 1, 1000);
    t->handoff_pending = v.find("handoff_pending") && v.find("handoff_pending")->asBool();
    t->handoff_status = field("handoff_status");
    if (t->id.empty() || t->id.size() > 64 || t->bot.empty() ||
        t->prompt.empty() || !seen.insert(t->id).second) continue;
    if (t->status == "running") {
      t->status = "interrupted";
      t->result = "Prozessneustart: zuerst externe Auswirkungen prüfen, dann explizit fortsetzen.";
      t->ended = now_s();
      if (!t->requester.empty()) t->handoff_pending = true;
      interrupted = true;
    }
    t->lane.model = "task:" + t->id;
    TASKS.push_back(t);
    if (t->handoff_pending) pending_handoffs.push_back(t);
  }
  if (interrupted && !tasks_save_locked())
    throw std::runtime_error("Unterbrochene Aufgaben konnten nicht gesichert werden");
  }
  // Handoff events are recoverable if the room exits between the worker's
  // result being persisted and the owner's follow-up finishing.
  for (const auto &t : pending_handoffs) task_handoff_enqueue(t);
}


static Val task_val_locked(const BgTask &t) {
  Val v = Val::object();
  v["id"] = t.id;
  v["bot"] = t.bot;
  if (!t.requester.empty()) v["requester"] = t.requester;
  v["title"] = t.title;
  v["status"] = t.status;
  v["started"] = (double)t.started;
  v["attempts"] = (double)t.attempts;
  if (!t.requester.empty()) {
    v["handoff_pending"] = t.handoff_pending;
    v["handoff_status"] = t.handoff_status;
  }
  if (t.ended) v["ended"] = (double)t.ended;
  if (!t.result.empty()) v["result"] = t.result.substr(0, 400);
  v["todo"] = todo_public(t.bot + "#" + t.id);
  return v;
}
static Val tasks_public(const std::string &bot = "") {
  std::lock_guard<std::mutex> lk(TASKS_MTX);
  Val arr = Val::array();
  for (auto &t : TASKS)
    if (bot.empty() || t->bot == bot || t->requester == bot) arr.push(task_val_locked(*t));
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
  int unfinished_goals = 0;
  {
    std::shared_lock<std::shared_mutex> turn(TURN_MTX);
    TL_TASK = t.get();
    TL_LANE = &t->lane;
    JobRun jr;
    jr.job_id = "task:" + t->id;
    jr.run_id = t->id;
    jr.name = t->title;
    jr.prompt = t->recovering
      ? "[WIEDERAUFNAHME: Diese Aufgabe wurde durch Neustart unterbrochen. "
        "Pruefe mit realen Werkzeugen zuerst, was bereits geschehen ist. "
        "Fuehre externe, irreversible oder nicht-idempotente Aktionen NICHT "
        "erneut ohne neue explizite Freigabe aus. Setze die gespeicherte "
        "Todo-Liste kontrolliert fort; melde ungeklaerte Schritte als blockiert.]\n\n"
        + t->prompt : t->prompt;
    jr.bot = t->bot;
    CURRENT_JOB = &jr;
    run_begin(t->bot + "#" + t->id, name + " · " + t->title);
    try {
      if (idx >= 0) rep = run_turn(idx, false, "");
      else rep.error = "Bot entfernt";
    } catch (const std::exception &e) { rep.error = e.what(); }
    unfinished_goals = todo_open_for(t->bot);
    run_end();
    CURRENT_JOB = nullptr;
    TL_LANE = nullptr;
    TL_TASK = nullptr;
  }
  const bool cancelled = t->lane.stop.load();
  // An LLM saying "done" does not close an open plan or finish a truncated run.
  const bool incomplete = !cancelled && rep.error.empty() &&
      (unfinished_goals > 0 || rep.continue_requested || rep.skipped || trim(rep.text).empty());
  std::string text = trim(rep.text);
  {
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    t->status = cancelled ? "cancelled" : !rep.error.empty() ? "error" :
                incomplete ? "incomplete" : "done";
    t->result = cancelled ? "abgebrochen" : incomplete
      ? "Nicht abgeschlossen (" + std::to_string(unfinished_goals) +
        " offene Ziele; ggf. begrenzte Schrittzahl). " + text.substr(0, 1600)
      : text.empty() ? rep.error : text;
    t->ended = now_s();
    // Stop is terminal: a cancelled delegated task must not wake its owner.
    t->handoff_pending = !cancelled && !t->requester.empty();
    t->handoff_status = t->handoff_pending ? "waiting" : "";
    if (!tasks_save_locked())
      std::cerr << "task persist failed at completion: " << t->id << "\n";
  }
  t->lane.busy = false;
  if (idx >= 0 && !SHUTDOWN.load()) {
    const std::string head = "↳ Aufgabe „" + t->title + "“" +
        (cancelled ? " · abgebrochen" : !rep.error.empty() ? " · Fehler" :
         incomplete ? " · unvollständig" : " · fertig");
    Val msg = append_room("assistant", name,
        cancelled ? head : head + "\n\n" + (incomplete ? t->result : text.empty() ? rep.error : text), t->bot);
    msg["type"] = "bot_done";
    publish(mj::dump(msg));
  }
  if (!SHUTDOWN.load()) task_handoff_enqueue(t);
  tasks_event();
}

// Event-driven owner reactivation. The original child result is durable;
// this queued JobRun is disposable and reconstructed after a crash.
static void task_handoff_enqueue(const std::shared_ptr<BgTask> &t) {
  std::string id, requester, title, target, status, result;
  {
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    if (!t->handoff_pending || t->requester.empty()) return;
    id=t->id; requester=t->requester; title=t->title;
    target=t->bot; status=t->status; result=t->result;
  }
  if (agent_index(requester) < 0) return;
  JobRun run;
  run.job_id = "handoff:" + id;
  run.run_id = run.job_id;
  run.name = "Delegation prüfen: " + title;
  run.bot = requester;
  run.reason = "delegated_task_result";
  run.prompt = "[INTERNE EREIGNISAKTIVIERUNG, KEINE NEUE BENUTZERNACHRICHT]\n"
    "Du bist weiterhin verantwortlich fuer den urspruenglichen Benutzerauftrag.\n"
    "Unteraufgabe: " + title + " (ID " + id + ", Ausfuehrer " + target + ")\n"
    "Status: " + status + "\nErgebnis: " + result.substr(0, 2400) + "\n"
    "Lies die bisherigen Auftragsziele und vorhandenen Beweise. Ueberpruefe "
    "den realen Endzustand mit Werkzeugen. Ein Unteraufgabenergebnis ist "
    "KEIN Beleg dafuer, dass das Gesamtziel erreicht wurde. Wiederhole keine "
    "externen Aktionen, deren Auswirkungen nach einer Unterbrechung unklar "
    "sind; frage vorher nach Freigabe. Schliesse nur verifizierte Ziele ab "
    "oder nenne den konkreten Blocker.\n";
  {
    std::lock_guard<std::mutex> lk(JOB_MTX);
    // At-most-once scheduling within one room process; after a crash, the
    // persisted pending bit is intentionally replayed for verification.
    if (std::any_of(JOBRUNS.begin(), JOBRUNS.end(),
        [&](const JobRun &x) { return x.job_id == run.job_id; })) return;
    JOBRUNS.push_back(std::move(run));
  }
  JOB_CV.notify_all();
}
static void task_followup_ack(const std::string &id, const std::string &status) {
  {
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    for (auto &t : TASKS) if (t->id == id && t->handoff_pending) {
      t->handoff_status = status;
      t->handoff_pending = false;
      if (!tasks_save_locked())
        std::cerr << "handoff persist failed: " << id << "\\n";
      break;
    }
  }
  tasks_event();
}

static std::string task_start(int bot_idx, const std::string &title_in,
                              const std::string &prompt, const std::string &requester = "") {
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
    t->requester = requester;
    t->title = title;
    t->prompt = p;
    t->started = now_s();
    t->lane.model = "task:" + t->id;
    t->lane.busy = true;
    TASKS.push_back(t);
    if (!tasks_save_locked()) {
      TASKS.pop_back();
      throw std::runtime_error("Aufgabe nicht dauerhaft speicherbar; nicht gestartet.");
    }
    while (TASKS.size() > 40) {
      auto it = std::find_if(TASKS.begin(), TASKS.end(), [](auto &x) { return x->status != "running"; });
      if (it == TASKS.end()) break;
      TASKS.erase(it);
    }
    if (!tasks_save_locked())
      std::cerr << "task retention persist failed\n";
  }
  std::thread(task_main, t).detach();
  tasks_event();
  return "Aufgabe " + t->id + " „" + title + "“ gestartet; Ergebnis kommt in den Raum.";
}
// Explicit recovery: never silently repeat an interrupted action.
static bool task_resume(const std::string &id) {
  std::shared_ptr<BgTask> t;
  {
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    for (auto &x : TASKS)
      if (x->id == id && x->status == "interrupted") { t = x; break; }
    if (!t || agent_index(t->bot) < 0) return false;
    int running = 0;
    for (const auto &x : TASKS)
      if (x->bot == t->bot && x->status == "running") ++running;
    if (running >= task_limit()) return false;
    t->status = "running";
    t->recovering = true;
    ++t->attempts;
    t->ended = 0;
    t->lane.stop.store(false);
    t->lane.user_cancel.store(false);
    t->lane.backend_fd.store(-1);
    t->lane.busy = true;
    if (!tasks_save_locked()) {
      t->status = "interrupted";
      t->lane.busy = false;
      --t->attempts;
      return false;
    }
  }
  std::thread(task_main, t).detach();
  tasks_event();
  return true;
}
// "" bot = any; returns true if a running task was found.
static bool task_cancel(const std::string &id, const std::string &bot = "") {
  bool found = false;
  {
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    for (auto &t : TASKS)
      if ((id.empty() || t->id == id) &&
          (bot.empty() || t->bot == bot || t->requester == bot) &&
          t->status == "running") {
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
  if (action == "delegate") {
    if (TL_TASK) throw std::runtime_error("Unteraufgaben duerfen nicht weiterdelegieren.");
    const std::string target_name = s("to_bot");
    if (target_name.empty()) throw std::runtime_error("to_bot fehlt.");
    int target = -1;
    {
      std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
      target = agent_index(target_name);
      if (target < 0)
        for (int i = 0; i < (int)AGENTS.size(); ++i)
          if (lc(bot_field(i, "name")) == lc(target_name)) { target = i; break; }
      if (target >= 0) {
        const Val *enabled = AGENTS[target].find("enabled");
        if (enabled && !enabled->asBool(true))
          throw std::runtime_error("Zielbot ist deaktiviert.");
      }
    }
    if (target < 0) throw std::runtime_error("Zielbot nicht gefunden.");
    if (bot_field(target, "id") == bot)
      throw std::runtime_error("Fuer eigene Unteraufgaben task(start) verwenden.");
    const std::string sender = bot_field(bot_idx, "name", "Bot");
    // No impersonated user turn. The target independently runs under its
    // own model, VM and tool permissions; existing approvals still apply.
    const std::string delegated = "[Explizite Bot-Delegation von " + sender +
      ", keine neue Benutzernachricht. Pruefe den Stand vor Aktionen. " +
      "Fuer irreversible/externe Aktionen gelten dieselben Freigaben.]\n" + s("prompt");
    const std::string title = sender + " → " + s("title");
    return task_start(target, title, delegated, bot);
  }
  if (action == "resume") return "Nur der Besitzer kann eine unterbrochene Aufgabe nach Prüfung der externen Auswirkungen fortsetzen.";
  if (action == "cancel") {
    if (s("id").empty()) throw std::runtime_error("id fehlt.");
    return task_cancel(s("id"), bot) ? "Abgebrochen." : "Keine laufende Aufgabe mit dieser id.";
  }
  if (action == "status") {
    std::string out;
    std::lock_guard<std::mutex> lk(TASKS_MTX);
    for (auto &t : TASKS)
      if (t->bot == bot || t->requester == bot)
        out += t->id + " · " + t->status + " · " + t->title +
               (t->result.empty() ? "" : " → " + one_line(t->result).substr(0, 200)) + "\n";
    return out.empty() ? "Keine Aufgaben." : out;
  }
  throw std::runtime_error("Unbekannte task-Aktion: " + action);
}
