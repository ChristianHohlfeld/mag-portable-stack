#include "mag_deadline.hpp"
#include "../native/json.hpp"
#include "../native/context_manager.hpp"
#include <cassert>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>
#include <unistd.h>
static mj::Val msg(const std::string &role, const std::string &s) {
  mj::Val x=mj::Val::object();x["role"]=role;x["content"]=s;return x;
}
int main() {
  mag_test_deadline(60); // mag-test-timeout: 60
  assert(room_context::token_budget("deepseek-v4-flash-0731") == 4096);
  assert(room_context::token_budget("qwen3.8-27b") == 8192);
  assert(room_context::token_budget("ornith") == 4096);
  assert(room_context::token_budget("ornith-numa0") == 8192);
  // Alias is NOT a model family: role/slot is the authority.
  assert(room_context::slot_budget_tokens(2)==4096); // ornith -> Noah
  assert(room_context::slot_budget_tokens(1)==8192); // ornith-numa0 -> Lena
  assert(room_context::fresh_budget_tokens(1)==500);
  assert(room_context::fresh_budget_tokens(2)==50);
  const std::string root="/tmp/mag-context-test-"+std::to_string((long)getpid());
  std::filesystem::create_directories(root);
  const std::string code="diff --git a/main.cpp b/main.cpp\n@@ -1 +1 @@\n-int a=1;\n+int a=2;\n";
  const auto ref=room_context::save(root,code);
  assert(room_context::valid_ref(ref));
  assert(!room_context::save(root,code).empty());
  std::string restored, error;
  assert(room_context::load(root,ref,1,0,restored,error));
  assert(restored==code);
  // A single archived JSONL record can exceed the normal 24 KB tool return.
  // The whole text must remain addressable without cutting or re-encoding.
  const std::string large(27000, 'x');
  const auto large_ref = room_context::save(root,large);
  assert(room_context::valid_ref(large_ref));
  std::string part;
  assert(room_context::load_bytes(root,large_ref,0,12000,part,error));
  assert(part==large.substr(0,12000));
  assert(room_context::load_bytes(root,large_ref,12000,24000,part,error));
  assert(part==large.substr(12000,12000));
  assert(room_context::load_bytes(root,large_ref,24000,27000,part,error));
  assert(part==large.substr(24000));
  assert(!room_context::load_bytes(root,large_ref,12000,999999,part,error));
  const std::string utf8 = std::string("A") + "\xC3\xA4" + "B";
  const auto utf8_ref=room_context::save(root,utf8);
  assert(!room_context::load_bytes(root,utf8_ref,2,4,part,error));

  assert(!room_context::load(root,"../bad",1,0,restored,error));
  std::vector<mj::Val> msgs={
    msg("system","immutable system"),msg("user","initial bootstrap"),
    msg("user","old task"),msg("assistant","making changes"),msg("tool",code),
    msg("assistant","old done"),msg("user","latest-2"),msg("assistant","reply"),
    msg("user","latest-1"),msg("assistant","reply"),
    msg("user","current task")
  };
  const auto before=msgs;
  assert(room_context::mask_old(msgs,root,50));
  assert(msgs[0].find("content")->asStr()=="immutable system");
  assert(msgs.size()==3); // system + immutable archive ref + current user
  assert(msgs[1].find("content")->asStr().find("context_artifact")!=std::string::npos);
  assert(msgs[2].find("content")->asStr()=="current task");
  const std::string note=msgs[1].find("content")->asStr();
  const auto p=note.find("SHA256=");
  assert(p!=std::string::npos);
  std::string old_wire;
  assert(room_context::load(root,note.substr(p+7,64),1,0,old_wire,error));
  std::string expected;
  for(size_t i=1;i<before.size()-1;++i)expected+=mj::dump(before[i])+"\\n";
  assert(old_wire==expected); // 9 original messages incl code/diff/tool bytes
  assert(!room_context::mask_old(msgs,root,50)); // no archive-chaining
  // PR #339 failed on the initial giant transcript: it was messages[1].
  std::vector<mj::Val> initial={msg("system","system byte-stable"),
      msg("user",std::string(40000,'A')+"\\n"+code)};
  const std::string initial_old=mj::dump(initial[1])+"\\n";
  assert(room_context::mask_initial(initial,root,"[Chris]: Behalte alle neuen Dateien",512));
  assert(initial.size()==3);
  assert(initial[0].find("content")->asStr()=="system byte-stable");
  assert(initial[2].find("content")->asStr()=="[Chris]: Behalte alle neuen Dateien");
  const std::string initial_note=initial[1].find("content")->asStr();
  const auto anchor=initial_note.find("SHA256=");
  assert(anchor!=std::string::npos);
  assert(room_context::load(root,initial_note.substr(anchor+7,64),1,0,restored,error));
  assert(restored==initial_old);
  std::filesystem::remove_all(root);
  std::cout<<"context artifacts and masking OK\n";
}
