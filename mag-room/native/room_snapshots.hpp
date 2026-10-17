// Durable room snapshots. Included from magroom.cpp after scoped delete helpers.
// All writes stay below STATE_DIR/snapshots, never /etc; no service or database.
// Caller serializes ROOM / FILES through STATE_MTX.
#pragma once
#include <system_error>

static constexpr uint64_t SNAPSHOT_MAX_BYTES = 512ULL * 1024 * 1024;
static constexpr uint64_t SNAPSHOT_RETAIN_BYTES = 2ULL * 1024 * 1024 * 1024;
static constexpr size_t SNAPSHOT_RETAIN_COUNT = 5;
static constexpr int64_t SNAPSHOT_RETAIN_SECONDS = 7 * 86400;

static std::string snapshot_home() { return STATE_DIR + "/snapshots"; }
static bool snapshot_id_ok(const std::string &id) {
  auto dash = id.find('-');
  if (dash == std::string::npos || dash < 9 || dash > 12 ||
      id.size() != dash + 25) return false;  // unix seconds + 24 hex chars
  for (size_t i=0; i<id.size(); ++i) {
    unsigned char ch = (unsigned char)id[i];
    if (i == dash) continue;
    if (i < dash ? !std::isdigit(ch) : !std::isxdigit(ch)) return false;
  }
  return true;
}
static bool snapshot_regular(const std::string &path) {
  std::error_code ec;
  auto status = std::filesystem::symlink_status(path, ec);
  return !ec && std::filesystem::is_regular_file(status);
}
static bool snapshot_sync(const std::string &path, bool directory = false) {
  int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW |
                               (directory ? O_DIRECTORY : 0));
  if (fd < 0) return false;
  bool ok = fsync(fd) == 0;
  close(fd);
  return ok;
}
static Val snapshot_array(const std::vector<Val> &vec) {
  Val arr = Val::array();
  for (const auto &value : vec) arr.push(value);
  return arr;
}
static bool snapshot_create_parent(std::string &error) {
  std::error_code ec;
  const auto home = snapshot_home();
  auto status = std::filesystem::symlink_status(home, ec);
  if (!ec && !std::filesystem::is_directory(status)) {
    error = "Snapshot-Verzeichnis ist kein reguläres Verzeichnis"; return false;
  }
  ec.clear();
  std::filesystem::create_directories(home, ec);
  if (ec || chmod(home.c_str(), 0700) != 0) {
    error = "Snapshot-Verzeichnis nicht beschreibbar"; return false;
  }
  return true;
}
static std::string snapshot_dir(const std::string &id) {
  return snapshot_home() + "/" + id;
}
static bool snapshot_copy_checked(const std::string &from, const std::string &to,
                                  uint64_t &size, std::string &error) {
  if (!snapshot_regular(from)) {
    error = "Eine Raumdatei fehlt oder ist kein reguläres File"; return false;
  }
  std::error_code ec;
  uint64_t n = std::filesystem::file_size(from, ec);
  if (ec || n > (uint64_t)MAX_UPLOAD ||
      n > SNAPSHOT_MAX_BYTES - size) {
    error = "Snapshot-Limit von 512 MiB überschritten"; return false;
  }
  // Never follow a symlink at destination.
  if (std::filesystem::exists(to, ec)) {
    error = "Zieldatei bereits vorhanden"; return false;
  }
  ec.clear();
  if (!std::filesystem::copy_file(from, to, std::filesystem::copy_options::none, ec) || ec ||
      chmod(to.c_str(), 0600) || !snapshot_sync(to)) {
    error = "Datei konnte nicht dauerhaft gesichert werden"; return false;
  }
  size += n;
  return true;
}
static void snapshot_prune() {
  std::vector<std::pair<int64_t, std::string>> entries;
  std::error_code ec;
  for (auto &e : std::filesystem::directory_iterator(snapshot_home(), ec)) {
    auto id = e.path().filename().string();
    if (!snapshot_id_ok(id) || !e.is_directory()) continue;
    int64_t when = 0;
    try { when = std::stoll(id.substr(0,id.find('-'))); } catch (...) { continue; }
    entries.push_back({when, id});
  }
  std::sort(entries.begin(), entries.end(), [](const auto &a, const auto &b) {
    return a.first != b.first ? a.first > b.first : a.second > b.second;
  });
  uint64_t bytes = 0;
  for (size_t i=0; i<entries.size(); ++i) {
    const auto &item = entries[i];
    uint64_t n = 0;
    const auto dir = snapshot_dir(item.second);
    ec.clear();
    for (auto &f : std::filesystem::recursive_directory_iterator(dir, ec)) {
      if (ec) break;
      if (f.is_regular_file(ec)) {
        auto size = f.file_size(ec);
        if (!ec) n += size;
      }
    }
    const bool expired = item.first < now_s()-SNAPSHOT_RETAIN_SECONDS;
    const bool over = i >= SNAPSHOT_RETAIN_COUNT || n > SNAPSHOT_RETAIN_BYTES - std::min(bytes, SNAPSHOT_RETAIN_BYTES);
    // Always keep the newest snapshot, even if its timestamp is old.
    if (i > 0 && (expired || over)) {
      ec.clear();
      std::filesystem::remove_all(dir, ec);
    } else bytes += n;
  }
}
// Must be called while STATE_MTX is held, before clearing or restoring.
// Returns no ID unless all metadata + file bytes are atomically committed.
static bool snapshot_capture_locked(std::string &id, std::string &error,
                                    bool prune = true) {
  if (!snapshot_create_parent(error)) return false;
  id = std::to_string(now_s()) + "-" + token_hex(24); // 96-bit random suffix
  std::string stage = snapshot_home() + "/." + id + ".tmp";
  std::string dest = snapshot_dir(id);
  std::error_code ec;
  if (!std::filesystem::create_directory(stage, ec) || ec ||
      chmod(stage.c_str(), 0700)) {
    error = "Snapshot-Staging konnte nicht erstellt werden"; id.clear(); return false;
  }
  auto abort = [&] {
    std::error_code ignored;
    std::filesystem::remove_all(stage, ignored);
    id.clear();
    return false;
  };
  if (!std::filesystem::create_directory(stage + "/files", ec) || ec ||
      chmod((stage + "/files").c_str(), 0700)) {
    error = "Snapshot-Dateiverzeichnis konnte nicht erstellt werden";
    return abort();
  }
  uint64_t size = 0;
  size_t count = 0;
  for (const Val &file : FILES) {
    if (file.find("removed")) continue;
    auto v = file.find("stored");
    auto name = v ? v->asStr() : "";
    if (!is_stored_name(name) || ++count > 5000) {
      error = "Ungültige oder zu viele Dateieinträge"; return abort();
    }
    if (!snapshot_copy_checked(FILES_DIR+"/"+name,stage+"/files/"+name,size,error))
      return abort();
  }
  const auto room = mj::dump(snapshot_array(ROOM));
  const auto files = mj::dump(snapshot_array(FILES));
  if (room.size() > 64*1024*1024 || files.size() > 64*1024*1024 ||
      room.size()+files.size() > SNAPSHOT_MAX_BYTES-size) {
    error = "Snapshot-Metadaten überschreiten das Limit"; return abort();
  }
  try {
    mag::write(stage+"/room.json", room);
    mag::write(stage+"/files.json", files);
    Val meta = Val::object();
    meta["id"] = id;
    meta["created"] = (long)now_s();
    meta["bytes"] = (long)(size+room.size()+files.size());
    meta["file_count"] = (long)count;
    meta["message_count"] = (long)ROOM.size();
    mag::write(stage+"/meta.json", mj::dump(meta));
  } catch (...) {
    error = "Snapshot-Metadaten konnten nicht geschrieben werden"; return abort();
  }
  if (!snapshot_sync(stage+"/files",true) || !snapshot_sync(stage,true)) {
    error = "Snapshot konnte nicht synchronisiert werden"; return abort();
  }
  ec.clear();
  std::filesystem::rename(stage,dest,ec);
  if (ec || !snapshot_sync(snapshot_home(),true)) {
    error = "Snapshot-Commit fehlgeschlagen"; return abort();
  }
  if (prune) snapshot_prune();
  return true;
}
static Val snapshot_overview() {
  Val out = Val::object(), list = Val::array();
  std::error_code ec;
  if (!std::filesystem::is_directory(snapshot_home(), ec)) {
    out["snapshots"] = list; return out;
  }
  std::vector<std::pair<std::string, Val>> rows;
  for (auto &e : std::filesystem::directory_iterator(snapshot_home(),ec)) {
    if (ec) break;
    auto id = e.path().filename().string();
    if (!snapshot_id_ok(id) || !e.is_directory()) continue;
    Val meta;
    bool ok = false;
    auto raw = read_file(snapshot_dir(id)+"/meta.json",&ok);
    if (!ok || !mj::parse(raw,meta) || !meta.isObj() ||
        (meta.find("id") && meta.find("id")->asStr()!=id)) continue;
    rows.push_back({id,meta});
  }
  std::sort(rows.begin(),rows.end(),[](auto &a,auto &b){return a.first>b.first;});
  for (auto &r : rows) list.push(r.second);
  out["snapshots"] = list;
  return out;
}
static ScopedResult snapshot_restore(const std::string &id) {
  if (!snapshot_id_ok(id)) return scoped_err(400,"Ungültige Snapshot-ID");
  ScopedResult done;
  {
  std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
  const std::string source = snapshot_dir(id);
  std::error_code ec;
  if (!std::filesystem::is_directory(source,ec) ||
      std::filesystem::is_symlink(std::filesystem::symlink_status(source,ec)))
    return scoped_err(404,"Snapshot nicht gefunden");
  bool room_ok=false,files_ok=false;
  auto raw_room=read_file(source+"/room.json",&room_ok);
  auto raw_files=read_file(source+"/files.json",&files_ok);
  Val room,files;
  if (!room_ok || !files_ok || raw_room.size()>64*1024*1024 ||
      raw_files.size()>64*1024*1024 ||
      !mj::parse(raw_room,room) || !room.isArr() ||
      !mj::parse(raw_files,files) || !files.isArr())
    return scoped_err(422,"Snapshot beschädigt");
  uint64_t total=raw_room.size()+raw_files.size();
  std::vector<std::string> restore;
  for (auto &f : files.asArr()) {
    auto v=f.find("stored");
    auto name=v ? v->asStr() : "";
    if (!is_stored_name(name)) return scoped_err(422,"Ungültiger Dateieintrag");
    if (f.find("removed")) continue;
    auto file=source+"/files/"+name;
    if (!snapshot_regular(file)) return scoped_err(422,"Snapshot-Datei fehlt");
    uint64_t sz=std::filesystem::file_size(file,ec);
    if (ec || sz>SNAPSHOT_MAX_BYTES-total)
      return scoped_err(422,"Snapshot zu groß");
    total+=sz;
    restore.push_back(name);
  }
  if (total>SNAPSHOT_MAX_BYTES) return scoped_err(422,"Snapshot zu groß");
  std::string previous,error;
  if (!snapshot_capture_locked(previous,error,false))
    return scoped_err(507,"Vorheriger Zustand nicht gesichert: "+error);
  // Stage every file before changing state. Never purge current files here;
  // they remain recoverable in the automatically-created previous snapshot.
  std::string staging = FILES_DIR + "/.restore-" + token_hex(12);
  if (!std::filesystem::create_directory(staging,ec) || ec)
    return scoped_err(507,"Restore-Staging fehlgeschlagen");
  auto finish = [&] { std::error_code ignored; std::filesystem::remove_all(staging,ignored); };
  uint64_t bytes=0;
  for (const auto &name : restore) {
    if (!snapshot_copy_checked(source+"/files/"+name,staging+"/"+name,bytes,error)) {
      finish(); return scoped_err(507,error);
    }
  }
  for (const auto &name : restore) {
    ec.clear();
    std::filesystem::rename(staging+"/"+name, FILES_DIR+"/"+name,ec);
    if (ec) {finish();return scoped_err(507,"Restore-Datei konnte nicht übernommen werden");}
  }
  finish();
  auto old_room=ROOM,old_files=FILES;
  ROOM = *room.arr;
  FILES = *files.arr;
  try {
    save_room();
    save_json_array(FILES_JSON,FILES);
    clear_reaction_notes();
  } catch (...) {
    ROOM=std::move(old_room); FILES=std::move(old_files);
    try { save_room(); save_json_array(FILES_JSON,FILES); } catch (...) {}
    return scoped_err(507,"Restore-Persistenz fehlgeschlagen; alten Zustand wiederherstellen");
  }
  snapshot_prune();
  Val result=Val::object();
  result["ok"]=true;
  result["restored"]=id;
  result["previous_snapshot"]=previous;
  result["messages"]=(long)ROOM.size();
  result["files"]=(long)FILES.size();
  done=scoped_ok(result);
  } // release STATE_MTX before publishing SSE state change
  room_changed();
  return done;
}

// Run retention even if no further clears occur. Reuse magroom's process;
// no additional daemon or systemd unit is required.
static void snapshot_retention_loop() {
  for (;;) {
    std::this_thread::sleep_for(std::chrono::hours(1));
    std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
    std::error_code ec;
    if (std::filesystem::is_directory(snapshot_home(),ec)) snapshot_prune();
  }
}
