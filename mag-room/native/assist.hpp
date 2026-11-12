// The bots as assistants: approvals per action with an auto mode, and
// routines that run on their own.
//
// Approvals (`consent`): before a consequential action (send, buy, delete,
// publish, set up a routine) a bot asks the room. The owner's rules decide
// (settings `assist`: per kind ask/auto/never, the auto mode, money limits
// per purchase and per day): allowed at once, refused, or a card at the
// bot's step that waits for Chris (allow or deny, 15 minutes); a request
// written as text is taken back by the engine and asked again through the
// tool. Every decision (who, what, amount, to, when, allowed or not, whether
// by the auto mode) is a line of the journal, each line sealed with
// AES-256-GCM (STATE/assist/ledger.enc; the key STATE/assist/ledger.key,
// 0600, read only by this process, never in a prompt, a tool or the page);
// the day's spending is summed from it. Plugins that change something keep their
// own card (mcp.hpp); this covers what a bot does in its VM's browser or
// shell, which the room cannot intercept, so the rule lives in the tool and
// the prompt.
//
// Routines: an instruction for one bot, at a time (daily, on weekdays or
// days of the week, every n minutes, once) or on an event (a picture Chris
// posts, a job of the graphics card Chris ordered being done). A routine
// that is due becomes a message of the chat ("Routine »…«: …") addressed to
// its bot, which answers it in a normal turn. STATE/routines.json keeps
// them with their last run; a run missed while the room was down runs once
// when it is back (if it is at most 12 hours late). A tick every 30 s.
#pragma once
#include "room.hpp"

#include <condition_variable>
#include <ctime>
#include <deque>
#include <filesystem>
#include <fstream>
#include <set>

#include <openssl/evp.h>

namespace mag {

class Assist {
public:
  // A field of a constant object, or null.
  static const Val &at(const Val &v, const char *key) {
    static const Val none;
    const Val *found = v.find(key);
    return found ? *found : none;
  }

  // Set by main: a routine is due; post it and give its bot the turn.
  std::function<void(const Val &routine, const std::string &why)> fire;

  explicit Assist(Room &room) : room_(room) {
    std::error_code ec;
    std::filesystem::create_directories(room_.cfg.state_dir + "/assist", ec);
    std::thread([this] { tick(); }).detach();
  }

  static constexpr const char *kKinds[] = {"send", "buy", "delete", "publish", "routine"};
  static std::string kind_label(const std::string &kind) {
    if (kind == "send") return "Senden";
    if (kind == "buy") return "Kaufen";
    if (kind == "delete") return "Löschen";
    if (kind == "publish") return "Veröffentlichen";
    if (kind == "routine") return "Routine einrichten";
    return "Aktion";
  }

  // The owner's rules as they stand, with defaults filled in.
  Val rules() {
    const Val s = room_.settings();
    const Val *a = s.find("assist");
    Val out = Val::object(), per = Val::object();
    out["auto"] = a ? jbool(*a, "auto", false) : false;
    for (const char *k : kKinds) {
      std::string v = a && a->find("rules") ? jstr(at(*a, "rules"), k, "ask") : "ask";
      if (v != "ask" && v != "auto" && v != "never") v = "ask";
      per[k] = v;
    }
    out["rules"] = per;
    out["once_eur"] = a ? std::max(0.0, jnum(*a, "once_eur", 0)) : 0.0;
    out["day_eur"] = a ? std::max(0.0, jnum(*a, "day_eur", 0)) : 0.0;
    out["spent_today"] = spent_today();
    return out;
  }

  // What the owner may set (PUT /api/settings {"assist": {...}}), checked.
  static Val clean_rules(const Val &in) {
    Val out = Val::object(), per = Val::object();
    out["auto"] = jbool(in, "auto", false);
    for (const char *k : kKinds) {
      std::string v = in.find("rules") ? jstr(at(in, "rules"), k, "ask") : "ask";
      per[k] = (v == "auto" || v == "never") ? v : "ask";
    }
    out["rules"] = per;
    out["once_eur"] = std::clamp(jnum(in, "once_eur", 0), 0.0, 100000.0);
    out["day_eur"] = std::clamp(jnum(in, "day_eur", 0), 0.0, 100000.0);
    return out;
  }

  // A bot asks before an action. `ask` shows the card at its step (an object)
  // and takes it away again (null). Returns what the bot reads.
  std::string consent(const std::string &bot_id, const Val &args, const Cancel *cancel, const std::function<void(const Val &)> &ask,
                      bool *granted = nullptr) {
    std::string kind = jstr(args, "action");
    if (std::find(std::begin(kKinds), std::end(kKinds), kind) == std::end(kKinds)) kind = "other";
    const std::string what = clip(trim(jstr(args, "what")), 600, "…"), to = clip(trim(jstr(args, "to")), 200, "…");
    const double amount = std::max(0.0, jnum(args, "amount_eur", 0));
    if (granted) *granted = false;
    if (what.empty()) return "Fehler: Beschreibe in `what`, was genau geschehen soll.";
    if (kind == "buy" && amount <= 0) return "Fehler: Ein Kauf braucht den Betrag in amount_eur.";
    const Val r = rules();
    const std::string rule = kind == "other" ? "ask" : jstr(at(r, "rules"), kind.c_str(), "ask");
    std::string decision, why;
    bool by_auto = false;
    if (rule == "never") {
      decision = "deny";
      why = "Regel: " + kind_label(kind) + " ist nie erlaubt";
    } else {
      bool auto_ok = rule == "auto" || (jbool(r, "auto") && rule == "ask");
      if (kind == "buy") {
        // Money only within the limits; above them Chris decides, always.
        const double once = jnum(r, "once_eur"), day = jnum(r, "day_eur"), spent = jnum(r, "spent_today");
        if (once <= 0 || amount > once || day <= 0 || spent + amount > day) auto_ok = false;
      }
      if (auto_ok) {
        decision = "allow";
        by_auto = true;
        why = rule == "auto" ? "Regel: ohne Nachfrage" : "Auto-Modus";
      } else {
        Val card = Val::object();
        card["bot"] = bot_id;
        card["action"] = kind;
        card["label"] = kind_label(kind);
        card["what"] = what;
        if (!to.empty()) card["to"] = to;
        if (amount > 0) card["amount_eur"] = amount;
        decision = await_card(card, cancel, ask);
        why = decision == "allow" ? "Chris hat erlaubt" : decision == "deny" ? "Chris hat abgelehnt" : decision == "stopped" ? "abgebrochen" : "keine Antwort in 15 Minuten";
      }
    }
    journal(bot_id, kind, what, to, amount, decision, why, by_auto);
    const bool yes = decision == "allow";
    if (granted) *granted = yes;
    if (yes) return "Freigegeben (" + why + "): " + kind_label(kind) + " – " + what + ". Führe genau das jetzt aus, nichts darüber hinaus.";
    return "Nicht freigegeben (" + why + "): " + kind_label(kind) + " – " + what + ". Führe es nicht aus; sag Chris kurz, was offen bleibt.";
  }

  // The last decisions, newest first, for the settings.
  Val journal_tail(size_t n = 20) {
    std::deque<Val> last;
    {
      std::lock_guard<std::mutex> lk(mu_);
      std::ifstream f(ledger());
      std::string line;
      while (std::getline(f, line)) {
        last.push_back(jparse(unseal(line)));
        if (last.size() > n) last.pop_front();
      }
    }
    Val out = Val::array();
    for (auto it = last.rbegin(); it != last.rend(); ++it)
      if (it->isObj()) out.push(*it);
    return out;
  }

  // The owner's answer to a pending card. False: no such card waits.
  // allow/deny for an action, done/cancel for a login. Only the owner's
  // request (/api/approval, /api/login) gets here, never a model.
  bool decide(const std::string &id, const std::string &decision) {
    if (decision != "allow" && decision != "deny" && decision != "done" && decision != "cancel") return false;
    {
      std::lock_guard<std::mutex> lk(mu_);
      const auto it = pending_.find(id);
      if (it == pending_.end() || !it->second.empty()) return false;
      it->second = decision;
    }
    cv_.notify_all();
    return true;
  }

  // A card that waits at a bot's step until Chris decides, the turn is
  // stopped, or 15 minutes have passed. Returns the decision, "stopped" or
  // "timeout".
  std::string await_card(Val card, const Cancel *cancel, const std::function<void(const Val &)> &ask) {
    const std::string id = random_hex(8);
    card["id"] = id;
    {
      std::lock_guard<std::mutex> lk(mu_);
      pending_[id] = "";
      cards_[id] = card;
    }
    if (ask) ask(card);
    const int64_t until = mono_ms() + std::clamp(envl("MAG_ASSIST_APPROVAL_S", 900), 2L, 86400L) * 1000;
    std::string decision;
    {
      std::unique_lock<std::mutex> lk(mu_);
      for (;;) {
        if (!pending_[id].empty()) { decision = pending_[id]; break; }
        if (cancel && cancel->stopped()) { decision = "stopped"; break; }
        if (mono_ms() >= until) { decision = "timeout"; break; }
        cv_.wait_for(lk, std::chrono::milliseconds(200));
      }
      pending_.erase(id);
      cards_.erase(id);
    }
    if (ask) ask(Val());
    return decision;
  }

  // A card that still waits (for /api/login: whose browser to fill), or null.
  Val waiting_card(const std::string &id) {
    std::lock_guard<std::mutex> lk(mu_);
    const auto it = cards_.find(id);
    return it == cards_.end() || !pending_[id].empty() ? Val() : it->second;
  }

  // ---- routines -------------------------------------------------------------
  Val routines() {
    std::lock_guard<std::mutex> lk(routines_mu_);
    Val all = read_json(path());
    return all.isArr() ? all : Val::array();
  }

  // A routine from a bot's description: {name, bot, instruction, when}; when is
  // {"daily": "07:30"} | {"weekdays": "08:00"} | {"days": [1..7], "time"} |
  // {"every_minutes": n} | {"once": "2026-11-14 18:00"} | {"event": "picture"|"job_done"}.
  // Returns "" with `out` set, or why not in words.
  std::string add(const Val &in, const std::string &bot_id, Val &out) {
    const std::string name = clip(trim(jstr(in, "name")), 80, ""), text = clip(trim(jstr(in, "instruction")), 2000, "…");
    if (name.empty() || text.empty()) return "Eine Routine braucht name und instruction.";
    const Val *when = in.find("when");
    if (!when || !when->isObj()) return "Wann soll sie laufen? when fehlt.";
    Val w = Val::object();
    if (const std::string t = jstr(*when, "daily"); !t.empty()) { if (!clock_ok(t)) return "Uhrzeit als HH:MM."; w["time"] = t; w["days"] = days({1, 2, 3, 4, 5, 6, 7}); }
    else if (const std::string t = jstr(*when, "weekdays"); !t.empty()) { if (!clock_ok(t)) return "Uhrzeit als HH:MM."; w["time"] = t; w["days"] = days({1, 2, 3, 4, 5}); }
    else if (const Val *d = when->find("days"); d && d->isArr()) {
      const std::string t = jstr(*when, "time");
      if (!clock_ok(t)) return "Uhrzeit als HH:MM.";
      std::vector<int> list;
      for (auto &x : d->asArr()) if (x.type == Val::NUM && x.num >= 1 && x.num <= 7) list.push_back((int)x.num);
      if (list.empty()) return "Wochentage als Zahlen 1 (Montag) bis 7 (Sonntag).";
      w["time"] = t;
      w["days"] = days(list);
    } else if (when->find("every_minutes")) {
      const double n = jnum(*when, "every_minutes");
      if (n < 15) return "Höchstens alle 15 Minuten.";
      w["every_minutes"] = std::min(n, 60.0 * 24 * 31);
    } else if (const std::string t = jstr(*when, "once"); !t.empty()) {
      const int64_t at = parse_local(t);
      if (at <= now_s()) return "Der Zeitpunkt (JJJJ-MM-TT HH:MM) muss in der Zukunft liegen.";
      w["once"] = (double)at;
    } else if (const std::string e = jstr(*when, "event"); e == "picture" || e == "job_done") {
      w["event"] = e;
    } else {
      return "when kennt daily, weekdays, days+time, every_minutes, once oder event (picture, job_done).";
    }
    std::unique_lock<std::mutex> lk(routines_mu_);
    Val all = read_json(path());
    if (!all.isArr()) all = Val::array();
    if (all.asArr().size() >= 20) return "Es gibt schon 20 Routinen; erst eine löschen.";
    Val r = Val::object();
    r["id"] = random_hex(6);
    r["name"] = name;
    // The bot by its id or its name; an unknown one is the asking bot.
    std::string who = bot_id;
    const Val bots = room_.bots();
    for (auto &b : bots.asArr())
      if (const std::string want = lower(trim(jstr(in, "bot"))); !want.empty() && (want == jstr(b, "id") || want == lower(jstr(b, "name")))) who = jstr(b, "id");
    r["bot"] = who;
    r["instruction"] = text;
    r["when"] = w;
    r["by"] = bot_id;
    r["created"] = (double)now_s();
    r["paused"] = false;
    r["next"] = (double)next_after(w, now_s());
    all.push(r);
    write_json(path(), all);
    out = r;
    lk.unlock();
    publish();
    return "";
  }

  // pause | resume | remove | run; "" or why not.
  std::string act(const std::string &id, const std::string &what) {
    Val due;
    {
      std::lock_guard<std::mutex> lk(routines_mu_);
      Val all = read_json(path());
      if (!all.isArr()) return "Keine Routinen.";
      Val kept = Val::array();
      bool found = false;
      for (auto &r : all.asArr()) {
        if (jstr(r, "id") != id && lower(jstr(r, "name")) != lower(id)) { kept.push(r); continue; }
        found = true;
        if (what == "remove") continue;
        Val c = r;
        if (what == "pause") c["paused"] = true;
        else if (what == "resume") { c["paused"] = false; c["next"] = (double)next_after(c["when"], now_s()); }
        else if (what == "run") { due = c; c["last"] = (double)now_s(); }
        else return "pause, resume, remove oder run.";
        kept.push(c);
      }
      if (!found) return "Diese Routine gibt es nicht.";
      write_json(path(), kept);
    }
    publish();
    if (due.isObj() && fire) fire(due, "von Hand");
    return "";
  }

  static std::string describe_when(const Val &w) {
    static const char *names[] = {"", "Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"};
    if (w.find("every_minutes")) {
      const long n = (long)jnum(w, "every_minutes");
      return n % 60 == 0 ? "alle " + std::to_string(n / 60) + " h" : "alle " + std::to_string(n) + " min";
    }
    if (w.find("once")) return "einmal am " + local_label((int64_t)jnum(w, "once"));
    if (const std::string e = jstr(w, "event"); !e.empty()) return e == "picture" ? "bei jedem Bild von Chris" : "wenn ein Auftrag der Grafikkarte fertig ist";
    std::string d;
    if (const Val *days = w.find("days"); days && days->isArr()) {
      if (days->asArr().size() == 7) d = "täglich";
      else if (days->asArr().size() == 5 && days->asArr()[0].num == 1 && days->asArr()[4].num == 5) d = "werktags";
      else for (auto &x : days->asArr()) d += (d.empty() ? "" : ", ") + std::string(names[(int)x.num]);
    }
    return d + " " + jstr(w, "time");
  }

  std::string list_text() {
    const Val all = routines();
    if (all.asArr().empty()) return "Keine Routine eingerichtet.";
    std::string out;
    for (auto &r : all.asArr())
      out += "- " + jstr(r, "name") + " (" + jstr(r, "id") + ", " + jstr(r, "bot") + ", " + describe_when(at(r, "when")) +
             (jbool(r, "paused") ? ", pausiert" : "") + "): " + clip(jstr(r, "instruction"), 200, "…") + "\n";
    return out;
  }

private:
  Room &room_;
  std::mutex mu_, routines_mu_;
  std::condition_variable cv_;
  std::map<std::string, std::string> pending_; // card id -> decision ("" while waiting)
  std::map<std::string, Val> cards_;           // card id -> the card, while it waits
  int64_t seen_ms_ = 0;                        // messages up to here were looked at for events
  std::set<std::string> done_;                 // jobs of the graphics card already seen done

  std::string path() const { return room_.cfg.state_dir + "/routines.json"; }
  std::string ledger() const { return room_.cfg.state_dir + "/assist/ledger.enc"; }
  std::string key_;   // the journal's key, loaded once

  // The journal's key: 32 random bytes made on first use (0600).
  const std::string &key() {
    if (key_.size() == 32) return key_;
    const std::string file = room_.cfg.state_dir + "/assist/ledger.key";
    key_ = slurp(file);
    if (key_.size() != 32) {
      key_ = random_bytes(32);
      if (!write_atomic(file, key_, 0600)) logf("assist: the journal's key could not be written");
    }
    return key_;
  }

  // One line sealed: base64 of nonce (12), ciphertext and tag (16).
  std::string seal(const std::string &plain) {
    const std::string nonce = random_bytes(12);
    std::string out(plain.size() + 16, '\0');
    int len = 0, fin = 0;
    EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
    bool ok = c && EVP_EncryptInit_ex(c, EVP_aes_256_gcm(), nullptr, (const unsigned char *)key().data(), (const unsigned char *)nonce.data()) == 1 &&
              EVP_EncryptUpdate(c, (unsigned char *)&out[0], &len, (const unsigned char *)plain.data(), (int)plain.size()) == 1 &&
              EVP_EncryptFinal_ex(c, (unsigned char *)&out[0] + len, &fin) == 1 &&
              EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_GCM_GET_TAG, 16, (unsigned char *)&out[0] + len + fin) == 1;
    if (c) EVP_CIPHER_CTX_free(c);
    if (!ok) return "";
    out.resize(len + fin + 16);
    return b64_encode(nonce + out);
  }

  // A sealed line opened, or "" when it is not one of ours or was changed.
  std::string unseal(const std::string &line) {
    const std::string raw = b64_decode(line);
    if (raw.size() < 12 + 16) return "";
    const std::string nonce = raw.substr(0, 12), body = raw.substr(12, raw.size() - 28), tag = raw.substr(raw.size() - 16);
    std::string out(body.size() + 16, '\0');
    int len = 0, fin = 0;
    EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
    bool ok = c && EVP_DecryptInit_ex(c, EVP_aes_256_gcm(), nullptr, (const unsigned char *)key().data(), (const unsigned char *)nonce.data()) == 1 &&
              EVP_DecryptUpdate(c, (unsigned char *)&out[0], &len, (const unsigned char *)body.data(), (int)body.size()) == 1 &&
              EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_GCM_SET_TAG, 16, (void *)tag.data()) == 1 &&
              EVP_DecryptFinal_ex(c, (unsigned char *)&out[0] + len, &fin) == 1;
    if (c) EVP_CIPHER_CTX_free(c);
    if (!ok) return "";
    out.resize(len + fin);
    return out;
  }

  void publish() {
    Val ev = Val::object();
    ev["routines"] = routines();
    room_.bus.publish("routines", ev);
  }

  void journal(const std::string &bot, const std::string &kind, const std::string &what, const std::string &to, double amount,
               const std::string &decision, const std::string &why, bool by_auto) {
    Val line = Val::object();
    line["ts"] = (double)now_s();
    line["bot"] = bot;
    line["action"] = kind;
    line["what"] = what;
    if (!to.empty()) line["to"] = to;
    if (amount > 0) line["amount_eur"] = amount;
    line["decision"] = decision;
    line["why"] = why;
    line["auto"] = by_auto;
    std::lock_guard<std::mutex> lk(mu_);
    const std::string sealed = seal(mj::dump(line));
    if (sealed.empty()) { logf("assist: a journal line could not be sealed"); return; }
    std::ofstream f(ledger(), std::ios::app);
    f << sealed << "\n";
    f.close();
    ::chmod(ledger().c_str(), 0600);
  }

  // What was allowed to be spent since local midnight.
  double spent_today() {
    std::time_t t = std::time(nullptr);
    std::tm day{};
    localtime_r(&t, &day);
    day.tm_hour = day.tm_min = day.tm_sec = 0;
    const int64_t since = (int64_t)std::mktime(&day);
    double sum = 0;
    std::lock_guard<std::mutex> lk(mu_);
    std::ifstream f(ledger());
    std::string line;
    while (std::getline(f, line)) {
      const Val v = jparse(unseal(line));
      if (jstr(v, "action") == "buy" && jstr(v, "decision") == "allow" && jnum(v, "ts") >= since) sum += jnum(v, "amount_eur");
    }
    return sum;
  }

  static Val days(const std::vector<int> &list) {
    Val a = Val::array();
    for (int d : list) a.push((double)d);
    return a;
  }
  static bool clock_ok(const std::string &t) {
    int h = -1, m = -1;
    return sscanf(t.c_str(), "%d:%d", &h, &m) == 2 && h >= 0 && h < 24 && m >= 0 && m < 60;
  }
  static int64_t parse_local(const std::string &t) {
    std::tm tm{};
    if (sscanf(t.c_str(), "%d-%d-%d %d:%d", &tm.tm_year, &tm.tm_mon, &tm.tm_mday, &tm.tm_hour, &tm.tm_min) != 5 &&
        sscanf(t.c_str(), "%d-%d-%dT%d:%d", &tm.tm_year, &tm.tm_mon, &tm.tm_mday, &tm.tm_hour, &tm.tm_min) != 5)
      return 0;
    tm.tm_year -= 1900;
    tm.tm_mon -= 1;
    tm.tm_isdst = -1;
    return (int64_t)std::mktime(&tm);
  }
  static std::string local_label(int64_t at) {
    std::time_t t = (std::time_t)at;
    std::tm tm{};
    localtime_r(&t, &tm);
    char buf[32];
    strftime(buf, sizeof buf, "%d.%m. %H:%M", &tm);
    return buf;
  }

  // The first time after `from` this routine is due (0: never again, or an event).
  static int64_t next_after(const Val &w, int64_t from) {
    if (w.find("every_minutes")) return from + (int64_t)(jnum(w, "every_minutes") * 60);
    if (w.find("once")) return (int64_t)jnum(w, "once") > from ? (int64_t)jnum(w, "once") : 0;
    const Val *days = w.find("days");
    if (!days || !days->isArr()) return 0;
    int h = 0, m = 0;
    sscanf(jstr(w, "time").c_str(), "%d:%d", &h, &m);
    for (int ahead = 0; ahead < 9; ++ahead) {
      std::time_t t = (std::time_t)from;
      std::tm tm{};
      localtime_r(&t, &tm);
      tm.tm_mday += ahead;
      tm.tm_hour = h;
      tm.tm_min = m;
      tm.tm_sec = 0;
      tm.tm_isdst = -1;
      const std::time_t at = std::mktime(&tm);
      std::tm check{};
      localtime_r(&at, &check);
      const int weekday = check.tm_wday == 0 ? 7 : check.tm_wday;
      bool on = false;
      for (auto &d : days->asArr()) on = on || (int)d.num == weekday;
      if (on && at > from) return (int64_t)at;
    }
    return 0;
  }

  // Every 30 s: routines that are due, and events since the last look.
  void tick() {
    seen_ms_ = now_ms();
    for (auto &m : room_.all())
      if (const Val *m3 = m.find("model3d"); m3 && jstr(*m3, "state") == "done") done_.insert(jstr(m, "id"));
    for (;;) {
      std::this_thread::sleep_for(std::chrono::seconds(30));
      try {
        due_by_time();
        due_by_event();
      } catch (const std::exception &e) {
        logf(std::string("assist: tick: ") + e.what());
      }
    }
  }

  void due_by_time() {
    std::vector<Val> due;
    const int64_t now = now_s();
    {
      std::lock_guard<std::mutex> lk(routines_mu_);
      Val all = read_json(path());
      if (!all.isArr()) return;
      bool changed = false;
      for (auto &r : *all.arr) {
        const int64_t next = (int64_t)jnum(r, "next");
        if (jbool(r, "paused") || next <= 0 || next > now) continue;
        // Late by more than 12 hours (the room was down): skipped, not made up.
        if (now - next <= 12 * 3600) {
          due.push_back(r);
          r["last"] = (double)now;
        }
        r["next"] = (double)next_after(r["when"], now);
        changed = true;
      }
      if (changed) write_json(path(), all);
    }
    for (auto &r : due) if (fire) fire(r, "Zeit");
    if (!due.empty()) publish();
  }

  // A picture Chris posted, or a job of the graphics card he ordered that is
  // done. An event routine runs at most every 15 minutes, so a routine that
  // makes pictures itself cannot set itself off in a circle.
  void due_by_event() {
    bool picture = false, job = false;
    int64_t newest = seen_ms_;
    for (auto &m : room_.all()) {
      const int64_t ts = (int64_t)jnum(m, "ts");
      const Val *m3 = m.find("model3d");
      if (ts > seen_ms_ && jstr(m, "from") == "chris")
        if (const Val *atts = m.find("attachments"); atts && atts->isArr())
          for (auto &a : atts->asArr()) picture = picture || jstr(a, "kind") == "image";
      if (m3 && jstr(*m3, "state") == "done" && done_.insert(jstr(m, "id")).second && !jstr(*m3, "job").empty() &&
          (jstr(*m3, "by").empty() || jstr(*m3, "by") == "chris"))
        job = true;
      newest = std::max(newest, ts);
    }
    seen_ms_ = newest;
    if (!picture && !job) return;
    std::vector<Val> due;
    const int64_t now = now_s();
    {
      std::lock_guard<std::mutex> lk(routines_mu_);
      Val all = read_json(path());
      if (!all.isArr()) return;
      bool changed = false;
      for (auto &r : *all.arr) {
        const std::string e = jstr(r["when"], "event");
        if (jbool(r, "paused") || e.empty() || now - (int64_t)jnum(r, "last") < 15 * 60) continue;
        if ((e == "picture" && picture) || (e == "job_done" && job)) {
          due.push_back(r);
          r["last"] = (double)now;
          changed = true;
        }
      }
      if (changed) write_json(path(), all);
    }
    for (auto &r : due) if (fire) fire(r, jstr(r["when"], "event") == "picture" ? "neues Bild" : "Auftrag fertig");
    if (!due.empty()) publish();
  }
};

} // namespace mag
