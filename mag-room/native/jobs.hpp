// ---------------------------------------------------------------- Jobs
// Scheduled / webhook-triggered prompts ("ChatGPT Tasks"). Persistent in
// STATE_DIR/jobs.json. Runs go through the global scheduler (never parallel
// to chat), respect Stop/pause, carry idempotent run ids and a persisted
// lease (queued_run), retry with backoff and post results into the room.
// The job prompt is never part of the static system prompt (cache rule).
#include <openssl/evp.h>
#include <set>

struct JobDef {
  std::string id, name, prompt, bot;
  Val schedule;          // {type: interval|daily|monthly|once|none, ...}
  bool paused = false;
  std::string hook_hash; // sha256 hex of the webhook secret ("" = none)
  int version = 1;
  int64_t next_run = 0, retry_at = 0;
  int attempts = 0;
  std::string queued_run;   // lease: run id that is queued or running
  std::string last_run_id, last_status, last_error;
  int64_t last_run = 0, created = 0;
  Val history = Val::array(); // newest last, max 30
};
static std::mutex JOBDEF_MTX;
static std::vector<JobDef> JOBDEFS;
struct JobRun { std::string job_id, run_id, name, prompt, bot, reason; int version = 0; };
static std::deque<JobRun> JOBRUNS; // guarded by JOB_MTX
static std::string jobs_file() { return STATE_DIR + "/jobs.json"; }
static int job_max_attempts() { return std::max(1, atoi(env("MAG_JOBS_MAX_ATTEMPTS", "3").c_str())); }
static int64_t job_backoff(int attempt) {
  int64_t base = atoll(env("MAG_JOBS_BACKOFF_S", "60").c_str());
  return base * (1LL << std::min(attempt - 1, 6));
}
static std::string sha256_hex(const std::string &s) {
  unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
  EVP_Digest(s.data(), s.size(), md, &n, EVP_sha256(), nullptr);
  static const char *hx = "0123456789abcdef"; std::string out;
  for (unsigned i = 0; i < n; i++) { out += hx[md[i] >> 4]; out += hx[md[i] & 15]; }
  return out;
}
static Val job_to_val(const JobDef &j, bool internal) {
  Val v = Val::object();
  v["id"] = j.id; v["name"] = j.name; v["prompt"] = j.prompt; v["bot"] = j.bot;
  v["schedule"] = j.schedule; v["paused"] = j.paused; v["version"] = j.version;
  v["next_run"] = (double)j.next_run; v["retry_at"] = (double)j.retry_at;
  v["attempts"] = j.attempts; v["queued_run"] = j.queued_run;
  v["last_run_id"] = j.last_run_id; v["last_status"] = j.last_status;
  v["last_error"] = j.last_error; v["last_run"] = (double)j.last_run;
  v["created"] = (double)j.created; v["history"] = j.history;
  if (internal) v["hook_hash"] = j.hook_hash;
  else { v["webhook"] = !j.hook_hash.empty(); if (!j.hook_hash.empty()) v["webhook_path"] = "/hook/jobs/" + j.id; v["running"] = !j.queued_run.empty(); }
  return v;
}
static JobDef job_from_val(const Val &v) {
  JobDef j;
  auto s = [&](const char *k) { auto x = v.find(k); return x ? x->asStr() : std::string(); };
  auto n = [&](const char *k) { auto x = v.find(k); return x ? (int64_t)x->asNum(0) : (int64_t)0; };
  j.id = s("id"); j.name = s("name"); j.prompt = s("prompt"); j.bot = s("bot");
  if (auto x = v.find("schedule")) j.schedule = *x; else j.schedule = Val::object();
  if (auto x = v.find("paused")) j.paused = x->asBool(false);
  j.hook_hash = s("hook_hash"); j.version = (int)std::max<int64_t>(1, n("version"));
  j.next_run = n("next_run"); j.retry_at = n("retry_at"); j.attempts = (int)n("attempts");
  j.queued_run = s("queued_run"); j.last_run_id = s("last_run_id");
  j.last_status = s("last_status"); j.last_error = s("last_error");
  j.last_run = n("last_run"); j.created = n("created");
  if (auto x = v.find("history"); x && x->isArr()) j.history = *x;
  return j;
}
static void jobs_save_locked() {
  Val a = Val::array();
  for (auto &j : JOBDEFS) { Val v = job_to_val(j, true); a.push(v); }
  write_file_atomic(jobs_file(), mj::dump(a));
}
static void jobs_event_locked() {
  Val e = Val::object(); e["type"] = "jobs"; publish(mj::dump(e));
}
static JobDef *job_find_locked(const std::string &id) {
  for (auto &j : JOBDEFS) if (j.id == id) return &j;
  return nullptr;
}
// ---- schedule maths (local time = Europe/Berlin via TZ set at startup)
static bool parse_hhmm(const std::string &t, int &h, int &m) {
  return sscanf(t.c_str(), "%d:%d", &h, &m) == 2 && h >= 0 && h < 24 && m >= 0 && m < 60;
}
static int64_t local_epoch(int y, int mon, int d, int h, int mi) {
  struct tm tm{}; tm.tm_year = y - 1900; tm.tm_mon = mon; tm.tm_mday = d;
  tm.tm_hour = h; tm.tm_min = mi; tm.tm_isdst = -1;
  return (int64_t)mktime(&tm);
}
static int64_t interval_seconds(const Val &s) {
  double every = s.find("every") ? s.find("every")->asNum(0) : 0;
  std::string u = s.find("unit") ? s.find("unit")->asStr() : "h";
  int64_t mul = u == "m" ? 60 : u == "h" ? 3600 : u == "d" ? 86400 : u == "w" ? 604800
              : (u == "s" && !env("MAG_JOBS_ALLOW_SECONDS", "").empty()) ? 1 : 0;
  return every >= 1 ? (int64_t)every * mul : 0;
}
// Next due time strictly after `after` (0 = never).
static int64_t schedule_next(const Val &s, int64_t after, int64_t prev_due) {
  std::string type = s.find("type") ? s.find("type")->asStr() : "none";
  if (type == "interval") {
    int64_t p = interval_seconds(s); if (p <= 0) return 0;
    int64_t base = prev_due > 0 ? prev_due : after;
    int64_t n = base + p;
    if (n <= after) n += ((after - n) / p + 1) * p; // collapse missed periods
    return n;
  }
  if (type == "once") {
    int64_t at = s.find("at_epoch") ? (int64_t)s.find("at_epoch")->asNum(0) : 0;
    return at > after ? at : 0;
  }
  int h, m; if (!parse_hhmm(s.find("time") ? s.find("time")->asStr() : "", h, m)) return 0;
  time_t a = (time_t)after; struct tm lt{}; localtime_r(&a, &lt);
  if (type == "daily") { // optional days: 0=So..6=Sa
    std::set<int> days;
    if (auto d = s.find("days"); d && d->isArr()) for (auto &x : d->asArr()) days.insert((int)x.asNum(-1));
    for (int i = 0; i < 9; i++) {
      int64_t t = local_epoch(lt.tm_year + 1900, lt.tm_mon, lt.tm_mday + i, h, m);
      time_t tt = (time_t)t; struct tm c{}; localtime_r(&tt, &c);
      if (t > after && (days.empty() || days.count(c.tm_wday))) return t;
    }
    return 0;
  }
  if (type == "monthly") {
    int day = s.find("day") ? (int)s.find("day")->asNum(1) : 1; day = std::max(1, std::min(31, day));
    for (int i = 0; i < 14; i++) {
      int y = lt.tm_year + 1900, mon = lt.tm_mon + i;
      int64_t first = local_epoch(y, mon + 1, 1, 12, 0) - 86400; // last day of mon
      time_t ft = (time_t)first; struct tm c{}; localtime_r(&ft, &c);
      int dd = std::min(day, c.tm_mday);
      int64_t t = local_epoch(y, mon, dd, h, m);
      if (t > after) return t;
    }
  }
  return 0;
}
static std::string validate_schedule(Val &s) {
  if (!s.isObj()) return "Zeitplan fehlt";
  std::string type = s.find("type") ? s.find("type")->asStr() : "";
  if (type == "none") return "";
  if (type == "interval") return interval_seconds(s) > 0 ? "" : "Intervall ungültig";
  if (type == "once") {
    std::string at = s.find("at") ? s.find("at")->asStr() : "";
    int y, mo, d, h, mi;
    if (sscanf(at.c_str(), "%d-%d-%dT%d:%d", &y, &mo, &d, &h, &mi) != 5) return "Datum ungültig";
    s["at_epoch"] = (double)local_epoch(y, mo - 1, d, h, mi);
    return "";
  }
  int h, m;
  if ((type == "daily" || type == "monthly") && parse_hhmm(s.find("time") ? s.find("time")->asStr() : "", h, m)) return "";
  return "Zeitplan ungültig";
}
// Called by the ticker and by triggers; JOBDEF_MTX held. Returns false if a
// run of this job is already queued/running (no parallel or double runs).
static bool job_enqueue_locked(JobDef &j, const std::string &run_id, const std::string &reason) {
  if (!j.queued_run.empty()) return false;
  if (run_id == j.last_run_id) return false; // idempotent: already done
  j.queued_run = run_id;
  jobs_save_locked(); // persist the lease before dispatch
  {
    std::lock_guard<std::mutex> lk(JOB_MTX);
    JOBRUNS.push_back(JobRun{j.id, run_id, j.name, j.prompt, j.bot, reason, j.version});
  }
  JOB_CV.notify_all();
  jobs_event_locked();
  return true;
}
static void jobs_tick() {
  std::lock_guard<std::mutex> lk(JOBDEF_MTX);
  const int64_t now = now_s();
  for (auto &j : JOBDEFS) {
    if (j.paused || !j.queued_run.empty()) continue;
    if (j.retry_at > 0 && j.retry_at <= now) {
      j.retry_at = 0;
      std::string base = j.last_run_id.substr(0, j.last_run_id.find('#'));
      job_enqueue_locked(j, base + "#r" + std::to_string(j.attempts + 1), "Wiederholung");
      continue;
    }
    if (j.next_run > 0 && j.next_run <= now) {
      std::string rid = j.id + ":" + std::to_string(j.next_run);
      int64_t due = j.next_run;
      j.next_run = schedule_next(j.schedule, now, due); // catch-up at most once
      j.attempts = 0;
      if (!job_enqueue_locked(j, rid, "Zeitplan")) jobs_save_locked();
    }
  }
}
static void jobs_ticker() {
  const int ms = std::max(200, atoi(env("MAG_JOBS_TICK_MS", "5000").c_str()));
  while (!SHUTDOWN.load()) {
    try { jobs_tick(); } catch (...) {}
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
  }
}
static void jobs_load() {
  std::lock_guard<std::mutex> lk(JOBDEF_MTX);
  for (auto &v : load_json_array(jobs_file())) {
    JobDef j = job_from_val(v);
    if (j.id.empty()) continue;
    JOBDEFS.push_back(j);
  }
  // A lease left by a crashed/restarted process: run that exact run again,
  // once (same run id -> history prevents doubles).
  for (auto &j : JOBDEFS)
    if (!j.queued_run.empty()) {
      std::string rid = j.queued_run; j.queued_run.clear();
      job_enqueue_locked(j, rid, "nach Neustart");
    }
  jobs_save_locked();
}
static std::string fmt_local(int64_t t) {
  time_t tt = (time_t)t; struct tm lt{}; localtime_r(&tt, &lt);
  char b[32]; strftime(b, sizeof b, "%d.%m.%Y %H:%M", &lt); return b;
}
static std::string job_user_content(const JobRun *r) {
  return "[Geplanter Job \u201e" + r->name + "\u201c \u00b7 " + fmt_local(now_s()) +
         " \u00b7 eigener Lauf, Ergebnis wird in den Raum gepostet]\n" + r->prompt + "\n";
}
static TurnReport run_turn(int bot_idx, bool autotalk, const std::string &other);
static void run_job(const JobRun &run) {
  int idx = run.bot.empty() ? -1 : agent_index(run.bot);
  if (idx < 0) { std::lock_guard<std::recursive_mutex> lk(STATE_MTX); idx = AGENTS.empty() ? -1 : 0; }
  std::string status = "ok", error, text;
  const int64_t started = now_s();
  if (idx < 0) { status = "error"; error = "Kein Bot vorhanden"; }
  else {
    std::string name = bot_field(idx, "name", "Bot"), bid = bot_field(idx, "id");
    Val st = Val::object(); st["type"] = "bot_start"; st["bot"] = bid; st["name"] = name; publish(mj::dump(st));
    CURRENT_JOB = &run;
    TurnReport rep;
    try { rep = run_turn(idx, false, ""); } catch (const std::exception &e) { rep.error = e.what(); }
    CURRENT_JOB = nullptr;
    text = trim(rep.text);
    if (STOP.load()) { status = "abgebrochen"; error = "gestoppt"; }
    else if (!rep.error.empty()) { status = "error"; error = rep.error; }
    else if (text.empty() || text.rfind("Antwort fehlgeschlagen", 0) == 0 ||
             text.find("Es wurde keine Antwort erzeugt.") != std::string::npos) { status = "error"; error = text.empty() ? "leere Antwort" : text.substr(0, 300); }
    std::string head = "🗓 Job „" + run.name + "“ · " + fmt_local(started) + (status == "ok" ? "" : " · " + status);
    Val msg = append_room("assistant", name, head + "\n\n" + (text.empty() ? "(" + error + ")" : text), bid);
    msg["type"] = "bot_done"; publish(mj::dump(msg));
  }
  std::lock_guard<std::mutex> lk(JOBDEF_MTX);
  JobDef *j = job_find_locked(run.job_id);
  if (!j) return; // deleted while running: finished, no further run
  if (j->queued_run == run.run_id) j->queued_run.clear();
  j->last_run_id = run.run_id; j->last_run = started;
  Val h = Val::object(); h["run_id"] = run.run_id; h["ts"] = (double)started;
  h["status"] = status; h["reason"] = run.reason; h["version"] = run.version;
  h["summary"] = text.substr(0, 400); if (!error.empty()) h["error"] = error;
  j->history.push(h);
  while (j->history.asArr().size() > 30) j->history.arr->erase(j->history.arr->begin());
  if (status == "error") {
    j->attempts++;
    if (j->attempts < job_max_attempts()) { j->retry_at = now_s() + job_backoff(j->attempts); j->last_status = "retry"; j->last_error = error; }
    else { j->last_status = "error"; j->last_error = error + " (nach " + std::to_string(j->attempts) + " Versuchen)"; j->attempts = 0; j->retry_at = 0; }
  } else { j->last_status = status; j->last_error = error; j->attempts = 0; j->retry_at = 0; }
  jobs_save_locked(); jobs_event_locked();
}
// ---- API helpers
static std::string job_apply(JobDef &j, const Val &b, bool create) {
  auto s = [&](const char *k) { auto x = b.find(k); return x ? trim(x->asStr()) : std::string(); };
  if (create || b.find("name")) { j.name = s("name"); if (j.name.empty() || j.name.size() > 120) return "Name fehlt"; }
  if (create || b.find("prompt")) { j.prompt = s("prompt"); if (j.prompt.empty() || j.prompt.size() > 16000) return "Prompt fehlt"; }
  if (b.find("bot")) j.bot = s("bot");
  if (create || b.find("schedule")) {
    Val sc = b.find("schedule") ? *b.find("schedule") : Val::object();
    std::string err = validate_schedule(sc); if (!err.empty()) return err;
    j.schedule = sc;
    j.next_run = schedule_next(sc, now_s(), 0);
    j.retry_at = 0; j.attempts = 0;
  }
  if (b.find("paused")) j.paused = b.find("paused")->asBool(false);
  return "";
}
