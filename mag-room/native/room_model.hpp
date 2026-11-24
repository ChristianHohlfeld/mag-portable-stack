#pragma once
#include <cstdlib>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

// Shared audit routing. The suffix selects the corresponding ID and label;
// each server has its own llama slot 0.
struct RoomModel {
  struct Backend { int port; std::string id, label; };
  std::string id, label;
  std::vector<Backend> backends;
  bool enabled() const { return !id.empty(); }
  bool knows(const std::string &model) const {
    for (const auto &b : backends) if (b.id == model) return true;
    return false;
  }
  static RoomModel from_env() {
    auto get = [](const std::string &key, const std::string &fallback) {
      const char *s = std::getenv(key.c_str()); return s && *s ? std::string(s) : fallback;
    };
    const std::regex id_pattern("[A-Za-z0-9._-]+"), entry_pattern("([0-9]{1,5}):([0-9]{1,5})");
    auto valid = [&](const std::string &id, const std::string &label) {
      return std::regex_match(id, id_pattern) &&
             label.size() <= 80 && label.find_first_of("\r\n\t") == std::string::npos;
    };
    RoomModel c;
    c.id = get("MAG_ROOM_MODEL_ID", "");
    if (!c.enabled()) return c;
    c.label = get("MAG_ROOM_MODEL_LABEL", c.id);
    if (!valid(c.id, c.label)) throw std::runtime_error("Invalid MAG_ROOM_MODEL_ID/LABEL");

    const std::string raw = get("MAG_ROOM_MODEL_PORTS", "8191:0,8192:1");
    std::istringstream entries(raw);
    std::set<int> ports, suffixes;
    std::string entry;
    std::smatch match;
    while (std::getline(entries, entry, ',')) {
      if (!std::regex_match(entry, match, entry_pattern))
        throw std::runtime_error("MAG_ROOM_MODEL_PORTS requires comma-separated port:index entries");
      int port = std::stoi(match[1]), index = std::stoi(match[2]);
      if (!port || port > 65535 || !ports.insert(port).second || !suffixes.insert(index).second)
        throw std::runtime_error("MAG_ROOM_MODEL_PORTS requires distinct ports and indices");
      const std::string suffix = std::to_string(index);
      Backend b{port, get("MAG_ROOM_MODEL_ID_" + suffix, c.id), get("MAG_ROOM_MODEL_LABEL_" + suffix, c.label)};
      if (!valid(b.id, b.label)) throw std::runtime_error("Invalid backend model ID/label");
      c.backends.push_back(std::move(b));
    }
    if (c.backends.empty() || raw.back() == ',') throw std::runtime_error("Incomplete MAG_ROOM_MODEL_PORTS");
    return c;
  }
};
