#include "../native/group_dispatcher.hpp"
#include <cstdio>
#include <cstdlib>
using namespace group_chat;
static int fails = 0;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #x); ++fails; } } while (0)
int main() {
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
  CHECK(is_pass("[Flint] PASS"));
  CHECK(!is_pass("Passt so, aber ergänze X"));
  CHECK(maybe_pass("PA"));
  CHECK(maybe_pass("[Fli"));
  CHECK(!maybe_pass("Passt"));
  CHECK(!maybe_pass("Ergänzung: "));
  // follow-up budget, never self, each once
  CHECK(next_followup(r, {"qwen-numa0"}, "qwen-numa0", 0) == "qwen-numa1");
  CHECK(next_followup(r, {"qwen-numa0", "qwen-numa1"}, "qwen-numa1", 1).empty());
  CHECK(next_followup(r, {"qwen-numa0"}, "qwen-numa0", kMaxFollowups).empty());
  if (fails) return 1;
  std::puts("test-group-dispatcher ok");
  return 0;
}
