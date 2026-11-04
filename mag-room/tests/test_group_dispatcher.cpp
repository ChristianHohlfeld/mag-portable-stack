#include "mag_deadline.hpp"
#include "../native/group_dispatcher.hpp"
#include <cstdio>
#include <cstdlib>
using namespace group_chat;
static int fails = 0;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #x); ++fails; } } while (0)
int main() {
  mag_test_deadline(60); // mag-test-timeout: 60
  const std::vector<Bot> r = {{"qwen-numa0", "Qwen", false}, {"qwen-numa1", "Flint", true}};
  using V = std::vector<std::string>;
  CHECK(addressed("Flint, schreib mir ein Skript", r) == V{"qwen-numa1"});
  CHECK(addressed("@qwen was meinst du?", r) == V{"qwen-numa0"});
  CHECK(addressed("Hi beide", r) == (V{"qwen-numa0", "qwen-numa1"}));
  CHECK(addressed("Hi alle", r) == (V{"qwen-numa0", "qwen-numa1"}));
  CHECK(addressed("Was meint ihr?", r).size() == 2);
  CHECK(addressed("lösch alle Dateien im Ordner", r).empty());
  CHECK(addressed("Wie spät ist es?", r).empty());
  CHECK(addressed("Qwenfoo", r).empty());
  // Shared alias: "Qwen3" vs "Qwen3-Coder-Next"
  const std::vector<Bot> q = {{"a", "Qwen3", false}, {"b", "Qwen3-Coder-Next", true}};
  CHECK(addressed("Qwen3-Coder-Next bitte", q) == V{"b"});
  CHECK(addressed("Qwen3 bitte", q) == V{"a"});
  CHECK(pick_speaker("Schreib ein Python-Skript", r) == "qwen-numa1");
  CHECK(pick_speaker("Was ist die Hauptstadt von Peru?", r) == "qwen-numa0");
  CHECK(is_pass("PASS"));
  CHECK(is_pass("  pass.\n"));
  CHECK(is_pass("**PASS**"));
  CHECK(leading_pass("PASS\n\n- User: [Chris]: Hallo?"));
  CHECK(!leading_pass("PASS"));
  CHECK(maybe_pass("PASS\n\n- User: x"));
  CHECK(!leading_pass("Passwort ist falsch\nmehr"));
  CHECK(!leading_pass("Hier ist der Code:\nPASS"));
  CHECK(is_pass("[Flint] PASS"));
  CHECK(is_pass("**[PASS]**"));
  CHECK(is_pass("<PASS>"));
  CHECK(!is_pass("Passend dazu: X"));
  CHECK(maybe_name_only("No", {"Lena", "Noah"}));
  CHECK(maybe_name_only("[Noah", {"Lena", "Noah"}));
  CHECK(maybe_name_only("Noah (qwen-nu", {"Lena", "Noah"}));
  CHECK(!maybe_name_only("Noah hat", {"Lena", "Noah"}));
  CHECK(is_pass("[PASS]"));
  CHECK(is_pass(" [pass]\n"));
  CHECK(is_pass("[Lena] [PASS]"));
  CHECK(name_only("Noah", {"Noah", "Lena"}));
  CHECK(name_only("[Noah]:", {"Noah", "Lena"}));
  CHECK(name_only("**Lena**", {"Noah", "Lena"}));
  CHECK(!name_only("Noah, schau mal hier", {"Noah", "Lena"}));
  CHECK(!name_only("", {"Noah"}));
  CHECK(norm_reply("Hallo,  Welt!") == norm_reply("hallo welt"));
  CHECK(!is_pass("Passt so, aber ergänze X"));
  CHECK(maybe_pass("PA"));
  CHECK(maybe_pass("[Fli"));
  CHECK(!maybe_pass("Passt"));
  CHECK(!maybe_pass("Ergänzung: "));
  // Real bot dialogue: repeated alternating rounds and direct @mentions.
  CHECK(kMaxFollowups == 0);
  CHECK(next_followup(r, {}, "qwen-numa0", 500) == "qwen-numa1"); // unlimited
  CHECK(next_followup(r, {}, "qwen-numa1", 500, 0) == "qwen-numa0");
  CHECK(!conversation_rests(0, 2));
  CHECK(!conversation_rests(1, 2));
  CHECK(conversation_rests(2, 2));
  CHECK(conversation_rests(0, 1));
  CHECK(next_followup(r, {"qwen-numa0"}, "qwen-numa0", 0) == "qwen-numa1");
  CHECK(next_followup(r, {"qwen-numa0", "qwen-numa1"}, "qwen-numa1", 1) == "qwen-numa0");
  CHECK(next_followup(r, {"qwen-numa0"}, "qwen-numa0", 2, 6, "@Flint bitte prüfen") == "qwen-numa1");
  CHECK(next_followup(r, {}, "qwen-numa1", 3, 6, "@Qwen widersprich") == "qwen-numa0");
  CHECK(next_followup(r, {}, "qwen-numa0", 4, 6, "@Qwen bitte") == "qwen-numa1");
  CHECK(next_followup(r, {}, "qwen-numa1", 6, 6, "@Qwen weiter").empty());
  CHECK(next_followup(r, {}, "qwen-numa0", 0, 0, "@Flint weiter") == "qwen-numa1"); // 0 = unlimited
  CHECK(next_followup({{"solo","Solo",false}}, {}, "solo", 0).empty());
  // Staggered group answers: named order, code -> coder, otherwise alternate.
  const std::vector<Bot> ln = {{"lena", "Lena", false}, {"noah", "Noah", false}};
  const V both{"lena", "noah"};
  CHECK(speaking_order("Noah und Lena, was meint ihr?", both, ln, "") == (V{"noah", "lena"}));
  CHECK(speaking_order("Lena und Noah?", both, ln, "lena") == (V{"lena", "noah"}));
  CHECK(speaking_order("Hi beide", both, ln, "lena") == (V{"noah", "lena"}));
  CHECK(speaking_order("Hi beide", both, ln, "noah") == (V{"lena", "noah"}));
  CHECK(speaking_order("Wie spät ist es?", both, ln, "") == (V{"lena", "noah"}));
  CHECK(speaking_order("schreib ein python skript", V{"qwen-numa0", "qwen-numa1"}, r, "qwen-numa1") ==
        (V{"qwen-numa1", "qwen-numa0"}));
  CHECK(speaking_order("Hi", V{"lena"}, ln, "") == V{"lena"});
  CHECK(after_instruction("Lena", true).find("PASS") != std::string::npos);
  CHECK(after_instruction("Lena", false).find("PASS") == std::string::npos);
  if (fails) return 1;
  std::puts("test-group-dispatcher ok");
  return 0;
}
