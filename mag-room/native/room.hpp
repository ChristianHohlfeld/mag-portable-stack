// Room state: configuration, the event bus and the single message store.
// Everything in the timeline is a message (text, bot work, files, 3D results);
// each one is a JSON file under STATE/messages, attachments under STATE/files.
#pragma once
#include "util.hpp"

#include <condition_variable>
#include <deque>
#include <set>

namespace mag {

struct Config {
  std::string state_dir, files_dir, web_dir, bind_host, bridge_ip, gateway, models_status,
      models_control, idol_url, scene_url, rtc_url, stt_url, tts_dir, guest_run, agent_ctl, brave_key, browser_binary,
      version, access_file, s23_token_file;
  bool sudo = true; // the guest wrappers run through sudo (off only for the test fixture)
  int port = 8766, bridge_port = 8767, max_steps = 48, turn_budget_s = 3600, tool_timeout_s = 900,
      max_tokens = 4096, bot_rounds = 0, s23_port = 18721;
  static Config from_env() {
    Config c;
    c.state_dir = env("MAG_ROOM_STATE", "/var/lib/magbot/room");
    c.files_dir = c.state_dir + "/files";
    c.web_dir = env("MAG_ROOM_WEB", "/opt/mag-stack/current/web");
    c.bind_host = env("MAG_ROOM_BIND", "127.0.0.1");
    c.port = (int)envl("MAG_ROOM_PORT", 8766);
    c.bridge_ip = env("MAG_ROOM_BRIDGE_IP", "192.168.122.1");
    c.bridge_port = (int)envl("MAG_ROOM_BRIDGE_PORT", 8767);
    // Empty gateway: talk to the llama-server ports directly (tests, dev).
    c.gateway = env("MAG_ROOM_GATEWAY", "http://127.0.0.1:8080");
    if (c.gateway == "direct") c.gateway.clear();
    c.models_status = env("MAG_MODELS_STATUS", "/run/mag-models/status.json");
    c.models_control = env("MAG_MODELS_CONTROL", "/var/lib/mag-models/control");
    c.idol_url = env("MAG_IDOL_URL", "http://127.0.0.1:8793");
    c.scene_url = env("MAG_SCENE_URL", "http://127.0.0.1:8795");
    c.rtc_url = env("MAG_RTC_URL", "http://127.0.0.1:18784");
    c.guest_run = env("MAG_ROOM_GUEST_RUN", "/usr/local/sbin/magbot-guest-run");
    c.agent_ctl = env("MAG_ROOM_AGENT_CTL", "/usr/local/sbin/magbot-agent-ctl");
    c.sudo = env("MAG_ROOM_NO_SUDO") != "1";
    c.brave_key = env("BRAVE_API_KEY", env("MAG_ROOM_BRAVE_KEY"));
    c.stt_url = env("MAG_STT_URL", "http://127.0.0.1:8194");
    c.tts_dir = env("MAG_TTS_DIR", "/opt/mag-tts"); // the voices the browser reads aloud with (ops/tts)
    // The control daemon of Chris's phone (devices/s23), if he runs it. Its token is read when it is needed.
    c.s23_port = (int)envl("MAG_S23_PORT", 18721);
    c.s23_token_file = env("MAG_S23_TOKEN_FILE", "/run/mag-s23/token");
    c.browser_binary = env("MAG_BROWSER_BINARY", "/opt/mag-stack/current/bin/mag-browser");
    c.access_file = env("MAG_ROOM_ACCESS", c.state_dir + "/access.json");
    c.max_steps = (int)envl("MAG_ROOM_MAX_STEPS", 48);
    c.turn_budget_s = (int)envl("MAG_ROOM_RUN_BUDGET_S", 3600);
    c.tool_timeout_s = (int)envl("MAG_ROOM_TOOL_TIMEOUT_S", 900);
    c.max_tokens = (int)envl("MAG_ROOM_MAX_TOKENS", 4096);
    c.bot_rounds = (int)envl("MAG_ROOM_BOT_ROUNDS", 0);
    return c;
  }
};

// ------------------------------------------------------------------ events
// One ordered stream. A subscriber that falls too far behind is closed; the
// client then reconnects and resyncs from a snapshot, so nothing is lost.
struct Subscriber {
  std::mutex mu;
  std::condition_variable cv;
  std::deque<std::string> queue;
  bool overflow = false;
};

class Bus {
public:
  uint64_t publish(const char *type, Val payload) {
    std::lock_guard<std::mutex> lk(mu_);
    if (!payload.isObj()) payload = Val::object();
    payload["t"] = type;
    payload["seq"] = (double)++seq_;
    const std::string frame = "id: " + std::to_string(seq_) + "\ndata: " + mj::dump(payload) + "\n\n";
    for (auto &s : subs_) {
      std::lock_guard<std::mutex> sl(s->mu);
      if (s->queue.size() > 8000) s->overflow = true;
      else s->queue.push_back(frame);
      s->cv.notify_one();
    }
    return seq_;
  }
  uint64_t seq() {
    std::lock_guard<std::mutex> lk(mu_);
    return seq_;
  }
  std::shared_ptr<Subscriber> subscribe() {
    auto s = std::make_shared<Subscriber>();
    std::lock_guard<std::mutex> lk(mu_);
    subs_.insert(s);
    return s;
  }
  void unsubscribe(const std::shared_ptr<Subscriber> &s) {
    std::lock_guard<std::mutex> lk(mu_);
    subs_.erase(s);
  }
  size_t subscribers() {
    std::lock_guard<std::mutex> lk(mu_);
    return subs_.size();
  }

private:
  std::mutex mu_;
  uint64_t seq_ = 0;
  std::set<std::shared_ptr<Subscriber>> subs_;
};

// ------------------------------------------------------------------- store
inline std::string attachment_kind(const std::string &mime, const std::string &name) {
  if (starts_with(mime, "image/")) return "image";
  if (starts_with(mime, "audio/")) return "audio";
  if (starts_with(mime, "video/")) return "video";
  const std::string n = lower(name);
  if (ends_with(n, ".glb") || ends_with(n, ".stl") || ends_with(n, ".ply")) return "model";
  return "file";
}

class Room {
public:
  Config cfg;
  Bus bus;

  explicit Room(Config c) : cfg(std::move(c)) {
    for (const char *sub : {"", "/messages", "/files", "/wire", "/uploads", "/vision", "/text"})
      fs::create_directories(cfg.state_dir + sub);
    load();
  }

  // ---- messages -----------------------------------------------------------
  Val add(Val m) {
    std::lock_guard<std::mutex> lk(mu_);
    if (jstr(m, "id").empty()) m["id"] = new_id();
    if (!m.find("ts")) m["ts"] = (double)now_ms();
    if (!m.find("state")) m["state"] = "done";
    const std::string id = jstr(m, "id");
    messages_[id] = m;
    persist_locked(id);
    Val ev = Val::object();
    ev["m"] = m;
    bus.publish("message", ev);
    return m;
  }

  bool get(const std::string &id, Val &out) {
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = messages_.find(id);
    if (it == messages_.end()) return false;
    out = it->second;
    return true;
  }

  // Applies fn and publishes the whole message. For rare, structural changes
  // (state, attachments, 3D progress); streaming uses the appenders below.
  bool update(const std::string &id, const std::function<void(Val &)> &fn, bool persist = true) {
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = messages_.find(id);
    if (it == messages_.end()) return false;
    fn(it->second);
    if (persist) persist_locked(id);
    Val ev = Val::object();
    ev["m"] = it->second;
    bus.publish("message", ev);
    return true;
  }

  // Streaming text of a bot message: appended to its last text part, or to a
  // new one when the last part is a step. Order of parts is order of events.
  void append_text(const std::string &id, const std::string &delta) {
    if (delta.empty()) return;
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = messages_.find(id);
    if (it == messages_.end()) return;
    Val &parts = it->second["parts"];
    if (!parts.isArr()) parts = Val::array();
    if (parts.arr->empty() || jstr(parts.arr->back(), "k") != "text") {
      Val part = Val::object();
      part["k"] = "text";
      part["text"] = "";
      parts.push(part);
    }
    Val &last = parts.arr->back();
    last["text"] = jstr(last, "text") + delta;
    Val ev = Val::object();
    ev["id"] = id;
    ev["part"] = (double)(parts.arr->size() - 1);
    ev["text"] = delta;
    bus.publish("delta", ev);
  }

  // How long the text is that a bot's answer is being written into right now
  // (its last part, when that is text).
  size_t text_len(const std::string &id) {
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = messages_.find(id);
    if (it == messages_.end()) return 0;
    const Val *parts = it->second.find("parts");
    return parts && parts->isArr() && !parts->asArr().empty() && jstr(parts->asArr().back(), "k") == "text" ? jstr(parts->asArr().back(), "text").size() : 0;
  }
  // How many parts (texts and steps) a bot's answer has so far.
  size_t parts(const std::string &id) {
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = messages_.find(id);
    const Val *list = it == messages_.end() ? nullptr : it->second.find("parts");
    return list ? list->size() : 0;
  }
  // Takes back what was streamed into that text beyond `keep` bytes and puts
  // `text` there instead: a model that ran in circles keeps its first round.
  void retake_text(const std::string &id, size_t keep, const std::string &text) {
    update(id, [&](Val &m) {
      Val &parts = m["parts"];
      if (!parts.isArr()) parts = Val::array();
      if (parts.arr->empty() || jstr(parts.arr->back(), "k") != "text") {
        if (text.empty()) return;
        Val part = Val::object();
        part["k"] = "text";
        part["text"] = "";
        parts.push(part);
      }
      Val &last = parts.arr->back();
      last["text"] = mj::utf8_cut(jstr(last, "text"), keep) + text;
    }, false);
  }

  void append_reasoning(const std::string &id, const std::string &delta) {
    if (delta.empty()) return;
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = messages_.find(id);
    if (it == messages_.end()) return;
    it->second["reasoning"] = jstr(it->second, "reasoning") + delta;
    Val ev = Val::object();
    ev["id"] = id;
    ev["text"] = delta;
    bus.publish("reasoning", ev);
  }

  // Writes down what a seeing bot saw in an image of this message ("" when
  // it could not tell): the bots without eyes read it, and nobody asks twice.
  bool describe(const std::string &id, const std::string &stored, const std::string &text) {
    return update(id, [&](Val &m) {
      Val *atts = m.find("attachments");
      if (!atts || !atts->isArr()) return;
      for (auto &a : *atts->arr) {
        if (jstr(a, "file") != stored) continue;
        a["seen"] = true;
        if (!text.empty()) a["description"] = text;
      }
    });
  }

  // Adds a step part (a tool call) and returns its part index.
  int add_step(const std::string &id, const Val &step) {
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = messages_.find(id);
    if (it == messages_.end()) return -1;
    Val &parts = it->second["parts"];
    if (!parts.isArr()) parts = Val::array();
    parts.push(step);
    persist_locked(id);
    Val ev = Val::object();
    ev["id"] = id;
    ev["part"] = (double)(parts.arr->size() - 1);
    ev["step"] = step;
    bus.publish("step", ev);
    return (int)parts.arr->size() - 1;
  }

  void step_output(const std::string &id, int part, const std::string &chunk) {
    if (chunk.empty()) return;
    std::lock_guard<std::mutex> lk(mu_);
    Val *step = step_locked(id, part);
    if (!step) return;
    // The stored output is a bounded tail; the full log stays in the VM.
    std::string out = jstr(*step, "output") + mj::utf8_fix(chunk);
    if (out.size() > kStepOutputMax) {
      out = tail(out, kStepOutputMax * 3 / 4);
      (*step)["cut"] = true;
    }
    (*step)["output"] = out;
    Val ev = Val::object();
    ev["id"] = id;
    ev["part"] = (double)part;
    ev["text"] = mj::utf8_fix(chunk);
    bus.publish("step_output", ev);
  }

  void update_step(const std::string &id, int part, const std::function<void(Val &)> &fn) {
    std::lock_guard<std::mutex> lk(mu_);
    Val *step = step_locked(id, part);
    if (!step) return;
    fn(*step);
    persist_locked(id);
    Val ev = Val::object();
    ev["id"] = id;
    ev["part"] = (double)part;
    ev["step"] = *step;
    bus.publish("step", ev);
  }

  // Deletes one message with its attachments. Returns the removed message so
  // the caller can clean up what lives elsewhere (Idol job directories).
  bool remove(const std::string &id, Val *removed = nullptr) {
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = messages_.find(id);
    if (it == messages_.end()) return false;
    if (removed) *removed = it->second;
    drop_files_locked(it->second);
    messages_.erase(it);
    unlink((cfg.state_dir + "/messages/" + id + ".json").c_str());
    Val ev = Val::object();
    ev["id"] = id;
    bus.publish("gone", ev);
    return true;
  }

  // Empties the room: every message and every attachment. Returns what was
  // removed (for Idol cleanup).
  std::vector<Val> clear() {
    std::lock_guard<std::mutex> lk(mu_);
    std::vector<Val> gone;
    for (auto &kv : messages_) gone.push_back(kv.second);
    messages_.clear();
    std::error_code ec;
    for (const char *sub : {"/messages", "/files", "/uploads", "/vision", "/text"}) {
      fs::remove_all(cfg.state_dir + sub, ec);
      fs::create_directories(cfg.state_dir + sub, ec);
    }
    // Conversation leftovers of the previous room version (archived tool
    // output, code blobs, plans, prompt logs) go with the room as well.
    for (const char *old : {"/context-artifacts", "/code", "/todo", "/upload-parts", "/trash", "/room.json", "/files.json",
                            "/queue.json", "/queue.json.bak-loop", "/prompt-prefix.json", "/reaction_notices.json"})
      fs::remove_all(cfg.state_dir + old, ec);
    for (auto &entry : fs::directory_iterator(cfg.state_dir, ec))
      if (starts_with(entry.path().filename().string(), "prompt-wire-")) fs::remove(entry.path(), ec);
    bus.publish("cleared", Val::object());
    return gone;
  }

  std::vector<Val> all() {
    std::lock_guard<std::mutex> lk(mu_);
    std::vector<Val> out;
    for (auto &kv : messages_) out.push_back(kv.second);
    return out;
  }
  // Messages with id > after, oldest first.
  std::vector<Val> since(const std::string &after) {
    std::lock_guard<std::mutex> lk(mu_);
    std::vector<Val> out;
    for (auto it = messages_.upper_bound(after); it != messages_.end(); ++it) out.push_back(it->second);
    return out;
  }
  std::string last_id() {
    std::lock_guard<std::mutex> lk(mu_);
    return messages_.empty() ? "" : messages_.rbegin()->first;
  }
  size_t count() {
    std::lock_guard<std::mutex> lk(mu_);
    return messages_.size();
  }

  // Newest `limit` messages (before `before` when given), oldest first, with
  // the event sequence the page reflects.
  Val page(const std::string &before, size_t limit, uint64_t *seq = nullptr, bool *more = nullptr) {
    std::lock_guard<std::mutex> lk(mu_);
    if (seq) *seq = bus.seq();
    auto end = before.empty() ? messages_.end() : messages_.lower_bound(before);
    std::vector<const Val *> picked;
    auto it = end;
    while (it != messages_.begin() && picked.size() < limit) {
      --it;
      picked.push_back(&it->second);
    }
    if (more) *more = it != messages_.begin();
    Val out = Val::array();
    for (auto r = picked.rbegin(); r != picked.rend(); ++r) out.push(**r);
    return out;
  }

  // ---- attachments --------------------------------------------------------
  // Stores bytes as a room file and returns its attachment record.
  Val store_file(const std::string &name, const std::string &data, const std::string &by) {
    const std::string safe = safe_name(name);
    const std::string stored = new_id() + "_" + safe;
    const std::string path = cfg.files_dir + "/" + stored;
    if (!write_atomic(path, data, 0640)) return Val();
    return attachment(stored, safe, (int64_t)data.size(), by);
  }
  // Adopts an existing file (moved into the store).
  Val adopt_file(const std::string &source, const std::string &name, const std::string &by) {
    const std::string safe = safe_name(name);
    const std::string stored = new_id() + "_" + safe;
    std::error_code ec;
    fs::rename(source, cfg.files_dir + "/" + stored, ec);
    if (ec) {
      fs::copy_file(source, cfg.files_dir + "/" + stored, ec);
      if (ec) return Val();
      fs::remove(source, ec);
    }
    return attachment(stored, safe, file_size(cfg.files_dir + "/" + stored), by);
  }
  std::string file_path(const std::string &stored) const {
    if (stored.empty() || stored.find('/') != std::string::npos || stored.find("..") != std::string::npos ||
        stored[0] == '.')
      return "";
    return cfg.files_dir + "/" + stored;
  }
  // Uploads wait here until a message references them.
  void stage(const Val &att) {
    std::lock_guard<std::mutex> lk(mu_);
    staged_[jstr(att, "file")] = {att, now_s()};
    // Forget (and delete) uploads nobody sent within a day.
    for (auto it = staged_.begin(); it != staged_.end();) {
      if (now_s() - it->second.second > 86400) {
        unlink((cfg.files_dir + "/" + it->first).c_str());
        it = staged_.erase(it);
      } else ++it;
    }
  }
  bool take_staged(const std::string &stored, Val &att) {
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = staged_.find(stored);
    if (it == staged_.end()) return false;
    att = it->second.first;
    staged_.erase(it);
    return true;
  }

  // ---- bots and settings --------------------------------------------------
  Val bots() {
    std::lock_guard<std::mutex> lk(mu_);
    return bots_;
  }
  Val bot(const std::string &id) {
    std::lock_guard<std::mutex> lk(mu_);
    for (auto &b : bots_.asArr())
      if (jstr(b, "id") == id) return b;
    return Val();
  }
  bool update_bot(const std::string &id, const std::function<void(Val &)> &fn) {
    std::lock_guard<std::mutex> lk(mu_);
    if (!bots_.isArr()) return false;
    for (auto &b : *bots_.arr)
      if (jstr(b, "id") == id) {
        fn(b);
        save_bots_locked();
        return true;
      }
    return false;
  }
  Val settings() {
    std::lock_guard<std::mutex> lk(mu_);
    return settings_;
  }
  void update_settings(const std::function<void(Val &)> &fn) {
    std::lock_guard<std::mutex> lk(mu_);
    fn(settings_);
    write_json(cfg.state_dir + "/settings.json", settings_);
    Val ev = Val::object();
    ev["settings"] = settings_;
    bus.publish("settings", ev);
  }

  // ---- skills ---------------------------------------------------------------
  // How-tos the bots share (STATE/skills.json): when a skill applies, its
  // steps, how to check the result, what needs the owner's approval. A skill
  // describes work; it grants nothing.
  Val skills() {
    std::lock_guard<std::mutex> lk(skills_mu_);
    Val all = read_json(cfg.state_dir + "/skills.json");
    return all.isArr() ? all : Val::array();
  }
  // Saves or replaces a skill. Returns "" or why it was not saved.
  std::string save_skill(Val skill) {
    const std::string name = trim(jstr(skill, "name"));
    if (name.empty() || name.size() > 80 || name.find_first_of("\r\n") != std::string::npos) return "Der Name fehlt oder ist zu lang (höchstens 80 Zeichen, eine Zeile).";
    std::string all_text;
    for (const char *key : {"when", "steps", "verify", "approval"}) {
      const std::string text = trim(jstr(skill, key));
      if (text.empty() || text.size() > (std::string(key) == "steps" ? 4000u : 1000u)) return std::string(key) + " fehlt oder ist zu lang.";
      skill[key] = text;
      all_text += text + "\n";
    }
    // Nothing that looks like a credential becomes a reusable instruction.
    for (const char *mark : {"-----BEGIN ", "ghp_", "github_pat_", "hf_", "sk-", "Bearer "})
      if (all_text.find(mark) != std::string::npos) return "Zugangsdaten gehören nicht in einen Skill.";
    skill["name"] = name;
    skill["updated"] = (double)now_s();
    std::lock_guard<std::mutex> lk(skills_mu_);
    Val all = read_json(cfg.state_dir + "/skills.json");
    if (!all.isArr()) all = Val::array();
    for (auto &known : *all.arr)
      if (jstr(known, "name") == name) {
        known = skill;
        return write_json(cfg.state_dir + "/skills.json", all) ? "" : "Der Skill ließ sich nicht speichern.";
      }
    if (all.arr->size() >= 24) return "Es gibt schon 24 Skills; mehr werden nicht gespeichert.";
    all.push(skill);
    return write_json(cfg.state_dir + "/skills.json", all) ? "" : "Der Skill ließ sich nicht speichern.";
  }
  bool remove_skill(const std::string &name) {
    std::lock_guard<std::mutex> lk(skills_mu_);
    Val all = read_json(cfg.state_dir + "/skills.json");
    if (!all.isArr()) return false;
    const size_t before = all.arr->size();
    all.arr->erase(std::remove_if(all.arr->begin(), all.arr->end(), [&](const Val &s) { return jstr(s, "name") == name; }), all.arr->end());
    return all.arr->size() != before && write_json(cfg.state_dir + "/skills.json", all);
  }

  std::mutex &mutex() { return mu_; }

private:
  static constexpr size_t kStepOutputMax = 96 * 1024;
  std::mutex mu_, skills_mu_;
  std::map<std::string, Val> messages_;
  std::map<std::string, std::pair<Val, int64_t>> staged_;
  Val bots_ = Val::array(), settings_ = Val::object();

  Val attachment(const std::string &stored, const std::string &name, int64_t size, const std::string &by) {
    Val a = Val::object();
    a["file"] = stored;
    a["name"] = name;
    a["mime"] = mime_for(name);
    a["size"] = (double)size;
    a["kind"] = attachment_kind(mime_for(name), name);
    a["by"] = by;
    return a;
  }

  Val *step_locked(const std::string &id, int part) {
    const auto it = messages_.find(id);
    if (it == messages_.end()) return nullptr;
    Val *parts = it->second.find("parts");
    if (!parts || !parts->isArr() || part < 0 || (size_t)part >= parts->arr->size()) return nullptr;
    Val &p = (*parts->arr)[(size_t)part];
    return jstr(p, "k") == "step" ? &p : nullptr;
  }

  void persist_locked(const std::string &id) {
    const auto it = messages_.find(id);
    if (it == messages_.end()) return;
    write_json(cfg.state_dir + "/messages/" + id + ".json", it->second);
  }

  void drop_files_locked(const Val &m) {
    if (const Val *atts = m.find("attachments"); atts && atts->isArr())
      for (auto &a : atts->asArr()) {
        const std::string path = file_path(jstr(a, "file"));
        if (path.empty()) continue;
        unlink(path.c_str());
        unlink((cfg.state_dir + "/vision/" + jstr(a, "file") + ".jpg").c_str()); // its copy for the models
        unlink((cfg.state_dir + "/text/" + jstr(a, "file") + ".txt").c_str());   // what a document says
      }
  }

  void save_bots_locked() {
    write_json(cfg.state_dir + "/bots.json", bots_);
    // The WebRTC gateway and the boot helper read agents.json: [{id, vm, ...}].
    Val agents = Val::array();
    for (auto &b : bots_.asArr()) {
      Val a = Val::object();
      a["id"] = jstr(b, "id");
      a["name"] = jstr(b, "name");
      a["vm"] = jstr(b, "vm");
      a["enabled"] = jbool(b, "enabled", true);
      agents.push(a);
    }
    write_json(cfg.state_dir + "/agents.json", agents);
  }

  static Val default_bot(const char *id, const char *name) {
    Val b = Val::object();
    b["id"] = id;
    b["name"] = name;
    b["vm"] = "";
    b["enabled"] = true;
    b["thinking"] = false;
    b["persona"] = "";
    b["memory"] = Val::array();
    return b;
  }

  void load() {
    std::error_code ec;
    for (auto &entry : fs::directory_iterator(cfg.state_dir + "/messages", ec)) {
      if (entry.path().extension() != ".json") continue;
      Val m = read_json(entry.path().string());
      const std::string id = jstr(m, "id");
      if (!m.isObj() || id.empty()) continue;
      // A bot answer that was still streaming when the process ended is final
      // now. A 3D job or the film of a scene is not: it runs in its worker,
      // which neither knows nor cares that the room restarted. Its message
      // stays as it is and the room picks the job up again (Idol::resume,
      // Scenes::resume).
      if (jstr(m, "state") == "streaming" && !m.find("model3d")) {
        m["state"] = "stopped";
        if (Val *parts = m.find("parts"); parts && parts->isArr())
          for (auto &p : *parts->arr)
            if (jstr(p, "k") == "step" && jstr(p, "state") == "running") p["state"] = "cancelled";
        write_json(entry.path().string(), m);
      }
      messages_[id] = m;
    }
    // Files nobody references any more (an upload that was never sent, a
    // crash between store and message) do not pile up.
    std::set<std::string> used;
    for (auto &kv : messages_)
      if (const Val *atts = kv.second.find("attachments"); atts && atts->isArr())
        for (auto &a : atts->asArr()) used.insert(jstr(a, "file"));
    for (auto &entry : fs::directory_iterator(cfg.files_dir, ec))
      if (!used.count(entry.path().filename().string())) fs::remove(entry.path(), ec);
    for (auto &entry : fs::directory_iterator(cfg.state_dir + "/uploads", ec)) fs::remove(entry.path(), ec);
    for (const char *cache : {"/vision", "/text"})
      for (auto &entry : fs::directory_iterator(cfg.state_dir + cache, ec)) {
        const std::string name = entry.path().filename().string();
        if (name.size() < 5 || !used.count(name.substr(0, name.size() - 4))) fs::remove(entry.path(), ec);
      }
    settings_ = read_json(cfg.state_dir + "/settings.json");
    if (!settings_.isObj()) settings_ = Val::object();
    if (!settings_.find("bot_rounds")) settings_["bot_rounds"] = cfg.bot_rounds;
    if (!settings_.find("web")) settings_["web"] = true;

    bots_ = read_json(cfg.state_dir + "/bots.json");
    if (!bots_.isArr() || bots_.asArr().empty()) {
      // First start of this version: adopt name, VM and memory of the bots
      // the previous room kept in agents.json (kept as agents.v1.json).
      bots_ = Val::array();
      Val lena = default_bot("lena", "Lena"), noah = default_bot("noah", "Noah");
      Val old = read_json(cfg.state_dir + "/agents.json");
      if (old.isArr()) {
        for (auto &a : old.asArr()) {
          const std::string name = lower(jstr(a, "name"));
          Val *target = name == "lena" ? &lena : name == "noah" ? &noah : nullptr;
          if (!target) continue;
          (*target)["vm"] = jstr(a, "vm");
          if (const Val *mem = a.find("memory"); mem && mem->isArr()) (*target)["memory"] = *mem;
          if (const Val *pl = a.find("plugins")) (*target)["plugins"] = *pl;
        }
        if (!exists(cfg.state_dir + "/agents.v1.json"))
          write_json(cfg.state_dir + "/agents.v1.json", old);
      }
      bots_.push(lena);
      bots_.push(noah);
      save_bots_locked();
    }
  }
};

} // namespace mag
