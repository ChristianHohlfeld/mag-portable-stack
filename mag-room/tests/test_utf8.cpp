#include "mag_deadline.hpp"
// UTF-8 sanitizing: Unicode Table 3-8 vectors + Markus Kuhn stress test.
#include "../native/json.hpp"
#include <cassert>
#include <cstdio>
#include <fstream>
#include <sstream>
static int fffd(const std::string &r) { int n = 0; for (size_t i = 0; (i = r.find("\xEF\xBF\xBD", i)) != std::string::npos; i += 3) n++; return n; }
int main(int argc, char **argv) {
  mag_test_deadline(60); // mag-test-timeout: 60
  using namespace mj;
  assert(utf8_fix("ok \xC3\xA4") == "ok \xC3\xA4");
  // Unicode Core Spec 3.9 Table 3-8: 61 F1 80 80 E1 80 C2 62 80 63 80 BF 64
  std::string t38 = "\x61\xF1\x80\x80\xE1\x80\xC2\x62\x80\x63\x80\xBF\x64";
  assert(utf8_fix(t38) == "a\xEF\xBF\xBD\xEF\xBF\xBD\xEF\xBF\xBD" "b\xEF\xBF\xBD" "c\xEF\xBF\xBD\xEF\xBF\xBD" "d");
  assert(fffd(utf8_fix("\xE2\x82\x41")) == 1);
  assert(fffd(utf8_fix("\xED\xA0\x80")) == 3);
  assert(fffd(utf8_fix("\xC0\xAF")) == 2);
  assert(utf8_fix("x\xE2\x82") == "x\xEF\xBF\xBD");
  assert(utf8_cut("ab\xE2\x82\xAC", 4) == "ab");
  assert(utf8_cut("ab\xE2\x82\xAC", 5) == "ab\xE2\x82\xAC");
  Val v = Val::object(); v["c"] = std::string("exit=0\n\xC3(\xA9 ende");
  assert(utf8_valid(dump(v)));
  if (argc > 1) {
    std::ifstream f(argv[1], std::ios::binary); std::stringstream ss; ss << f.rdbuf();
    std::string k = ss.str(), r = utf8_fix(k);
    assert(utf8_valid(r) && fffd(r) > 50);
    Val w = Val::object(); w["k"] = k; Val back; assert(parse(dump(w), back) && back.find("k")->asStr() == r);
    printf("kuhn: %zu bytes, %d replacements\n", k.size(), fffd(r));
  }
  puts("utf8 ok");
}
