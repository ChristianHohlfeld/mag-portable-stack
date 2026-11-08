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
  const std::string root="/tmp/mag-context-test-"+std::to_string((long)getpid());
  std::filesystem::create_directories(root);
  const std::string code="diff --git a/main.cpp b/main.cpp\n@@ -1 +1 @@\n-int a=1;\n+int a=2;\n";
  const auto ref=room_context::save(root,code);
  assert(room_context::valid_ref(ref));
  assert(!room_context::save(root,code).empty());
  std::string restored, error;
  assert(room_context::load(root,ref,1,0,restored,error));
  assert(restored==code);
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
  assert(msgs[1].find("content")->asStr()=="initial bootstrap");
  assert(msgs.back().find("content")->asStr()=="current task");
  assert(msgs[2].find("content")->asStr().find("context_artifact")!=std::string::npos);
  const std::string note=msgs[2].find("content")->asStr();
  const auto p=note.find("SHA256=");
  assert(p!=std::string::npos);
  std::string old_wire;
  assert(room_context::load(root,note.substr(p+7,64),1,0,old_wire,error));
  std::string expected;
  for(size_t i=2;i<6;++i)expected+=mj::dump(before[i])+"\n";
  assert(old_wire==expected); // tool/diff/stack/content/roles recovered byte-identically
  assert(!room_context::mask_old(msgs,root,50)); // no per-turn churn
  std::filesystem::remove_all(root);
  std::cout<<"context artifacts and masking OK\n";
}
