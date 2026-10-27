#pragma once
// mag-test-timeout: 60
// One deadline for the whole native binary. The process exits 124 instead of
// sitting on a socket, lock or child.
#include <csignal>
#include <cstdlib>
#include <unistd.h>

inline void mag_test_deadline_hit(int) { _Exit(124); }

inline void mag_test_deadline(unsigned seconds = 60) {
  std::signal(SIGALRM, mag_test_deadline_hit);
  alarm(seconds);
}
