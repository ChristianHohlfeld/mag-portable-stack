#include "../native/common.hpp"
#include <stdexcept>
#include <iostream>
int main() {
  const std::string data(256 * 1024, 'Z'); // above MAX_ARG_STRLEN
  const auto r = mag::run({"/bin/cat"}, 10, 2 * 1024 * 1024, nullptr, &data);
  if (r.code != 0 || r.out != data) throw std::runtime_error("256KB payload corrupted");
  const std::string multi(2 * 1024 * 1024, 'Q');
  const auto big = mag::run({"/bin/cat"}, 10, 4 * 1024 * 1024, nullptr, &multi);
  if (big.code != 0 || big.out != multi) throw std::runtime_error("2MB payload corrupted");
  bool got_limit = false;
  try { (void)mag::run({"/bin/cat"}, 10, 128, nullptr, &data); }
  catch (const std::runtime_error &e) {
    got_limit = std::string(e.what()).find("output limit") != std::string::npos;
  }
  if (!got_limit) throw std::runtime_error("oversized stdout missing clear error");
  std::cout << "stdin streaming and output-limit diagnostics OK\n";
}
