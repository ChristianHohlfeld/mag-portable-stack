#include "../native/group_dispatcher.hpp"
#include <cassert>
#include <vector>
int main() {
  using namespace group_chat;
  const std::vector<Bot> roster={{"q","Qwen"},{"d","DeepSeek"},{"o","Ornith 1"},{"p","Ornith 2"}};
  bool mandatory=false;
  auto r=select("hi",roster,{"d","q"},mandatory);
  assert(!mandatory && r==std::vector<std::string>{"d"});
  r=select("@Ornith 2 bitte @Qwen",roster,{"d"},mandatory);
  assert(mandatory && r==std::vector<std::string>({"p","q"}));
  r=select("Ich will eine zweite Meinung",roster,{"d"},mandatory);
  assert(!mandatory && r==std::vector<std::string>({"d","q"}));
  assert(mentions("@Ornith 2x",roster).empty());
  assert(mentions("@Ornith 2 @Ornith 2",roster).size()==1);
  assert(mentions("ich stimme zu",roster,"q").empty());
  assert(mentions("@Qwen",roster,"q").empty());
  assert(can_reply_to_bot(true,0) && can_reply_to_bot(true,2));
  assert(!can_reply_to_bot(true,3) && !can_reply_to_bot(false,0));
  int count=3; assert(!can_reply_to_bot(true,count));
  count=0; assert(can_reply_to_bot(true,count)); // human preemption resets cap
  assert(is_pass("  PASS\n") && !is_pass("PASS bitte"));
  assert(needs_user_tail("assistant") && needs_user_tail("tool"));
  assert(!needs_user_tail("user"));
}
