// Resumable, disk-backed room uploads. Each HTTP request is at most 8 MiB;
// the complete file never enters magroom's heap or the model prompt.
static std::mutex UPLOAD_MTX;
static constexpr size_t UPLOAD_CHUNK_MAX = 8 * 1024 * 1024;
static std::string upload_part(const std::string &id) {
  return FILES_DIR + "/.upload-" + id + ".part";
}
static std::string upload_info(const std::string &id) {
  return FILES_DIR + "/.upload-" + id + ".json";
}
static bool upload_id_ok(const std::string &id) {
  return id.size() == 32 &&
    std::all_of(id.begin(), id.end(), [](unsigned char c) {
      return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
    });
}
static std::string upload_safe_name(const std::string &name) {
  std::string safe;
  for (char c : basename_of(name)) {
    if (isalnum((unsigned char)c) || c == '.' || c == '_' || c == '-')
      safe.push_back(c);
    else safe.push_back('_');
  }
  if (safe.empty()) safe = "datei";
  if (safe.size() > 80) safe.resize(80);
  return safe;
}
static Val upload_meta_locked(const std::string &id, const std::string &owner) {
  if (!upload_id_ok(id) || owner.empty())
    throw std::runtime_error("Upload-Ticket ungueltig");
  Val v;
  if (!mj::parse(read_file(upload_info(id)), v) || !v.isObj() ||
      !v.find("owner") || v.find("owner")->asStr() != owner ||
      now_s() - (int64_t)(v.find("created") ? v.find("created")->asNum() : 0) > 86400)
    throw std::runtime_error("Upload-Ticket nicht mehr gueltig");
  return v;
}
static uint64_t upload_received_locked(const std::string &id, const Val &v) {
  struct stat st{};
  const std::string part = upload_part(id);
  const std::string final = FILES_DIR + "/" + id + "_" +
     upload_safe_name(v.find("name")->asStr());
  if (stat(part.c_str(), &st) == 0 && S_ISREG(st.st_mode))
    return (uint64_t)st.st_size;
  if (stat(final.c_str(), &st) == 0 && S_ISREG(st.st_mode))
    return (uint64_t)st.st_size;
  throw std::runtime_error("Upload-Datei nicht gefunden");
}
// Remove abandoned, server-issued temporary uploads older than 24 hours.
// Never touch committed chat files, only '.upload-' tickets and parts.
static void upload_cleanup_locked() {
  std::error_code ec;
  const auto now = std::filesystem::file_time_type::clock::now();
  for (const auto &entry : std::filesystem::directory_iterator(FILES_DIR, ec)) {
    if (ec) break;
    const std::string name = entry.path().filename().string();
    if (name.rfind(".upload-", 0) != 0 ||
        name.size() < 41 || !upload_id_ok(name.substr(8,32)))
      continue;
    if (entry.path().extension() != ".json" && entry.path().extension() != ".part")
      continue;
    std::error_code se;
    if (!entry.is_regular_file(se)) continue;
    const auto modified = entry.last_write_time(se);
    if (!se && now - modified > std::chrono::hours(24))
      std::filesystem::remove(entry.path(), se);
  }
}
static Val upload_begin(const std::string &name, uint64_t size,
                        const std::string &owner) {
  // No product-imposed total-file limit. Browser-safe 53-bit integers,
  // off_t and actual free disk space set the practical bounds.
  if (owner.empty() || name.empty() || name.size() > 255 || size == 0 ||
      size > (uint64_t)std::numeric_limits<int64_t>::max() ||
      size > 9007199254740991ULL)
    throw std::runtime_error("Ungueltige Dateigroesse oder Name");
  std::lock_guard<std::mutex> lk(UPLOAD_MTX);
  upload_cleanup_locked();
  auto space = std::filesystem::space(FILES_DIR);
  if (space.available < 64ULL * 1024 * 1024 ||
      size > space.available - 64ULL * 1024 * 1024)
    throw std::runtime_error("Nicht genug Speicherplatz fuer diese Datei");
  const std::string id = token_hex(16);
  const std::string part = upload_part(id);
  int fd = open(part.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (fd < 0) throw std::runtime_error("Upload-Datei konnte nicht erstellt werden");
  close(fd);
  Val v = Val::object();
  v["name"] = upload_safe_name(name);
  v["size"] = (double)size;
  v["owner"] = owner;
  v["created"] = (double)now_s();
  if (!write_file_atomic(upload_info(id), mj::dump(v))) {
    unlink(part.c_str());
    throw std::runtime_error("Upload-Ticket konnte nicht gespeichert werden");
  }
  Val out = Val::object();
  out["ticket"] = id;
  out["received"] = 0.0;
  out["chunk_size"] = (double)UPLOAD_CHUNK_MAX;
  return out;
}
static Val upload_status(const std::string &id, const std::string &owner) {
  std::lock_guard<std::mutex> lk(UPLOAD_MTX);
  Val v = upload_meta_locked(id, owner);
  Val out = Val::object();
  out["received"] = (double)upload_received_locked(id,v);
  out["size"] = v.find("size")->asNum();
  out["done"] = v.find("done") ? v.find("done")->asBool() : false;
  return out;
}
static Val upload_write_chunk(const std::string &id, const std::string &owner,
                              uint64_t offset, const std::string &data) {
  if (data.empty() || data.size() > UPLOAD_CHUNK_MAX)
    throw std::runtime_error("Upload-Teil ungueltig (maximal 8 MiB)");
  std::lock_guard<std::mutex> lk(UPLOAD_MTX);
  Val v = upload_meta_locked(id, owner);
  if (v.find("done") && v.find("done")->asBool())
    throw std::runtime_error("Upload bereits abgeschlossen");
  const uint64_t wanted = (uint64_t)v.find("size")->asNum();
  const std::string part = upload_part(id);
  int fd = open(part.c_str(), O_RDWR | O_NOFOLLOW | O_CLOEXEC);
  if (fd < 0) throw std::runtime_error("Upload-Datei nicht erreichbar");
  struct stat st{};
  if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size < 0) {
    close(fd);
    throw std::runtime_error("Upload-Datei ungueltig");
  }
  const uint64_t have = (uint64_t)st.st_size;
  if (offset > have || offset > wanted ||
      (uint64_t)data.size() > wanted - offset) {
    close(fd);
    throw std::runtime_error("Upload-Offset oder Dateigroesse falsch");
  }
  // A network interruption can leave either a complete chunk or a partially
  // committed prefix. Compare the overlap byte-for-byte, then safely append
  // only the missing suffix. A changed replay never overwrites existing data.
  const size_t overlap = offset < have
      ? (size_t)std::min<uint64_t>(data.size(), have - offset) : 0;
  size_t checked = 0;
  std::vector<char> compare(64 * 1024);
  while (checked < overlap) {
    size_t nbytes = std::min(overlap - checked, compare.size());
    ssize_t n = pread(fd, compare.data(), nbytes, (off_t)(offset + checked));
    if (n != (ssize_t)nbytes ||
        memcmp(compare.data(), data.data() + checked, nbytes) != 0) {
      close(fd);
      throw std::runtime_error("Upload-Wiederholung stimmt nicht ueberein");
    }
    checked += nbytes;
  }
  size_t pos = overlap;
  while (pos < data.size()) {
    ssize_t n = pwrite(fd, data.data() + pos, data.size() - pos, (off_t)(offset + pos));
    if (n <= 0) { close(fd); throw std::runtime_error("Upload-Datentraegerfehler"); }
    pos += (size_t)n;
  }
  if (pos > overlap && fsync(fd) != 0) {
    close(fd);
    throw std::runtime_error("Upload nicht gesichert");
  }
  close(fd);
  Val out = Val::object();
  out["received"] = (double)std::max<uint64_t>(have, offset + data.size());
  out["size"] = (double)wanted;
  return out;
}
static Val upload_finish(const std::string &id, const std::string &owner,
                         const std::string &by, bool &created) {
  created = false;
  std::lock_guard<std::mutex> lk(UPLOAD_MTX);
  Val v = upload_meta_locked(id, owner);
  uint64_t wanted = (uint64_t)v.find("size")->asNum();
  if (upload_received_locked(id,v) != wanted)
    throw std::runtime_error("Upload unvollstaendig");
  const std::string name = upload_safe_name(v.find("name")->asStr());
  const std::string stored = id + "_" + name;
  {
    std::lock_guard<std::recursive_mutex> st(STATE_MTX);
    for (const auto &f : FILES)
      if (f.find("stored") && f.find("stored")->asStr() == stored)
        return f;
    if (rename(upload_part(id).c_str(), (FILES_DIR + "/" + stored).c_str()) != 0) {
      struct stat info{};
      if (stat((FILES_DIR + "/" + stored).c_str(), &info) != 0 ||
          !S_ISREG(info.st_mode) || (uint64_t)info.st_size != wanted)
        throw std::runtime_error("Upload konnte nicht abgeschlossen werden");
    }
    Val rec = Val::object();
    rec["stored"] = stored;
    rec["name"] = name;
    rec["mime"] = mime_for(name);
    rec["size"] = (double)wanted;
    rec["by"] = by.substr(0,40);
    rec["ts"] = (double)now_s();
    FILES.push_back(rec);
    try { save_json_array(FILES_JSON, FILES); }
    catch (...) { FILES.pop_back(); throw; }
    v["done"] = true;
    write_file_atomic(upload_info(id), mj::dump(v));
    created = true;
    return rec;
  }
}

// Deleting a never-published upload drops its partial file and ticket.
// A committed room file is never silently removed here.
static void upload_cancel(const std::string &id, const std::string &owner) {
  std::lock_guard<std::mutex> lk(UPLOAD_MTX);
  Val v = upload_meta_locked(id, owner);
  const std::string stored = id + "_" + upload_safe_name(v.find("name")->asStr());
  {
    std::lock_guard<std::recursive_mutex> st(STATE_MTX);
    for (const auto &f : FILES)
      if (f.find("stored") && f.find("stored")->asStr() == stored)
        throw std::runtime_error("Datei bereits im Chat; Chat-Anhang separat entfernen");
  }
  unlink(upload_part(id).c_str());
  unlink(upload_info(id).c_str());
}
