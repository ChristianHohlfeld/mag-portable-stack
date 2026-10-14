// UDP hot-path for each isolated VM's existing Chromium session.
// Authenticated datagrams and bounded JPEG fragments; never exposes CDP.
// S23 uses H.264; here CDP screenshots are JPEG sent without transcoding.
#pragma once
#include <arpa/inet.h>
#include <openssl/hmac.h>
#include <poll.h>
#include <sys/socket.h>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

class BrowserUDP {
  int fd_ = -1;
  sockaddr_in server_{};
  std::string secret_, vm_;
  uint32_t frame_seq_ = 0, ctrl_seq_ = 0;
  std::chrono::steady_clock::time_point subscribed_{}, hello_{}, frame_{};
  static constexpr size_t HEADER = 13, TAG = 16, CHUNK = 1000;
  static void put16(std::string &s, uint16_t n) {
    s.push_back((char)(n >> 8)); s.push_back((char)n);
  }
  static void put32(std::string &s, uint32_t n) {
    for (int i = 3; i >= 0; --i) s.push_back((char)(n >> (8 * i)));
  }
  static uint16_t get16(const unsigned char *s) {
    return (uint16_t)(((unsigned)s[0] << 8) | s[1]);
  }
  static uint32_t get32(const unsigned char *s) {
    return ((uint32_t)s[0] << 24) | ((uint32_t)s[1] << 16) |
           ((uint32_t)s[2] << 8) | s[3];
  }
  bool verified(const unsigned char *s, size_t n) const {
    if (n < HEADER + TAG || n > 1400 || memcmp(s, "MGB1", 4)) return false;
    unsigned char mac[EVP_MAX_MD_SIZE];
    unsigned len = 0;
    if (!HMAC(EVP_sha256(), secret_.data(), (int)secret_.size(),
              s, n - TAG, mac, &len) || len < TAG) return false;
    // Constant time to avoid a timing oracle on the VM bridge.
    unsigned diff = 0;
    for (size_t i = 0; i < TAG; ++i) diff |= mac[i] ^ s[n - TAG + i];
    return diff == 0;
  }
  void send(uint8_t kind, uint32_t seq, uint16_t part,
            uint16_t total, const std::string &data) {
    if (fd_ < 0 || data.size() > CHUNK) return;
    std::string packet = "MGB1";
    packet.push_back((char)kind);
    put32(packet, seq); put16(packet, part); put16(packet, total);
    packet += data;
    unsigned char mac[EVP_MAX_MD_SIZE];
    unsigned len = 0;
    if (!HMAC(EVP_sha256(), secret_.data(), (int)secret_.size(),
              (const unsigned char *)packet.data(), packet.size(),
              mac, &len) || len < TAG) return;
    packet.append((const char *)mac, TAG);
    (void)sendto(fd_, packet.data(), packet.size(), MSG_DONTWAIT,
                 (sockaddr *)&server_, sizeof(server_));
  }
  static std::string str(const mj::Val &v, const char *key) {
    auto p = v.find(key);
    return p ? p->asStr() : "";
  }
public:
  BrowserUDP(const std::string &secret, const std::string &vm)
      : secret_(secret), vm_(vm) {
    fd_ = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd_ < 0) throw std::runtime_error("Browser UDP socket failed");
    sockaddr_in local{};
    local.sin_family = AF_INET;
    local.sin_port = htons(9227);
    local.sin_addr.s_addr = INADDR_ANY;
    if (bind(fd_, (sockaddr *)&local, sizeof(local))) {
      close(fd_); fd_ = -1;
      throw std::runtime_error("Browser UDP port 9227 occupied");
    }
    server_.sin_family = AF_INET;
    server_.sin_port = htons(18783);
    const auto host = mag::env("MAG_ROOM_BRIDGE_IP", "192.168.122.1");
    if (inet_pton(AF_INET, host.c_str(), &server_.sin_addr) != 1)
      throw std::runtime_error("Invalid MAG bridge IP");
  }
  ~BrowserUDP() { if (fd_ >= 0) close(fd_); }
  int fd() const { return fd_; }
  void receive(CDP &cdp) {
    unsigned char packet[1400];
    sockaddr_in src{};
    socklen_t sl = sizeof(src);
    ssize_t n = recvfrom(fd_, packet, sizeof(packet), MSG_DONTWAIT,
                         (sockaddr *)&src, &sl);
    if (n <= 0 || src.sin_addr.s_addr != server_.sin_addr.s_addr ||
        !verified(packet, (size_t)n) || packet[4] != 3 ||
        get16(packet + 9) != 0 || get16(packet + 11) != 1) return;
    const uint32_t seq = get32(packet + 5);
    if (seq <= ctrl_seq_) return; // replay protection per controller lifetime
    ctrl_seq_ = seq;
    std::string raw((const char *)packet + HEADER, (size_t)n - HEADER - TAG);
    mj::Val obj;
    if (!mj::parse(raw, obj) || !obj.isObj()) return;
    std::string kind = str(obj, "kind");
    if (kind == "subscribe") {
      subscribed_ = std::chrono::steady_clock::now();
      return;
    }
    if (kind == "pointer") {
      auto px = obj.find("x"), py = obj.find("y");
      if (!px || !py || px->type != mj::Val::NUM || py->type != mj::Val::NUM)
        return;
      double x = px->asNum(), y = py->asNum();
      if (!std::isfinite(x) || !std::isfinite(y) ||
          x < 0 || y < 0 || x >= 1440 || y >= 900) return;
      std::string action = str(obj, "action");
      std::string button = str(obj, "button");
      if (button.empty()) button = "left";
      if (button != "left" && button != "right" && button != "middle")
        return;
      auto sendMouse = [&](const char *type) {
        mj::Val p = mj::Val::object();
        p["type"] = type; p["x"] = x; p["y"] = y;
        p["button"] = button; p["clickCount"] = 1;
        cdp.call("Input.dispatchMouseEvent", p);
      };
      if (action == "tap") {
        sendMouse("mouseMoved"); sendMouse("mousePressed");
        sendMouse("mouseReleased");
      } else if (action == "move") sendMouse("mouseMoved");
      else if (action == "down") sendMouse("mousePressed");
      else if (action == "up") sendMouse("mouseReleased");
    } else if (kind == "wheel") {
      mj::Val p = mj::Val::object();
      p["type"] = "mouseWheel";
      p["x"] = 720; p["y"] = 450;
      p["deltaX"] = obj.find("dx") ? obj.find("dx")->asNum() : 0;
      p["deltaY"] = obj.find("dy") ? obj.find("dy")->asNum() : 0;
      cdp.call("Input.dispatchMouseEvent", p);
    } else if (kind == "key") {
      std::string key = str(obj, "key");
      if (key.empty() || key.size() > 40) return;
      mj::Val p = mj::Val::object();
      p["type"] = "keyDown"; p["key"] = key; p["code"] = key;
      cdp.call("Input.dispatchKeyEvent", p);
      p["type"] = "keyUp"; cdp.call("Input.dispatchKeyEvent", p);
    } else if (kind == "text") {
      std::string value = str(obj, "text");
      if (value.size() > 600) return;
      mj::Val p = mj::Val::object(); p["text"] = value;
      cdp.call("Input.insertText", p);
    }
  }
  void tick(CDP &cdp) {
    const auto now = std::chrono::steady_clock::now();
    if (now - hello_ >= std::chrono::seconds(1)) {
      hello_ = now;
      send(1, 0, 0, 1, vm_);
    }
    if (now - subscribed_ > std::chrono::seconds(8) ||
        now - frame_ < std::chrono::milliseconds(180)) return;
    frame_ = now;
    mj::Val p = mj::Val::object();
    p["format"] = "jpeg"; p["quality"] = 65;
    p["captureBeyondViewport"] = false;
    std::string jpg = unbase64(mag::str(cdp.call("Page.captureScreenshot", p), "data"));
    if (jpg.empty() || jpg.size() > CHUNK * 256) return;
    const size_t parts = (jpg.size() + CHUNK - 1) / CHUNK;
    const uint32_t seq = ++frame_seq_;
    for (size_t i = 0; i < parts; ++i)
      send(2, seq, (uint16_t)i, (uint16_t)parts,
           jpg.substr(i * CHUNK, CHUNK));
  }
};
