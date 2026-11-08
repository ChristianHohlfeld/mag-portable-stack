#pragma once
// Immutable content-addressed room observations. No inference and no in-memory model.
#include <openssl/sha.h>
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
namespace room_context {
inline std::string digest(const std::string &data) {
  unsigned char hash[SHA256_DIGEST_LENGTH];
  SHA256(reinterpret_cast<const unsigned char *>(data.data()), data.size(), hash);
  static const char digits[] = "0123456789abcdef";
  std::string result;
  for (unsigned char c : hash) { result += digits[c >> 4]; result += digits[c & 15]; }
  return result;
}
inline bool valid_ref(const std::string &ref) {
  if (ref.size() != 64) return false;
  for (char c : ref)
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  return true;
}
inline std::string artifact_path(const std::string &root, const std::string &ref) {
  return root + "/context-artifacts/" + ref.substr(0, 2) + "/" + ref + ".txt";
}
// Fail-closed: never mask an observation unless all its bytes are safely stored.
// A 32 MiB limit prevents unexpected disk exhaustion; larger payloads stay in wire.
inline std::string save(const std::string &root, const std::string &data) {
  if (data.empty() || data.size() > (size_t)32 * 1024 * 1024) return "";
  const std::string ref = digest(data), path = artifact_path(root, ref);
  std::error_code ec;
  std::filesystem::create_directories(std::filesystem::path(path).parent_path(), ec);
  if (ec) return "";
  if (std::filesystem::exists(path, ec)) {
    std::ifstream in(path, std::ios::binary);
    const std::string previous((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    return !in.bad() && previous == data ? ref : "";
  }
  const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  if (fd < 0) return "";
  size_t done = 0;
  while (done < data.size()) {
    ssize_t n = ::write(fd, data.data() + done, data.size() - done);
    if (n == -1 && errno == EINTR) continue;
    if (n <= 0) break;
    done += (size_t)n;
  }
  bool ok = done == data.size() && ::fsync(fd) == 0;
  if (::close(fd) != 0) ok = false;
  if (!ok) { ::unlink(path.c_str()); return ""; }
  return ref;
}
// Exact read, optionally by inclusive one-based lines. Zero means through EOF.
inline bool load(const std::string &root, const std::string &ref,
                 size_t first, size_t last, std::string &out, std::string &error) {
  if (!valid_ref(ref) || first < 1 || (last && last < first)) {
    error = "Ungueltige Artefakt-ID oder Zeilenbereich"; return false;
  }
  std::ifstream in(artifact_path(root, ref), std::ios::binary);
  if (!in) { error = "Artefakt nicht vorhanden"; return false; }
  const std::string data((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  if (in.bad() || digest(data) != ref) {
    error = "SHA-256-Abweichung oder Lesefehler"; return false;
  }
  if (first == 1 && last == 0) { out = data; return true; }
  size_t pos = 0, line = 1;
  for (; pos < data.size() && line < first; ++pos) if (data[pos] == '\n') ++line;
  if (line != first) { error = "Zeile existiert nicht"; return false; }
  const size_t from = pos;
  for (; pos < data.size(); ++pos) {
    if (data[pos] == '\n' && last && line++ >= last) { ++pos; break; }
  }
  out = data.substr(from, pos - from);
  return true;
}
} // namespace room_context
