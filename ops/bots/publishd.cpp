// Native publisher for one bot site. Listens on the libvirt gateway only.
// A guest POSTs a ustar/pax archive of /home/bot/www. The wildcard certificate
// and DNS record are never touched.
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {
constexpr size_t kMaxBody = 8 * 1024 * 1024;
constexpr size_t kMaxFile = 2 * 1024 * 1024;
constexpr size_t kMaxFiles = 200;
constexpr size_t kMaxHeaders = 64 * 1024;

std::string env_or(const char *key, const char *fallback) {
  const char *v = std::getenv(key);
  return (v && *v) ? v : fallback;
}

bool agent_id(const std::string &id) {
  if (id.size() < 6 || id.compare(0, 5, "agent") != 0) return false;
  for (size_t i = 5; i < id.size(); ++i)
    if (id[i] < '0' || id[i] > '9') return false;
  return true;
}

bool safe_name(const std::string &name) {
  if (name.empty() || name.size() > 181) return false;
  if (!std::isalnum(static_cast<unsigned char>(name[0]))) return false;
  for (unsigned char c : name) {
    if (!(std::isalnum(c) || c == '.' || c == '_' || c == '/' || c == '-'))
      return false;
  }
  size_t start = 0;
  while (start <= name.size()) {
    size_t slash = name.find('/', start);
    std::string part = name.substr(start, slash == std::string::npos
                                               ? std::string::npos
                                               : slash - start);
    if (part.empty() || part == "." || part == "..") return false;
    if (slash == std::string::npos) break;
    start = slash + 1;
  }
  return true;
}

bool const_eq(const std::string &a, const std::string &b) {
  unsigned diff = static_cast<unsigned>(a.size() ^ b.size());
  size_t n = a.size() > b.size() ? a.size() : b.size();
  for (size_t i = 0; i < n; ++i) {
    unsigned char ca = i < a.size() ? static_cast<unsigned char>(a[i]) : 0;
    unsigned char cb = i < b.size() ? static_cast<unsigned char>(b[i]) : 0;
    diff |= static_cast<unsigned>(ca ^ cb);
  }
  return diff == 0;
}

bool peer_ok(const sockaddr_in &in) {
  char buf[INET_ADDRSTRLEN];
  if (!inet_ntop(AF_INET, &in.sin_addr, buf, sizeof buf)) return false;
  if (const char *testing = std::getenv("MAG_BOTS_TESTING")) {
    if (::strcmp(testing, "1") == 0) return ::strcmp(buf, "127.0.0.1") == 0;
  }
  unsigned a = 0, b = 0, c = 0, d = 0;
  if (std::sscanf(buf, "%u.%u.%u.%u", &a, &b, &c, &d) != 4) return false;
  return a == 192 && b == 168 && c == 122 && d < 256;
}

std::string token_for(const fs::path &root, const std::string &id) {
  fs::path path = root / "tokens" / id;
  std::error_code ec;
  if (fs::is_symlink(path, ec) || !fs::is_regular_file(path, ec)) return "";
  if (fs::file_size(path, ec) > 128 || ec) return "";
  std::ifstream in(path, std::ios::binary);
  std::string raw((std::istreambuf_iterator<char>(in)), {});
  while (!raw.empty() &&
         (raw.back() == '\n' || raw.back() == '\r' || raw.back() == ' ' ||
          raw.back() == '\t'))
    raw.pop_back();
  size_t lead = 0;
  while (lead < raw.size() &&
         (raw[lead] == ' ' || raw[lead] == '\t' || raw[lead] == '\n'))
    ++lead;
  return raw.substr(lead);
}

uint64_t parse_octal(const char *s, size_t n) {
  size_t i = 0;
  while (i < n && (s[i] == ' ' || s[i] == '\0')) ++i;
  if (i >= n || s[i] < '0' || s[i] > '7')
    throw std::runtime_error("bad tar header");
  uint64_t v = 0;
  for (; i < n && s[i] >= '0' && s[i] <= '7'; ++i) {
    v = (v << 3) + static_cast<unsigned>(s[i] - '0');
  }
  return v;
}

std::string field(const char *s, size_t n) {
  size_t i = 0;
  while (i < n && s[i] != '\0') ++i;
  return std::string(s, i);
}

bool zero_block(const char *s) {
  for (size_t i = 0; i < 512; ++i)
    if (s[i] != '\0') return false;
  return true;
}

void check_sum(const char *hdr) {
  unsigned got = 0;
  for (size_t i = 0; i < 512; ++i)
    got += (i >= 148 && i < 156) ? static_cast<unsigned char>(' ')
                                 : static_cast<unsigned char>(hdr[i]);
  if (got != parse_octal(hdr + 148, 8))
    throw std::runtime_error("bad tar checksum");
}

std::string pax_path(const std::string &data) {
  std::string path;
  size_t i = 0;
  while (i < data.size()) {
    if (data[i] == '\0') break;
    size_t j = i;
    size_t len = 0;
    while (j < data.size() && std::isdigit(static_cast<unsigned char>(data[j]))) {
      len = len * 10 + static_cast<unsigned>(data[j] - '0');
      ++j;
    }
    if (j == i || j >= data.size() || data[j] != ' ' || len < (j - i) + 1 ||
        i + len > data.size())
      throw std::runtime_error("bad pax header");
    std::string rec = data.substr(j + 1, i + len - (j + 1));
    if (!rec.empty() && rec.back() == '\n') rec.pop_back();
    if (rec.compare(0, 5, "path=") == 0) path = rec.substr(5);
    i += len;
  }
  return path;
}

struct Member {
  std::string name;
  std::string data;
};

std::vector<Member> members_of(const std::string &payload) {
  std::vector<Member> found;
  std::string pending;
  bool saw_zero = false;
  size_t off = 0;
  while (off + 512 <= payload.size()) {
    const char *hdr = payload.data() + off;
    if (zero_block(hdr)) {
      saw_zero = true;
      break;
    }
    if (::memcmp(hdr + 257, "ustar", 5) != 0)
      throw std::runtime_error("not a ustar archive");
    check_sum(hdr);
    uint64_t size = parse_octal(hdr + 124, 12);
    if (size > kMaxBody) throw std::runtime_error("file too large");
    uint64_t blocks = (size + 511) / 512;
    uint64_t span = 512 + blocks * 512;
    if (span > payload.size() - off) throw std::runtime_error("short archive");
    std::string data = payload.substr(off + 512, static_cast<size_t>(size));
    off += static_cast<size_t>(span);
    char type = hdr[156] == '\0' ? '0' : hdr[156];
    if (type == '5') continue;
    if (type == 'g') continue;
    if (type == 'x' || type == 'X') {
      std::string path = pax_path(data);
      if (!path.empty()) pending = path;
      continue;
    }
    if (type == 'L') {
      while (!data.empty() && data.back() == '\0') data.pop_back();
      pending = data;
      continue;
    }
    if (type == 'K') continue;
    if (type != '0') throw std::runtime_error("unsupported archive member");
    std::string name = pending;
    pending.clear();
    if (name.empty()) {
      name = field(hdr, 100);
      std::string prefix = field(hdr + 345, 155);
      if (!prefix.empty()) name = prefix + "/" + name;
    }
    if (name.compare(0, 2, "./") == 0) name = name.substr(2);
    if (!safe_name(name)) throw std::runtime_error("unsafe path");
    if (data.size() > kMaxFile) throw std::runtime_error("file too large");
    found.push_back(Member{name, data});
    if (found.size() > kMaxFiles) throw std::runtime_error("too many files");
  }
  if (!saw_zero) throw std::runtime_error("not a ustar archive");
  bool index = false;
  for (const auto &m : found)
    if (m.name == "index.html") index = true;
  if (!index) throw std::runtime_error("index.html required");
  return found;
}

void write_file(const fs::path &dest, const std::string &data) {
  fs::create_directories(dest.parent_path());
  std::string tmp = dest.string() + ".part";
  int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0644);
  if (fd < 0) throw std::runtime_error("unsafe path");
  size_t done = 0;
  while (done < data.size()) {
    ssize_t n = ::write(fd, data.data() + done, data.size() - done);
    if (n < 0) {
      ::close(fd);
      ::unlink(tmp.c_str());
      throw std::runtime_error("short write");
    }
    done += static_cast<size_t>(n);
  }
  if (::close(fd) != 0) {
    ::unlink(tmp.c_str());
    throw std::runtime_error("short write");
  }
  if (::rename(tmp.c_str(), dest.c_str()) != 0) {
    ::unlink(tmp.c_str());
    throw std::runtime_error("unsafe path");
  }
  ::chmod(dest.c_str(), 0644);
}

void install_site(const fs::path &root, const std::string &id,
                  const std::vector<Member> &files) {
  fs::path www = root / "www";
  std::error_code ec;
  fs::create_directories(www, ec);
  if (ec || fs::is_symlink(www)) throw std::runtime_error("unsafe www");
  fs::path final = www / id;
  fs::path staging = www / (".staging-" + id);
  fs::path backup = www / (".old-" + id);
  if (fs::is_symlink(final) || fs::is_symlink(staging) || fs::is_symlink(backup))
    throw std::runtime_error("refusing symlink");
  fs::remove_all(staging, ec);
  fs::create_directory(staging);
  try {
    fs::path staging_abs = fs::absolute(staging).lexically_normal();
    for (const auto &member : files) {
      fs::path dest = (staging / member.name).lexically_normal();
      fs::path rel = dest.lexically_relative(staging_abs);
      if (rel.empty() || rel.native().compare(0, 2, "..") == 0)
        throw std::runtime_error("path escaped staging");
      write_file(dest, member.data);
    }
    fs::remove_all(backup, ec);
    if (fs::exists(final)) fs::rename(final, backup);
    fs::rename(staging, final);
    fs::remove_all(backup, ec);
  } catch (...) {
    fs::remove_all(staging, ec);
    throw;
  }
}

bool write_all(int fd, const std::string &data) {
  size_t done = 0;
  while (done < data.size()) {
    ssize_t n = ::write(fd, data.data() + done, data.size() - done);
    if (n < 0) return false;
    done += static_cast<size_t>(n);
  }
  return true;
}

bool read_some(int fd, std::string &buf, size_t cap) {
  if (buf.size() >= cap) return false;
  pollfd p{};
  p.fd = fd;
  p.events = POLLIN;
  int pr = ::poll(&p, 1, 30000);
  if (pr <= 0) return false;
  char tmp[8192];
  size_t room = cap - buf.size();
  if (room > sizeof tmp) room = sizeof tmp;
  ssize_t n = ::read(fd, tmp, room);
  if (n <= 0) return false;
  buf.append(tmp, static_cast<size_t>(n));
  return true;
}

void send_status(int fd, int code, const std::string &body) {
  const char *reason = "OK";
  if (code == 204) reason = "No Content";
  else if (code == 400) reason = "Bad Request";
  else if (code == 403) reason = "Forbidden";
  else if (code == 404) reason = "Not Found";
  else if (code == 411) reason = "Length Required";
  else if (code == 413) reason = "Payload Too Large";
  std::ostringstream out;
  out << "HTTP/1.1 " << code << ' ' << reason
      << "\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: "
      << body.size() << "\r\nConnection: close\r\n\r\n" << body;
  write_all(fd, out.str());
}

std::map<std::string, std::string> header_map(const std::string &block) {
  std::map<std::string, std::string> headers;
  size_t line = block.find("\r\n");
  size_t pos = (line == std::string::npos) ? block.size() : line + 2;
  while (pos < block.size()) {
    size_t end = block.find("\r\n", pos);
    if (end == std::string::npos || end == pos) break;
    std::string row = block.substr(pos, end - pos);
    pos = end + 2;
    size_t colon = row.find(':');
    if (colon == std::string::npos) continue;
    std::string key;
    key.reserve(colon);
    for (size_t i = 0; i < colon; ++i)
      key.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(row[i]))));
    size_t v = colon + 1;
    while (v < row.size() && row[v] == ' ') ++v;
    headers[key] = row.substr(v);
  }
  return headers;
}

void handle(int fd, const sockaddr_in &peer, const fs::path &root) {
  if (!peer_ok(peer)) {
    send_status(fd, 403, "forbidden\n");
    return;
  }
  std::string buf;
  while (buf.find("\r\n\r\n") == std::string::npos) {
    if (!read_some(fd, buf, kMaxHeaders)) {
      send_status(fd, 400, "bad request\n");
      return;
    }
  }
  size_t split = buf.find("\r\n\r\n");
  std::string head = buf.substr(0, split);
  std::string extra = buf.substr(split + 4);
  size_t eol = head.find("\r\n");
  std::string req = eol == std::string::npos ? head : head.substr(0, eol);
  std::istringstream rs(req);
  std::string method, target, version;
  rs >> method >> target >> version;
  std::string path = target;
  size_t q = path.find('?');
  if (q != std::string::npos) path = path.substr(0, q);
  if (method != "POST" || path != "/publish") {
    send_status(fd, 404, "not found\n");
    return;
  }
  auto headers = header_map(head);
  std::string bot = headers.count("x-mag-bot") ? headers["x-mag-bot"] : "";
  if (!agent_id(bot)) {
    send_status(fd, 400, "bad bot\n");
    return;
  }
  std::string auth = headers.count("authorization") ? headers["authorization"] : "";
  const std::string prefix = "Bearer ";
  if (auth.compare(0, prefix.size(), prefix) != 0) {
    send_status(fd, 403, "forbidden\n");
    return;
  }
  std::string presented = auth.substr(prefix.size());
  while (!presented.empty() && presented.back() == ' ') presented.pop_back();
  std::string expected = token_for(root, bot);
  if (expected.empty() || !const_eq(presented, expected)) {
    send_status(fd, 403, "forbidden\n");
    return;
  }
  auto len_hdr = headers.find("content-length");
  if (len_hdr == headers.end()) {
    send_status(fd, 411, "length required\n");
    return;
  }
  char *end = nullptr;
  errno = 0;
  unsigned long length = std::strtoul(len_hdr->second.c_str(), &end, 10);
  if (end == len_hdr->second.c_str() || *end != '\0' || errno == ERANGE) {
    send_status(fd, 411, "length required\n");
    return;
  }
  if (length > kMaxBody) {
    send_status(fd, 413, "too large\n");
    return;
  }
  while (extra.size() < length) {
    if (!read_some(fd, extra, length)) {
      send_status(fd, 400, "short body\n");
      return;
    }
  }
  extra.resize(length);
  try {
    install_site(root, bot, members_of(extra));
  } catch (const std::exception &exc) {
    send_status(fd, 400, std::string(exc.what()) + "\n");
    return;
  }
  send_status(fd, 204, "");
}
}  // namespace

int main() {
  ::signal(SIGPIPE, SIG_IGN);
  umask(022);
  fs::path root = env_or("MAG_BOTS_ROOT", "/var/lib/mag-bots");
  std::string bind_addr = env_or("MAG_BOTS_PUBLISH_BIND", "192.168.122.1");
  int port = std::atoi(env_or("MAG_BOTS_PUBLISH_PORT", "8091").c_str());
  if (port < 1 || port > 65535) {
    std::cerr << "bad publish port\n";
    return 1;
  }
  int srv = ::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (srv < 0) return 1;
  int on = 1;
  ::setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &on, sizeof on);
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<uint16_t>(port));
  if (inet_pton(AF_INET, bind_addr.c_str(), &addr.sin_addr) != 1) {
    std::cerr << "bad publish bind\n";
    return 1;
  }
  if (::bind(srv, reinterpret_cast<sockaddr *>(&addr), sizeof addr) != 0 ||
      ::listen(srv, 16) != 0) {
    std::cerr << "publish bind failed\n";
    return 1;
  }
  for (;;) {
    sockaddr_in peer{};
    socklen_t len = sizeof peer;
    int fd = ::accept4(srv, reinterpret_cast<sockaddr *>(&peer), &len, SOCK_CLOEXEC);
    if (fd < 0) continue;
    handle(fd, peer, root);
    ::close(fd);
  }
}
