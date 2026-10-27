#pragma once
#include <algorithm>
#include <cctype>
#include <string>
#include <utility>
#include <vector>
// Deterministic, CPU-free speaker selection shared with the unit test.
namespace group_chat {
using Bot = std::pair<std::string, std::string>; // id, display name
inline std::string lower(std::string s) {
  for (char &c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return s;
}
inline std::vector<std::string> mentions(const std::string &text,
                                          const std::vector<Bot> &roster,
                                          const std::string &exclude = "") {
  std::vector<std::string> found;
  const std::string low = lower(text);
  for (size_t at = 0; at < low.size(); ++at) {
    if (low[at] != '@') continue;
    size_t longest = 0;
    std::string id;
    for (const auto &bot : roster) {
      if (bot.first.empty() || bot.first == exclude || bot.second.empty()) continue;
      const std::string name = lower(bot.second);
      const size_t end = at + 1 + name.size();
      if (end > low.size() || low.compare(at + 1, name.size(), name) != 0) continue;
      if (end < low.size()) {
        const unsigned char c = static_cast<unsigned char>(low[end]);
        if (std::isalnum(c) || c == '_' || c == '-') continue;
      }
      if (name.size() > longest) { longest = name.size(); id = bot.first; }
    }
    if (longest) {
      if (std::find(found.begin(), found.end(), id) == found.end()) found.push_back(id);
      at += longest;
    }
  }
  return found;
}
inline std::vector<std::string> select(const std::string &text,
                                        const std::vector<Bot> &roster,
                                        const std::vector<std::string> &preferred,
                                        bool &mandatory) {
  auto addressed = mentions(text, roster);
  mandatory = !addressed.empty();
  if (mandatory) return addressed;
  for (const auto &id : preferred)
    for (const auto &bot : roster)
      if (bot.first == id) return {id};
  return roster.empty() ? std::vector<std::string>{}
                        : std::vector<std::string>{roster.front().first};
}
inline bool can_reply_to_bot(bool mentioned, int count) { return mentioned && count < 3; }
inline bool is_pass(const std::string &text) {
  const auto a = text.find_first_not_of(" \r\n\t");
  if (a == std::string::npos) return false;
  const auto b = text.find_last_not_of(" \r\n\t");
  return text.substr(a, b - a + 1) == "PASS";
}
inline bool needs_user_tail(const std::string &role) { return role != "user"; }
}
