#include <unistd.h>
#include "../native/group_dispatcher.hpp"
#include <cstdio>
#include <cstdlib>
using namespace group_chat;
static int fails = 0;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #x); ++fails; } } while (0)
int main() {
  alarm(60);
  const std::vector<Bot> r = {{"lena", "Lena"}, {"noah", "Noah"}};
  using V = std::vector<std::string>;
  CHECK(addressed("Noah, schreib mir ein Skript", r) == V{"noah"});
  CHECK(addressed("@lena was meinst du?", r) == V{"lena"});
  CHECK(addressed("Hi beide", r) == (V{"lena", "noah"}));
  CHECK(addressed("Hi alle", r) == (V{"lena", "noah"}));
  CHECK(addressed("Was meint ihr?", r).size() == 2);
  CHECK(addressed("lösch alle Dateien im Ordner", r).empty());
  CHECK(addressed("Wie spät ist es?", r).empty());
  CHECK(addressed("Lenafoo", r).empty());
  // Shared alias: "Lena" vs "Lena-Mini"
  const std::vector<Bot> q = {{"a", "Lena"}, {"b", "Lena-Mini"}};
  CHECK(addressed("Lena-Mini bitte", q) == V{"b"});
  CHECK(addressed("Lena bitte", q) == V{"a"});
  CHECK(is_pass("PASS"));
  CHECK(is_pass("  pass.\n"));
  CHECK(is_pass("**PASS**"));
  CHECK(leading_pass("PASS\n\n- User: [Chris]: Hallo?"));
  CHECK(!leading_pass("PASS"));
  CHECK(maybe_pass("PASS\n\n- User: x"));
  CHECK(!leading_pass("Passwort ist falsch\nmehr"));
  CHECK(!leading_pass("Hier ist der Code:\nPASS"));
  CHECK(is_pass("[Noah] PASS"));
  CHECK(is_pass("**[PASS]**"));
  CHECK(is_pass("<PASS>"));
  CHECK(!is_pass("Passend dazu: X"));
  CHECK(is_pass("Kein Content")); // live 2026-11-07 Lena chip-in
  CHECK(is_pass("*kein content.*"));
  CHECK(is_pass("No content"));
  CHECK(!is_pass("Kein Content-Problem, nur ein Layoutfehler"));
  CHECK(!is_pass("Keine Antwort."));
  CHECK(maybe_pass("Kein Con"));
  CHECK(maybe_pass("Kein Content."));
  CHECK(!maybe_pass("Kein Problem"));
  CHECK(is_pass("[[MAG:ENDE]]")); // live 2026-11-07 Noah
  CHECK(is_pass(" [[MAG:WEITER]]\n"));
  CHECK(!is_pass("Fertig. [[MAG:ENDE]]"));
  CHECK(maybe_name_only("No", {"Lena", "Noah"}));
  CHECK(maybe_name_only("[Noah", {"Lena", "Noah"}));
  CHECK(maybe_name_only("Noah (gpt-o", {"Lena", "Noah"}));
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
  CHECK(maybe_pass("[No"));
  CHECK(!maybe_pass("Passt"));
  CHECK(!maybe_pass("Ergänzung: "));
  // When every bot PASSes in a row the chat rests.
  CHECK(!conversation_rests(0, 2));
  CHECK(!conversation_rests(1, 2));
  CHECK(conversation_rests(2, 2));
  CHECK(conversation_rests(0, 1));
  if (fails) return 1;
  std::puts("test-group-dispatcher ok");
  return 0;
}
