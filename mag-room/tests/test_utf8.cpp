#include "../native/json.hpp"
#include <cassert>
#include <cstdio>
int main() {
  using namespace mj;
  assert(utf8_fix("ok \xC3\xA4") == "ok \xC3\xA4");
  assert(utf8_fix("a\xFF" "b") == "a\xEF\xBF\xBD" "b");
  assert(utf8_fix("x\xE2\x82") == "x\xEF\xBF\xBD\xEF\xBF\xBD");   // truncated euro sign
  assert(utf8_fix("\xED\xA0\x80") .find("\xED") == std::string::npos); // surrogate
  assert(utf8_cut("ab\xE2\x82\xAC", 4) == "ab");                   // no split code point
  Val v = Val::object(); v["c"] = std::string("exit=0\n\xC3(\xA9 ende");
  std::string d = dump(v); assert(utf8_valid(d));
  Val back; assert(parse(d, back));
  puts("utf8 ok");
}
