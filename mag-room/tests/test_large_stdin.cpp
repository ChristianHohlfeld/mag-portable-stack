#include "../native/common.hpp"
#include <cassert>
#include <iostream>
int main() {
  const std::string data(256 * 1024, 'Z'); // above MAX_ARG_STRLEN
  const auto r = mag::run({"/bin/cat"}, 10, 2 * 1024 * 1024, nullptr, &data);
  assert(r.code == 0);
  assert(r.out == data);
  const std::string multi(2 * 1024 * 1024, 'Q');
  const auto big = mag::run({"/bin/cat"}, 10, 4 * 1024 * 1024, nullptr, &multi);
  assert(big.code == 0);
  assert(big.out == multi);
  bool got_limit = false;
  try { (void)mag::run({"/bin/cat"}, 10, 128, nullptr, &data); }
  catch (const std::runtime_error &e) {
    got_limit = std::string(e.what()).find("output limit") != std::string::npos;
  }
  assert(got_limit && "oversized stdout must error, never silently disappear");
  std::cout << "stdin streaming and output-limit diagnostics OK\n";
}
