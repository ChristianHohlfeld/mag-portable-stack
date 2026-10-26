#pragma once
#include <array>
#include <cstdlib>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>

// Shared room/gateway contract: port:NUMA, exactly one llama slot per node.
struct RoomModel {
  static constexpr int max_bots = 2;
  struct Backend { int port = 0, numa = -1; };
  std::string id, label;
  std::array<Backend, max_bots> backends{};
  bool enabled() const { return !id.empty(); }
  static RoomModel from_env() {
    auto get = [](const char *key, const std::string &fallback) {
      const char *s = std::getenv(key); return s && *s ? std::string(s) : fallback;
    };
    RoomModel c;
    c.id = get("MAG_ROOM_MODEL_ID", "");
    if (!c.enabled()) return c;
    c.label = get("MAG_ROOM_MODEL_LABEL", c.id);
    if (!std::regex_match(c.id, std::regex("[A-Za-z0-9._-]+")) ||
        c.label.size() > 80 || c.label.find_first_of("\r\n\t") != std::string::npos ||
        get("MAG_ROOM_MAX_BOTS", "2") != "2")
      throw std::runtime_error("Invalid MAG_ROOM_MODEL_ID/LABEL/MAX_BOTS (requires 2 bots)");
    const std::string raw = get("MAG_ROOM_MODEL_PORTS", "8191:0,8192:1");
    std::smatch m;
    if (!std::regex_match(raw, m, std::regex("([0-9]{1,5}):([01]),([0-9]{1,5}):([01])")))
      throw std::runtime_error("MAG_ROOM_MODEL_PORTS requires port:NUMA,port:NUMA");
    c.backends[0] = {std::stoi(m[1]), std::stoi(m[2])};
    c.backends[1] = {std::stoi(m[3]), std::stoi(m[4])};
    if (c.backends[0].port < 1 || c.backends[0].port > 65535 ||
        c.backends[1].port < 1 || c.backends[1].port > 65535 ||
        c.backends[0].port == c.backends[1].port || c.backends[0].numa == c.backends[1].numa)
      throw std::runtime_error("MAG_ROOM_MODEL_PORTS requires distinct ports and NUMA nodes");
    return c;
  }
};
