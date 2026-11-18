#pragma once
// What the bots do in their file systems, for Chris alone ("Dateien
// beobachten" in the settings). Every 15 s, and a moment after each tool
// step of a bot, the files below /home/bot of each running bot VM are listed
// (Guests::watch: size and mtime, read as the user bot through the guest
// agent, no log and nothing staged in the VM). What came, changed or went
// since the last listing is one entry: bot, file, kind, time, and for a text
// file of up to 64 KiB its content or a diff against the last state the room
// saw (STATE/filewatch/shadow). So a write by `write_file`, by any shell
// command or by a program the bot left running is seen alike.
//
// Nothing of it reaches a bot: no prompt, no prompt log, no room message, no
// event of the shared stream. The page asks for it with GET /api/filewatch,
// which only the owner may. A VM that is off or does not answer keeps its last
// listing; the first listing of a bot, and the first after the switch was
// off, only notes what is there.
#include "room.hpp"
#include "vm.hpp"
#include <condition_variable>
#include <thread>

namespace mag {

class FileWatch {
public:
  FileWatch(Room &room, vm::Guests &guests) : room_(room), guests_(guests) {
    dir_ = room_.cfg.state_dir + "/filewatch";
    mkdir(dir_.c_str(), 0700);
    mkdir((dir_ + "/shadow").c_str(), 0700);
    const Val saved = read_json(dir_ + "/events.json");
    if (saved.isArr()) events_ = saved;
    else events_ = Val::array();
    for (auto &e : events_.asArr()) next_ = std::max(next_, (int64_t)jnum(e, "n") + 1);
    interval_s_ = std::max(2L, envl("MAG_ROOM_FILEWATCH_INTERVAL_S", 15));
  }
  ~FileWatch() {
    {
      std::lock_guard<std::mutex> lk(mu_);
      stop_ = true;
    }
    cv_.notify_all();
    if (thread_.joinable()) thread_.join();
  }

  void start() { thread_ = std::thread([this] { loop(); }); }

  bool enabled() { return jbool(room_.settings(), "file_watch", true); }

  // A bot finished a tool step: look at its VM soon.
  void poke(const std::string &bot) {
    {
      std::lock_guard<std::mutex> lk(mu_);
      poked_.insert(bot);
      wake_ = true;
    }
    cv_.notify_all();
  }

  // The entries after `after`, newest last, without their content.
  Val list(int64_t after) {
    std::lock_guard<std::mutex> lk(mu_);
    Val out = Val::array();
    for (auto &e : events_.asArr()) {
      if ((int64_t)jnum(e, "n") <= after) continue;
      Val row = Val::object();
      for (auto &kv : *e.obj)
        if (kv.first != "detail") row[kv.first] = kv.second;
      row["has_detail"] = !jstr(e, "detail").empty();
      out.push(row);
    }
    return out;
  }
  // One entry with its content or diff.
  Val entry(int64_t n) {
    std::lock_guard<std::mutex> lk(mu_);
    for (auto &e : events_.asArr())
      if ((int64_t)jnum(e, "n") == n) return e;
    return Val();
  }
private:
  Room &room_;
  vm::Guests &guests_;
  std::string dir_;
  std::mutex mu_, look_mu_;
  std::condition_variable cv_;
  std::thread thread_;
  bool stop_ = false, wake_ = false, was_on_ = true;
  std::set<std::string> poked_;
  std::map<std::string, int64_t> last_look_;
  Val events_;
  int64_t next_ = 1;
  long interval_s_ = 15;
  static constexpr size_t kKeep = 400, kText = 65536, kDetail = 24000;
  static constexpr int kMostPerLook = 120, kReadPerLook = 30;

  std::string listing_path(const std::string &bot) const { return dir_ + "/seen-" + bot + ".json"; }
  std::string shadow_path(const std::string &bot, const std::string &rel) const {
    return dir_ + "/shadow/" + bot + "-" + sha256_hex(rel).substr(0, 32);
  }

  void loop() {
    int64_t next_round = 0;
    for (;;) {
      std::set<std::string> poked;
      {
        std::unique_lock<std::mutex> lk(mu_);
        cv_.wait_for(lk, std::chrono::seconds(1), [&] { return stop_ || wake_; });
        if (stop_) return;
        wake_ = false;
        poked = poked_;
      }
      try {
        const bool on = enabled();
        const Val bots = room_.bots();
        if (on && !was_on_) {
          // Switched on again: what happened while it was off is not reported.
          for (auto &b : bots.asArr()) unlink(listing_path(jstr(b, "id")).c_str());
        }
        was_on_ = on;
        if (!on) continue;
        const int64_t now = now_s();
        const bool round = now >= next_round;
        if (round) next_round = now + interval_s_;
        for (auto &b : bots.asArr()) {
          const std::string id = jstr(b, "id");
          if (!round && !poked.count(id)) continue;
          // A step's poke waits a moment after the last look; it stays until then.
          if (!round && now - last_look_[id] < 3) continue;
          {
            std::lock_guard<std::mutex> lk(mu_);
            poked_.erase(id);
          }
          look(id);
        }
      } catch (const std::exception &e) {
        logf(std::string("filewatch: ") + e.what());
      }
    }
  }

  // Text: valid UTF-8 without control characters other than tab, line ends,
  // form feed and escape (a read cut in the middle of the last sign is still text).
  static bool is_text(const std::string &s) {
    for (size_t i = 0; i < s.size();) {
      const unsigned char c = (unsigned char)s[i];
      if (c < 0x80) {
        if (c < 0x20 && c != '\t' && c != '\n' && c != '\r' && c != '\f' && c != 0x1b) return false;
        ++i;
        continue;
      }
      const int n = c >= 0xf0 && c < 0xf5 ? 3 : c >= 0xe0 && c < 0xf0 ? 2 : c >= 0xc2 && c < 0xe0 ? 1 : -1;
      if (n < 0) return false;
      if (i + n >= s.size()) return true;
      for (int k = 1; k <= n; ++k)
        if (((unsigned char)s[i + k] & 0xc0) != 0x80) return false;
      i += n + 1;
    }
    return true;
  }

  std::string diff(const std::string &old_path, const std::string &new_path) {
    RunOptions opt;
    opt.timeout_s = 20;
    opt.cap = kDetail + 4096;
    const RunResult r = run({"/usr/bin/diff", "-u", "--label", "vorher", "--label", "nachher", old_path, new_path}, opt);
    if (!r.spawned || (r.code != 0 && r.code != 1)) return "";
    return r.out;
  }

  void look(const std::string &bot) {
    std::lock_guard<std::mutex> one(look_mu_);
    last_look_[bot] = now_s();
    const std::string vm = jstr(room_.bot(bot), "vm");
    if (!vm::valid_name(vm) || guests_.state(vm) != "ready") return;
    const Val now = guests_.watch(vm, "list");
    const Val *files = now.find("files");
    if (!files || !files->isObj()) return; // off or not answering: nothing forgotten
    const Val before = read_json(listing_path(bot));
    const Val *old = before.find("files");
    write_json(listing_path(bot), now, 0600);
    if (!old || !old->isObj()) return; // the first look only notes what is there
    struct Change { std::string rel, kind; double size; };
    std::vector<Change> changes;
    for (auto &kv : *files->obj) {
      const Val *was = old->find(kv.first);
      const double size = kv.second.isArr() && !kv.second.asArr().empty() ? kv.second.asArr()[0].num : 0;
      if (!was) changes.push_back({kv.first, "neu", size});
      else if (dump(*was) != dump(kv.second)) changes.push_back({kv.first, "geändert", size});
    }
    if (!jbool(now, "cut") && !jbool(before, "cut"))
      for (auto &kv : *old->obj)
        if (!files->find(kv.first)) changes.push_back({kv.first, "gelöscht", 0});
    if (changes.empty()) return;
    // What to read: the text files that came or changed, small enough to show.
    std::string ask;
    int asked = 0;
    for (auto &c : changes)
      if (c.kind != "gelöscht" && c.size <= kText && asked < kReadPerLook && (int)(&c - &changes[0]) < kMostPerLook) {
        ask += c.rel + "\n";
        ++asked;
      }
    const Val got = ask.empty() ? Val() : guests_.watch(vm, "read", ask);
    const int64_t ts = now_ms();
    std::vector<Val> fresh;
    int count = 0;
    for (auto &c : changes) {
      if (++count > kMostPerLook) break;
      Val e = Val::object();
      e["bot"] = bot;
      e["path"] = "/home/bot/" + c.rel;
      e["kind"] = c.kind;
      e["ts"] = (double)ts;
      if (c.kind != "gelöscht") e["size"] = c.size;
      const std::string shadow = shadow_path(bot, c.rel);
      std::string detail, how;
      if (c.kind == "gelöscht") {
        std::string last;
        if (read_file(shadow, last, kText + 1) && !last.empty()) { detail = last; how = "zuletzt"; }
        unlink(shadow.c_str());
      } else if (const Val *b64 = got.isObj() ? got.find(c.rel) : nullptr; b64 && b64->type == Val::STR) {
        const std::string data = b64_decode(b64->str);
        if (!is_text(data)) {
          how = "binär";
          unlink(shadow.c_str());
        } else if (data.size() > kText) {
          detail = data.substr(0, kText);
          how = "anfang";
          unlink(shadow.c_str());
        } else {
          const std::string tmp = shadow + ".neu";
          write_atomic(tmp, data, 0600);
          if (exists(shadow)) {
            detail = diff(shadow, tmp);
            how = detail.empty() ? "gleich" : "diff";
          } else {
            detail = data;
            how = "inhalt";
          }
          rename(tmp.c_str(), shadow.c_str());
        }
      }
      if (!how.empty()) e["detail_kind"] = how;
      if (!detail.empty()) e["detail"] = clip(detail, kDetail, "\n…[gekürzt]");
      fresh.push_back(e);
    }
    if ((int)changes.size() > kMostPerLook) {
      Val e = Val::object();
      e["bot"] = bot;
      e["path"] = "/home/bot";
      e["kind"] = "und " + std::to_string(changes.size() - kMostPerLook) + " weitere Änderungen";
      e["ts"] = (double)ts;
      fresh.push_back(e);
    }
    std::lock_guard<std::mutex> lk(mu_);
    for (auto &e : fresh) {
      e["n"] = (double)next_++;
      events_.push(e);
    }
    if (events_.asArr().size() > kKeep) {
      Val kept = Val::array();
      const size_t from = events_.asArr().size() - kKeep;
      for (size_t i = from; i < events_.asArr().size(); ++i) kept.push(events_.asArr()[i]);
      events_ = kept;
    }
    write_json(dir_ + "/events.json", events_, 0600);
  }
};

} // namespace mag
