#include "../native/guest_pty.hpp"
#include <chrono>
#include <iostream>
#include <thread>
#include <sys/ioctl.h>
#include <poll.h>

static bool await_text(GuestPTY &pty, const std::string &needle,
                       int timeout_ms = 5000) {
  std::string received;
  auto until = std::chrono::steady_clock::now() +
               std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < until) {
    pollfd fd{pty.fd(), POLLIN, 0};
    poll(&fd, 1, 100);
    received += pty.output();
    if (received.size() > 30000)
      received.erase(0, received.size() - 20000);
    if (received.find(needle) != std::string::npos) return true;
  }
  std::cerr << "Terminal output timeout for " << needle << ": "
            << received.substr(0, 1400) << "\n";
  return false;
}

int main() {
  GuestPTY term;
  term.start(80, 24);
  if (term.fd() < 0) return 1;
  term.input("echo MAG_TERMINAL_READY\r");
  if (!await_text(term, "MAG_TERMINAL_READY")) return 2;
  term.resize(112, 33);
  winsize dimensions{};
  if (ioctl(term.fd(), TIOCGWINSZ, &dimensions) ||
      dimensions.ws_col != 112 || dimensions.ws_row != 33) return 3;
  term.input("echo MAG_TERMINAL_RESIZE_OK\r");
  if (!await_text(term, "MAG_TERMINAL_RESIZE_OK")) return 4;
  term.stop();
  return 0;
}
