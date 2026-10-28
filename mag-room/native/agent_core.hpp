// ---------------------------------------------------------------- agent core
// Grok-Bot-style turn model pieces (docs/agent-architecture.md):
//   todo   - per-run goal list; open items keep the turn going (bounded),
//            every change is a visible milestone in the run card.
//   memory - profile (remember facts) + dated journal with recall/update.
//   task   - background sub-agents (agent_tasks.hpp) with status and cancel.
// Schemas stay registered on every turn (stable tool list = prompt cache).
#include <fstream>

static const char *T_TODO =
    R"({"type":"function","function":{"name":"todo","description":"Track the goal of the current task as a short todo list. set replaces the list (items), add appends one item, done/block mark item (1-based) as finished or blocked (with note). Open items mean the task is not finished: keep working until each is done or blocked. Each change is shown to the user as a milestone.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["set","add","done","block","list"]},"goal":{"type":"string"},"items":{"type":"array","items":{"type":"string"}},"item":{"type":"integer"},"text":{"type":"string"}},"required":["action"],"additionalProperties":false}}})";
static const char *T_MEMORY =
    R"({"type":"function","function":{"name":"memory","description":"Your durable memory. recall searches profile facts and journal (query); journal appends a dated journal entry (text); update replaces profile fact number item with text; forget deletes profile fact number item. Use remember for new profile facts.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["recall","journal","update","forget"]},"query":{"type":"string"},"text":{"type":"string"},"item":{"type":"integer"}},"required":["action"],"additionalProperties":false}}})";
static const char *T_SKILL =
    R"({"type":"function","function":{"name":"skill","description":"Persistent reusable workflow library shared between your two Bots. list finds existing skills; read returns exact instructions; save records a DRAFT process with when, steps, verify and approval. Never claim an untested draft is a working routine; schedule only through jobs_* after explicit approval.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["list","read","save"]},"name":{"type":"string"},"when":{"type":"string"},"steps":{"type":"string"},"verify":{"type":"string"},"approval":{"type":"string"}},"required":["action"],"additionalProperties":false}}})";
static const char *T_TASK =
    R"({"type":"function","function":{"name":"task","description":"Independent background work. start runs your own sub-agent; delegate hands a clear, reversible subtask to the OTHER bot (to_bot id or name) on its own computer, preserves each bot's access controls, and posts the result to the room. status shows own and delegated tasks; cancel stops owned or delegated task id. Never delegate irreversible actions without approval.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["start","delegate","status","cancel"]},"title":{"type":"string"},"prompt":{"type":"string"},"to_bot":{"type":"string"},"id":{"type":"string"}},"required":["action"],"additionalProperties":false}}})";

struct BgTask;
// Set inside a sub-agent thread; sub-agents get no task tool (depth 1) and
// never consume user input meant for the main turn.
static thread_local BgTask *TL_TASK = nullptr;
static std::string task_id_of(const BgTask *t);
static void task_yield_point();
static std::string task_tool(int bot_idx, const Val &a);

// ---- todo
static std::mutex TODO_MTX;
static std::map<std::string, Val> TODOS; // key -> {goal, items:[{text,status,note,ts}]}
static thread_local bool TL_TODO_TOUCHED = false;
static std::string todo_key(const std::string &bot_id) {
  // A new human request must never inherit another request's open todo plan.
  // Background subtasks retain their existing stable identity on recovery.
  if (TL_TASK) return bot_id + "#" + task_id_of(TL_TASK);
  return TL_GOAL_ID.empty() ? bot_id : bot_id + "#" + TL_GOAL_ID;
}
static std::string todo_file(const std::string &key) {
  std::string safe;
  for (char c : key) safe += (isalnum((unsigned char)c) || c == '-' || c == '_') ? c : '_';
  return STATE_DIR + "/todo/" + safe + ".json";
}
static Val todo_get_locked(const std::string &key) {
  auto it = TODOS.find(key);
  if (it != TODOS.end()) return it->second;
  Val v;
  if (!mj::parse(read_file(todo_file(key)), v) || !v.isObj()) {
    v = Val::object();
    v["goal"] = "";
    v["items"] = Val::array();
  }
  TODOS[key] = v;
  return v;
}
static int todo_open_count(const Val &t) {
  int n = 0;
  if (auto it = t.find("items"); it && it->isArr())
    for (auto &i : *it->arr)
      if (i.isObj() && i.find("status") && i.find("status")->asStr() == "open") ++n;
  return n;
}
static std::string todo_render(const Val &t) {
  std::string out;
  if (auto g = t.find("goal"); g && !g->asStr().empty()) out += "Ziel: " + g->asStr() + "\n";
  int n = 0;
  if (auto it = t.find("items"); it && it->isArr())
    for (auto &i : *it->arr) {
      const std::string st = i.find("status") ? i.find("status")->asStr() : "open";
      out += std::to_string(++n) + ". [" + (st == "done" ? "x" : st == "blocked" ? "!" : " ") +
             "] " + (i.find("text") ? i.find("text")->asStr() : "");
      if (auto no = i.find("note"); no && !no->asStr().empty()) out += " (" + no->asStr() + ")";
      out += "\n";
    }
  return out.empty() ? "(keine Ziele)\n" : out;
}
static void todo_publish(const std::string &bot_id, const std::string &key, const Val &t) {
  Val e = Val::object();
  e["type"] = "todo";
  e["bot"] = bot_id;
  e["key"] = key;
  e["todo"] = t;
  publish(mj::dump(e));
}
static void run_narration(const std::string &live, bool commit = true);
static std::string todo_tool(const std::string &bot_id, const Val &a) {
  auto s = [&](const char *k) { auto v = a.find(k); return v ? trim(v->asStr()) : std::string(); };
  const std::string action = s("action"), key = todo_key(bot_id);
  std::string milestone;
  Val t;
  {
    std::lock_guard<std::mutex> lk(TODO_MTX);
    t = todo_get_locked(key);
    Val &items = t["items"];
    if (!items.isArr()) items = Val::array();
    auto add = [&](const std::string &text) {
      if (text.empty()) return;
      if (items.arr->size() >= 30) throw std::runtime_error("Maximal 30 Ziele.");
      Val i = Val::object();
      i["text"] = text.substr(0, 300);
      i["status"] = "open";
      i["ts"] = (double)now_s();
      items.push(i);
    };
    if (action == "set") {
      items = Val::array();
      t["goal"] = s("goal").substr(0, 300);
      if (auto arr = a.find("items"); arr && arr->isArr())
        for (auto &x : *arr->arr) add(trim(x.asStr()));
      milestone = "Plan: " + std::to_string(items.arr->size()) + " Schritte";
    } else if (action == "add") {
      add(s("text"));
      milestone = "Neues Ziel: " + s("text");
    } else if (action == "done" || action == "block") {
      int n = (int)(a.find("item") ? a.find("item")->asNum(0) : 0);
      if (n < 1 || n > (int)items.arr->size())
        throw std::runtime_error("Ungueltige Ziel-Nummer " + std::to_string(n) + ".");
      Val &i = (*items.arr)[n - 1];
      i["status"] = action == "done" ? "done" : "blocked";
      if (!s("text").empty()) i["note"] = s("text").substr(0, 300);
      milestone = (action == "done" ? "✓ " : "⚠ ") + i["text"].asStr();
    } else if (action != "list") {
      throw std::runtime_error("Unbekannte todo-Aktion: " + action);
    }
    if (action != "list") {
      TODOS[key] = t;
      ensure_state();
      std::error_code ec;
      std::filesystem::create_directories(STATE_DIR + "/todo", ec);
      if (!write_file_atomic(todo_file(key), mj::dump(t)))
        throw std::runtime_error("Todo-Plan konnte nicht gespeichert werden.");
      // Legacy room UI reads /api/todo/<bot> for the current plan.
      // Keep that read-only/latest view, but never read it when a goal ID is
      // active. This preserves the API without leaking older plan state.
      if (!TL_TASK && !TL_GOAL_ID.empty()) {
        TODOS[bot_id] = t;
        if (!write_file_atomic(todo_file(bot_id), mj::dump(t)))
          throw std::runtime_error("Aktuelle Todo-Ansicht konnte nicht gespeichert werden.");
      }
      TL_TODO_TOUCHED = true;
    }
  }
  if (!milestone.empty()) {
    run_narration(milestone, true);
    todo_publish(bot_id, key, t);
  }
  const int open = todo_open_count(t);
  return todo_render(t) + (open ? std::to_string(open) + " offen: weiterarbeiten." : "Alle Ziele erledigt oder blockiert.");
}
static int todo_open_for(const std::string &bot_id) {
  std::lock_guard<std::mutex> lk(TODO_MTX);
  return todo_open_count(todo_get_locked(todo_key(bot_id)));
}
static std::string todo_text_for(const std::string &bot_id) {
  std::lock_guard<std::mutex> lk(TODO_MTX);
  return todo_render(todo_get_locked(todo_key(bot_id)));
}
static Val todo_public(const std::string &key) {
  std::lock_guard<std::mutex> lk(TODO_MTX);
  return todo_get_locked(key);
}

// ---- memory: journal + recall/update/forget
static std::mutex JOURNAL_MTX;
static std::string journal_file(const std::string &bot_id) {
  return STATE_DIR + "/journal/" + bot_id + ".jsonl";
}
static std::vector<Val> journal_read(const std::string &bot_id) {
  std::vector<Val> out;
  std::ifstream in(journal_file(bot_id));
  std::string line;
  while (std::getline(in, line)) {
    Val v;
    if (mj::parse(line, v) && v.isObj()) out.push_back(v);
  }
  return out;
}
static void journal_append(const std::string &bot_id, const std::string &text) {
  const std::string t = trim(text).substr(0, 2000);
  if (t.empty()) throw std::runtime_error("Leerer Journal-Eintrag.");
  std::lock_guard<std::mutex> lk(JOURNAL_MTX);
  std::error_code ec;
  std::filesystem::create_directories(STATE_DIR + "/journal", ec);
  Val e = Val::object();
  e["ts"] = (double)now_s();
  e["text"] = t;
  auto rows = journal_read(bot_id);
  rows.push_back(e);
  const size_t cap = 2000;
  if (rows.size() > cap) rows.erase(rows.begin(), rows.end() - cap);
  std::string body;
  for (auto &r : rows) body += mj::dump(r) + "\n";
  write_file_atomic(journal_file(bot_id), body);
}
static std::string day_of(int64_t ts) {
  time_t t = (time_t)ts;
  struct tm tm{};
  localtime_r(&t, &tm);
  char b[32];
  strftime(b, sizeof b, "%Y-%m-%d", &tm);
  return b;
}
static std::string memory_recall(const std::string &bot_id, const std::string &query) {
  std::vector<std::string> terms;
  {
    std::istringstream is(lc(query));
    std::string w;
    while (is >> w) if (w.size() > 1) terms.push_back(w);
  }
  auto score = [&](const std::string &text) {
    if (terms.empty()) return 1;
    const std::string l = lc(text);
    int n = 0;
    for (auto &w : terms) if (l.find(w) != std::string::npos) ++n;
    return n;
  };
  std::vector<std::pair<int, std::string>> hits;
  {
    std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
    int idx = agent_index(bot_id);
    if (idx >= 0)
      if (auto mem = AGENTS[idx].find("memory"); mem && mem->isArr()) {
        int i = 0;
        for (auto &m : *mem->arr) {
          ++i;
          const std::string text = m.isObj() && m.find("text") ? m.find("text")->asStr() : "";
          if (int sc = score(text)) hits.push_back({sc * 2, "Profil #" + std::to_string(i) + ": " + text});
        }
      }
  }
  {
    std::lock_guard<std::mutex> lk(JOURNAL_MTX);
    for (auto &r : journal_read(bot_id)) {
      const std::string text = r.find("text") ? r.find("text")->asStr() : "";
      if (int sc = score(text))
        hits.push_back({sc, "Journal " + day_of((int64_t)r.find("ts")->asNum(0)) + ": " + text});
    }
  }
  std::stable_sort(hits.begin(), hits.end(), [](auto &x, auto &y) { return x.first > y.first; });
  if (hits.empty()) return "Nichts gefunden.";
  std::string out;
  // Without a query: newest journal entries matter most.
  if (terms.empty()) std::reverse(hits.begin(), hits.end());
  for (size_t i = 0; i < hits.size() && i < 12; i++) out += hits[i].second.substr(0, 400) + "\n";
  return out;
}
static std::string memory_tool(const std::string &bot_id, const Val &a) {
  auto s = [&](const char *k) { auto v = a.find(k); return v ? trim(v->asStr()) : std::string(); };
  const std::string action = s("action");
  if (action == "recall") return memory_recall(bot_id, s("query"));
  if (action == "journal") {
    journal_append(bot_id, s("text"));
    return "Im Journal notiert.";
  }
  if (action == "update" || action == "forget") {
    int n = (int)(a.find("item") ? a.find("item")->asNum(0) : 0);
    std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
    int idx = agent_index(bot_id);
    if (idx < 0) throw std::runtime_error("Agent nicht gefunden.");
    Val &mem = AGENTS[idx]["memory"];
    if (!mem.isArr() || n < 1 || n > (int)mem.arr->size())
      throw std::runtime_error("Ungueltige Profil-Nummer " + std::to_string(n) + ".");
    if (action == "forget") {
      mem.arr->erase(mem.arr->begin() + (n - 1));
    } else {
      const std::string text = s("text").substr(0, 500);
      if (text.empty()) throw std::runtime_error("Text fehlt.");
      (*mem.arr)[n - 1]["text"] = text;
      (*mem.arr)[n - 1]["ts"] = (double)now_s();
    }
    save_agents();
    memory_git_sync(bot_id, action == "forget" ? "delete" : "edit");
    return action == "forget" ? "Vergessen." : "Aktualisiert.";
  }
  throw std::runtime_error("Unbekannte memory-Aktion: " + action);
}
