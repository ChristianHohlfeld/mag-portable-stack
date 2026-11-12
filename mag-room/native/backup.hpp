// A backup of everything when Chris asks for it (the settings or a bot in the
// chat): the room, Gitea and configuration as a restic snapshot
// (mag-backup-chat.service, ops/backup) and a save of every bot VM
// (mag-bot-saves@chat.service, magbot save-all). Both run in the background
// as units of their own; this only starts them and tells how they went.
#pragma once
#include "room.hpp"
#include "vm.hpp"
#include <ctime>

namespace mag::backup {

inline std::string unit_state(const std::string &unit) {
  RunOptions opt;
  opt.timeout_s = 10;
  return trim(mag::run({"systemctl", "is-active", unit}, opt).out);
}
inline bool running() {
  for (const char *unit : {"mag-backup-chat.service", "mag-bot-saves@chat.service"}) {
    const std::string state = unit_state(unit);
    if (state == "activating" || state == "active" || state == "reloading") return true;
  }
  return false;
}
// "" when it started (or was already running), else why not.
inline std::string start(vm::Guests &guests) {
  if (running()) return "";
  const RunResult r = guests.backup();
  return r.code == 0 ? "" : "Die Sicherung ließ sich nicht starten: " + clip(trim(r.out), 200);
}
inline std::string when(double epoch) {
  if (epoch <= 0) return "nie";
  const time_t t = (time_t)epoch;
  std::tm lt{};
  localtime_r(&t, &lt);
  char out[32];
  std::strftime(out, sizeof out, "%d.%m.%Y %H:%M", &lt);
  return out;
}
// What there is: {running, last_success, healthy, last_error, bots: [{bot, vm, saves: [...]}]}.
inline Val status(Room &room, vm::Guests &guests) {
  Val v = Val::object();
  v["running"] = running();
  const Val s = read_json(env("MAG_BACKUP_STATUS", "/var/lib/mag-backup/status.json"));
  v["last_success"] = jnum(s, "last_success");
  v["last_kind"] = jstr(s, "last_kind");
  v["healthy"] = jbool(s, "healthy") && now_s() - (int64_t)jnum(s, "last_success") < 26 * 3600;
  if (!jstr(s, "last_error").empty()) v["last_error"] = jstr(s, "last_error");
  Val bots = Val::array();
  for (auto &b : room.bots().asArr()) {
    const std::string vm = jstr(b, "vm");
    if (!vm::valid_name(vm)) continue;
    Val one = Val::object();
    one["bot"] = jstr(b, "id");
    one["name"] = jstr(b, "name", jstr(b, "id"));
    one["vm"] = vm;
    one["saves"] = guests.saves(vm);
    bots.push(one);
  }
  v["bots"] = bots;
  return v;
}
// The same in words, for a bot.
inline std::string report(Room &room, vm::Guests &guests) {
  const Val v = status(room, guests);
  std::string out = jbool(v, "running") ? "Eine Sicherung läuft gerade.\n" : "";
  out += "Letzte Sicherung von Raum, Gitea und Konfiguration (restic): " + when(jnum(v, "last_success")) +
         (jbool(v, "healthy") ? " (aktuell)" : " (älter als einen Tag)") + "\n";
  if (!jstr(v, "last_error").empty()) out += "Letzter Fehler: " + jstr(v, "last_error") + "\n";
  for (auto &b : v.find("bots")->asArr()) {
    const Val *saves = b.find("saves");
    out += "VM von " + jstr(b, "name") + ": ";
    if (!saves || saves->asArr().empty()) out += "noch keine Sicherung\n";
    else out += std::to_string(saves->asArr().size()) + " Sicherungen, die neueste " + jstr(saves->asArr()[0], "id") + "\n";
  }
  return out;
}

} // namespace mag::backup
