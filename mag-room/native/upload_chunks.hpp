// Large authenticated attachments: up to 16 MiB idempotent chunks, disk-backed.
// Fewer network roundtrips and fdatasyncs without parallel offset races.
// Included after add_file() and before routes.hpp; never expose partial files.
#pragma once
static constexpr uint64_t MAX_CHUNKED_FILE = 512ULL * 1024 * 1024;
static constexpr size_t UPLOAD_PART_BYTES = 16 * 1024 * 1024;
static std::mutex UPLOAD_MTX;
static std::string upload_dir() {
  const std::string dir = STATE_DIR + "/upload-parts";
  std::filesystem::create_directories(dir);
  chmod(dir.c_str(), 0700);
  return dir;
}
static bool upload_valid_id(const std::string &id) {
  if (id.size() != 32) return false;
  for (unsigned char c : id)
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  return true;
}
static std::string upload_safe_name(const std::string &name) {
  std::string safe;
  for (unsigned char c : basename_of(name))
    safe.push_back(std::isalnum(c) || c == '.' || c == '_' || c == '-' ? c : '_');
  if (safe.empty()) safe = "datei";
  if (safe.size() > 80) safe.resize(80);
  if (safe == "." || safe == "..") safe = "datei";
  return safe;
}
static std::string upload_meta(const std::string &id) { return upload_dir() + "/" + id + ".json"; }
static std::string upload_part(const std::string &id) { return upload_dir() + "/" + id + ".part"; }
static Val upload_load(const std::string &id) {
  if (!upload_valid_id(id)) throw std::runtime_error("Ungültige Upload-ID");
  bool exists = false;
  const std::string data = read_file(upload_meta(id), &exists);
  if (!exists) throw std::runtime_error("Upload nicht gefunden");
  Val v;
  if (!mj::parse(data, v) || !v.isObj())
    throw std::runtime_error("Upload-Metadaten beschädigt");
  return v;
}
static std::string upload_field(const Val &v, const char *name) {
  const auto p = v.find(name);
  return p ? p->asStr() : "";
}
static uint64_t upload_total(const Val &v) {
  auto p = v.find("total");
  const double n = p ? p->asNum(0) : 0;
  if (n < 1 || n > (double)MAX_CHUNKED_FILE || (uint64_t)n != n)
    throw std::runtime_error("Ungültige Upload-Größe");
  return (uint64_t)n;
}
static uint64_t upload_size(const std::string &path) {
  std::error_code ec;
  auto n = std::filesystem::file_size(path, ec);
  return ec ? 0 : (uint64_t)n;
}
static void upload_purge_sessions() {
  std::lock_guard<std::mutex> lock(UPLOAD_MTX);
  std::error_code ec;
  std::filesystem::remove_all(upload_dir(), ec);
}
static void upload_gc() {
  const auto dir = upload_dir();
  const auto now = std::filesystem::file_time_type::clock::now();
  std::error_code ec;
  for (const auto &entry : std::filesystem::directory_iterator(dir, ec)) {
    if (entry.path().extension() != ".json" || !entry.is_regular_file()) continue;
    auto age = now - entry.last_write_time(ec);
    if (ec) { ec.clear(); continue; }
    if (age < std::chrono::hours(24)) continue;
    auto p = entry.path(); auto id = p.stem().string();
    if (!upload_valid_id(id)) continue;
    std::filesystem::remove(upload_part(id), ec); ec.clear();
    std::filesystem::remove(p, ec); ec.clear();
  }
}
static Val upload_start_or_read(const std::string &id, const std::string &name,
                                uint64_t total, const std::string &owner,
                                const std::string &by, bool create) {
  if (!upload_valid_id(id)) throw std::runtime_error("Ungültige Upload-ID");
  const auto meta = upload_meta(id);
  bool exists = false;
  (void)read_file(meta, &exists);
  Val v;
  if (!exists) {
    if (!create) throw std::runtime_error("Upload nicht gefunden");
    if (total == 0 || total > MAX_CHUNKED_FILE)
      throw std::runtime_error("Datei über 512 MB");
    upload_gc();
    size_t pending = 0;
    for (const auto &entry : std::filesystem::directory_iterator(upload_dir())) {
      if (entry.path().extension() != ".json") continue;
      bool found = false;
      Val old; const auto raw = read_file(entry.path().string(), &found);
      if (found && mj::parse(raw, old) && old.isObj() &&
          upload_field(old, "owner") == owner && !old.find("done")) ++pending;
    }
    if (pending >= 4) throw std::runtime_error("Maximal vier unfertige Uploads");
    v = Val::object();
    v["name"] = upload_safe_name(name);
    v["total"] = (double)total;
    v["owner"] = owner;
    v["by"] = by.substr(0, 40);
    v["ts"] = (double)now_s();
    if (!write_file_atomic(meta, mj::dump(v)))
      throw std::runtime_error("Upload konnte nicht angelegt werden");
    int file = open(upload_part(id).c_str(), O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (file < 0) throw std::runtime_error("Upload-Zwischendatei konnte nicht angelegt werden");
    close(file);
  } else v = upload_load(id);
  if (upload_field(v, "owner") != owner ||
      (total != 0 && upload_total(v) != total) ||
      (!name.empty() && upload_field(v, "name") != upload_safe_name(name)))
    throw std::runtime_error("Upload gehört zu einer anderen Anfrage");
  return v;
}
static Val upload_status(const std::string &id, const std::string &owner) {
  std::lock_guard<std::mutex> lock(UPLOAD_MTX);
  // No session yet is the normal first-request state, not an HTTP error.
  // A different owner's existing session remains inaccessible (404).
  if (!std::filesystem::exists(upload_meta(id))) {
    Val out = Val::object();
    out["exists"] = false;
    out["done"] = false;
    out["received"] = 0;
    return out;
  }
  Val v = upload_load(id);
  if (upload_field(v,"owner") != owner) throw std::runtime_error("Upload nicht gefunden");
  Val out = Val::object();
  out["exists"] = true;
  if (auto done = v.find("done")) {
    out["done"] = true; out["file"] = *done;
    out["received"] = (double)upload_total(v);
  } else {
    out["done"] = false;
    out["received"] = (double)upload_size(upload_part(id));
  }
  return out;
}
static Val upload_chunk(const std::string &id, const std::string &name,
                        uint64_t total, uint64_t offset, const std::string &data,
                        const std::string &owner, const std::string &by) {
  if (data.empty() || data.size() > UPLOAD_PART_BYTES || total > MAX_CHUNKED_FILE ||
      total == 0 || offset > total || data.size() > total - offset)
    throw std::runtime_error("Ungültiger Upload-Block");
  std::lock_guard<std::mutex> lock(UPLOAD_MTX);
  Val v = upload_start_or_read(id, name, total, owner, by, offset == 0);
  if (v.find("done")) throw std::runtime_error("Upload bereits abgeschlossen");
  const auto path = upload_part(id);
  int file = open(path.c_str(), O_RDWR | O_CLOEXEC | O_NOFOLLOW);
  if (file < 0) throw std::runtime_error("Upload-Zwischendatei fehlt");
  struct stat st{};
  if (fstat(file,&st) || !S_ISREG(st.st_mode) || st.st_size < 0) {
    close(file); throw std::runtime_error("Ungültige Upload-Datei");
  }
  uint64_t received = (uint64_t)st.st_size;
  if (offset > received || received > total) {
    close(file); throw std::runtime_error("Upload-Offset stimmt nicht");
  }
  if (offset < received) {
    // Only identical retransmissions are idempotent. Any mismatch fails closed.
    if (data.size() > received - offset) {
      close(file); throw std::runtime_error("Überlappender Upload-Block");
    }
    std::string existing(data.size(), '\0');
    ssize_t got = pread(file, existing.data(), existing.size(), (off_t)offset);
    close(file);
    if (got != (ssize_t)data.size() || existing != data)
      throw std::runtime_error("Wiederholter Upload-Block weicht ab");
  } else {
    size_t done = 0;
    while (done < data.size()) {
      ssize_t n = pwrite(file, data.data() + done, data.size() - done, (off_t)(offset+done));
      if (n < 0 && errno == EINTR) continue;
      if (n <= 0) { close(file); throw std::runtime_error("Upload-Schreibfehler"); }
      done += (size_t)n;
    }
    if (fdatasync(file)) { close(file); throw std::runtime_error("Upload-Sicherung fehlgeschlagen"); }
    close(file);
    received += data.size();
  }
  Val out = Val::object();
  out["received"] = (double)received;
  out["done"] = false;
  return out;
}
static Val upload_commit(const std::string &id, const std::string &owner, bool &fresh) {
  std::lock_guard<std::mutex> lock(UPLOAD_MTX);
  Val v = upload_load(id);
  if (upload_field(v,"owner") != owner) throw std::runtime_error("Upload nicht gefunden");
  fresh = false;
  if (const auto done = v.find("done")) return *done;
  const auto total = upload_total(v);
  std::string stored = upload_field(v,"stored");
  if (stored.empty()) {
    if (upload_size(upload_part(id)) != total)
      throw std::runtime_error("Upload unvollständig");
    stored = token_hex(12) + "_" + upload_field(v,"name");
    v["stored"] = stored;
    if (!write_file_atomic(upload_meta(id), mj::dump(v)))
      throw std::runtime_error("Upload-Metadaten konnten nicht gesichert werden");
  }
  const auto destination = FILES_DIR + "/" + stored;
  const auto part = upload_part(id);
  std::error_code ec;
  if (std::filesystem::exists(part,ec)) {
    if (std::filesystem::exists(destination,ec))
      throw std::runtime_error("Zieldatei existiert bereits");
    std::filesystem::rename(part, destination, ec);
    if (ec) throw std::runtime_error("Upload konnte nicht übernommen werden");
  }
  if (upload_size(destination) != total)
    throw std::runtime_error("Upload-Datei nach Übernahme ungültig");
  Val rec = Val::object();
  rec["stored"] = stored;
  rec["name"] = upload_field(v,"name");
  rec["mime"] = mime_for(upload_field(v,"name"));
  rec["size"] = (double)total;
  rec["by"] = upload_field(v,"by");
  rec["ts"] = (double)now_s();
  {
    std::lock_guard<std::recursive_mutex> lk(STATE_MTX);
    bool exists = false;
    for (const auto &x : FILES)
      if (x.find("stored") && x.find("stored")->asStr() == stored) {
        rec = x; exists = true; break;
      }
    if (!exists) {
      FILES.push_back(rec);
      try { save_json_array(FILES_JSON, FILES); }
      catch (...) { FILES.pop_back(); throw; }
      fresh = true;
    }
  }
  v["done"] = rec;
  if (!write_file_atomic(upload_meta(id), mj::dump(v)))
    throw std::runtime_error("Upload abgeschlossen; Bestätigung konnte nicht gesichert werden");
  return rec;
}
