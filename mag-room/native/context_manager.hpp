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
// Keep this mapping independent of the GGUF alias and of the reviewer flag:
// slot 1 on MI50 is Lena; slot 2 on NUMA1 CPU is Noah.
inline size_t slot_budget_tokens(int slot) { return slot == 2 ? 4096 : 8192; }
inline size_t fresh_budget_tokens(int slot) { return slot == 2 ? 50 : 500; }
// Initial Room wire has the ENTIRE history inside messages[1]. Store it
// intact, then reinsert the real current human instruction independently.
// A single huge code instruction is preserved verbatim in the final user turn.
inline bool mask_initial(std::vector<mj::Val> &messages,
                         const std::string &root,
                         const std::string &current_user,
                         size_t min_savings_bytes = 512) {
  if (messages.size() != 2 || current_user.empty() ||
      messages[1].find("role") == nullptr ||
      messages[1].find("role")->asStr() != "user") return false;
  const std::string old = mj::dump(messages[1]) + "\n";
  if (old.size() <= min_savings_bytes) return false;
  const std::string ref = save(root, old);
  if (ref.empty()) return false;
  messages[1]["content"] = reference_note(ref, 1);
  mj::Val u=mj::Val::object();u["role"]="user";u["content"]=current_user;
  messages.push_back(u);
  return true;
}
// Mask whole completed old turns (including the formerly protected first user).
// No inference and no truncation; failed archival always retains original bytes.
// Never mask the latest user's instruction or an active assistant/tool exchange.
inline bool mask_old(std::vector<mj::Val> &messages,const std::string &root,
                     size_t min_savings_bytes) {
  if (messages.size()<3) return false;
  size_t protect=messages.size();
  for(size_t i=messages.size();i-->1;) {
    if(messages[i].find("role")&&messages[i].find("role")->asStr()=="user") {
      const std::string content=messages[i].find("content")?
          messages[i].find("content")->asStr():"";
      if(content.rfind("[Aeltere Beobachtungen",0)==0)continue;
      protect=i;break;
    }
  }
  // The last 1 user turn, plus every subsequent tool/result, is protected.
  // If no newer real instruction exists, do not risk losing current code.
  if(protect<=1||protect==messages.size())return false;
  const size_t from=1;
  size_t to=protect;
  while(to>from && (messages[to-1].find("tool_calls") ||
      (to<messages.size()&&messages[to].find("role")&&
       messages[to].find("role")->asStr()=="tool"))) --to;
  if(to<=from)return false;
  std::string archive;
  bool fresh=false;
  for(size_t i=from;i<to;++i) {
    const std::string content=messages[i].find("content")?
        messages[i].find("content")->asStr():"";
    if(content.rfind("[Aeltere Beobachtungen",0)!=0)fresh=true;
    archive+=mj::dump(messages[i])+"\n";
  }
  if(!fresh||archive.size()<=min_savings_bytes)return false;
  const std::string ref=save(root,archive);
  if(ref.empty())return false;
  mj::Val note=mj::Val::object();
  note["role"]="user";note["content"]=reference_note(ref,to-from);
  messages.erase(messages.begin()+from,messages.begin()+to);
  messages.insert(messages.begin()+from,note);
  return true;
}
} // namespace room_context
