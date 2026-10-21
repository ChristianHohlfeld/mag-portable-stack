// Per-VM interactive terminal. The browser daemon runs as the guest 'bot'
// user. No host shell or HTTP command endpoint is used.
#pragma once
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <fcntl.h>
#include <pty.h>
#include <stdexcept>
#include <string>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

class GuestPTY {
  int master_ = -1;
  pid_t child_ = -1;

public:
  GuestPTY() = default;
  GuestPTY(const GuestPTY &) = delete;
  GuestPTY &operator=(const GuestPTY &) = delete;
  ~GuestPTY() { stop(); }
  int fd() const { return master_; }

  void start(unsigned cols = 85, unsigned rows = 20) {
    if (master_ >= 0) return;
    struct winsize ws{};
    ws.ws_col = static_cast<unsigned short>(cols);
    ws.ws_row = static_cast<unsigned short>(rows);
    child_ = forkpty(&master_, nullptr, nullptr, &ws);
    if (child_ < 0) {
      master_ = -1;
      throw std::runtime_error("forkpty failed");
    }
    if (child_ == 0) {
      setenv("TERM", "xterm-256color", 1);
      setenv("COLORTERM", "truecolor", 1);
      if (getenv("HOME")) chdir(getenv("HOME"));
      char bash[] = "/bin/bash";
      char interactive[] = "-i";
      char *args[]{bash, interactive, nullptr};
      execv(bash, args);
      _exit(127);
    }
    int flags = fcntl(master_, F_GETFL, 0);
    if (flags >= 0) fcntl(master_, F_SETFL, flags | O_NONBLOCK);
  }

  void input(const std::string &data) {
    if (master_ < 0 || data.empty() || data.size() > 600) return;
    // Keep the daemon's CDP loop responsive if a command floods the TTY.
    (void)::write(master_, data.data(), data.size());
  }

  void resize(unsigned cols, unsigned rows) {
    if (cols < 20 || cols > 300 || rows < 5 || rows > 120) return;
    if (master_ < 0) start(cols, rows);
    struct winsize ws{};
    ws.ws_col = static_cast<unsigned short>(cols);
    ws.ws_row = static_cast<unsigned short>(rows);
    ioctl(master_, TIOCSWINSZ, &ws);
    if (child_ > 0) kill(child_, SIGWINCH);
  }

  // A viewer attached to a running session: make the foreground program
  // (bash at its prompt, vim, top, ...) repaint its current screen live.
  void redraw() {
    if (master_ < 0) return;
    const pid_t group = tcgetpgrp(master_);
    if (group > 0) kill(-group, SIGWINCH);
    else if (child_ > 0) kill(child_, SIGWINCH);
  }

  std::string output() {
    if (master_ < 0) return "";
    char data[512];
    ssize_t n = ::read(master_, data, sizeof(data));
    if (n > 0) return std::string(data, static_cast<size_t>(n));
    if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) {
      stop();
      return "\r\n[Terminal-Sitzung beendet]\r\n";
    }
    return "";
  }

  void stop() {
    if (master_ >= 0) { close(master_); master_ = -1; }
    if (child_ > 0) {
      kill(child_, SIGHUP);
      // Do not block the browser controller waiting for a child process.
      waitpid(child_, nullptr, WNOHANG);
      child_ = -1;
    }
  }
};
