#pragma once
// MAG room observation masking: deterministic C++17, no additional model.
// Import AFTER json.hpp. This is only invoked at a deliberate budget rollover.
// The warm prompt is append-only between rollovers.
#include "context_artifacts.hpp"
#include <algorithm>
#include <string>
#include <vector>
namespace room_context {
inline size_t token_budget(const std::string &model) {
  if (model == "ornith" || model.find("deepseek") != std::string::npos) return 4096; // Noah NUMA1 CPU
  if (model == "ornith-numa0" || model.find("qwen") != std::string::npos) return 8192; // Lena MI50
  return 8192;
}
inline size_t budget_bytes(const std::string &model) {
  // Byte estimate is only a trigger, never an excuse to trim protected data.
  return token_budget(model) * 4;
}
inline bool is_protected(const mj::Val &m) {
  const std::string role = m.find("role") ? m.find("role")->asStr() : "";
  const std::string s = m.find("content") ? m.find("content")->asStr() : "";
  if (m.find("tool_calls") || role == "tool") return true;
  return s.find("}") != std::string::npos && s.find("{") != std::string::npos ||
      s.find("diff --git ") != std::string::npos ||
      s.find("+++ b/") != std::string::npos ||
      s.find("@@ -") != std::string::npos ||
      s.find("Traceback (most recent call last)") != std::string::npos ||
      s.find("error:") != std::string::npos ||
      s.find("Fehler:") != std::string::npos ||
      s.find("Exception") != std::string::npos ||
      s.find(">>>") != std::string::npos ||
      s.find("~~~") != std::string::npos ||
      s.find("\x60\x60\x60") != std::string::npos;
}
inline std::string reference_note(const std::string &ref, size_t n) {
  return "[Aeltere Beobachtungen archiviert: " + std::to_string(n) +
      " Nachrichten. SHA256=" + ref +
      ". Exaktes Original mit context_artifact(ref) nachladen. "
      "Code, Fehlermeldungen und Diffs niemals aus dieser Referenz rekonstruieren.]";
}
// Mask a contiguous prefix of OLD, complete conversation turns ONLY.
// All removed bytes, including tool calls/results, are archived verbatim.
// Never mask the most recent three user turns or anything within them.
// Fail-closed: return false when no safe grouping/storage is available.
inline bool mask_old(std::vector<mj::Val> &messages, const std::string &root,
                     size_t min_savings_bytes) {
  if (messages.size() < 7) return false;
  size_t protect = messages.size();
  size_t users = 0;
  for (size_t i = messages.size(); i-- > 2;) {
    const mj::Val &m = messages[i];
    if (!m.find("role") || m.find("role")->asStr() != "user") continue;
    const std::string content = m.find("content") ? m.find("content")->asStr() : "";
    if (content.rfind("[System]", 0) == 0 ||
        content.rfind("[Bisher ausgefuehrte Schritte", 0) == 0 ||
        content.rfind("[Aeltere Beobachtungen", 0) == 0) continue;
    protect = i;
    if (++users >= 3) break;
  }
  // With fewer than three real user turns, preserve all available recent
  // turns. Very long single-turn tool loops may still archive completed older
  // rounds while keeping the newest six messages untouched.
  if (!users && messages.size() > 12) protect = messages.size() - 6;
  if (protect <= 2) return false;
  // Preserve system and the first transcript (system, first user, then history).
  const size_t from = 2;
  size_t to = protect;
  // To prevent dangling tool results, rollovers may archive entire completed
  // pairs. The last item before the protected user must never be a tool call.
  // Do not orphan an assistant tool call or a following tool result.
  while (to > from && (messages[to-1].find("tool_calls") ||
         (to < messages.size() && messages[to].find("role") &&
          messages[to].find("role")->asStr() == "tool"))) --to;
  if (to <= from) return false;
  bool has_fresh_history = false;
  for (size_t i = from; i < to; ++i) {
    const std::string content = messages[i].find("content") ?
        messages[i].find("content")->asStr() : "";
    if (content.rfind("[Aeltere Beobachtungen", 0) != 0)
      has_fresh_history = true;
  }
  // Never create a chain of SHA references for the same old content.
  if (!has_fresh_history) return false;
  std::string archive;
  for (size_t i = from; i < to; ++i) {
    archive += mj::dump(messages[i]);
    archive += "\n";
  }
  if (archive.size() <= min_savings_bytes) return false;
  std::string ref = save(root, archive);
  if (ref.empty()) return false;
  mj::Val note = mj::Val::object();
  note["role"] = "user";
  note["content"] = reference_note(ref, to-from);
  messages.erase(messages.begin()+from, messages.begin()+to);
  messages.insert(messages.begin()+from, note);
  return true;
}
} // namespace room_context
