#include "mag_deadline.hpp"
#include "../native/browser_recovery.hpp"
#include <csignal>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

namespace fs = std::filesystem;

static pid_t spawn(const std::string &program, bool ignore_term = false) {
  pid_t pid = fork();
  if (pid == 0) {
    if (ignore_term) signal(SIGTERM, SIG_IGN);
    execl(program.c_str(), program.c_str(), "40", nullptr);
    _exit(127);
  }
  if (pid < 0) throw std::runtime_error("fork");
  usleep(250000);
  return pid;
}

static int stop_browser_script() {
  pid_t child = fork();
  if (child == 0) {
    std::string script = browser_controller_stop_script();
    execl("/bin/bash", "bash", "-c", script.c_str(), nullptr);
    _exit(127);
  }
  if (child < 0) return 127;
  int status = 0;
  if (waitpid(child, &status, 0) != child || !WIFEXITED(status)) return 126;
  return WEXITSTATUS(status);
}

static void cleanup(pid_t pid) {
  kill(pid, SIGKILL);
  waitpid(pid, nullptr, 0);
}

int main() {
  mag_test_deadline(60); // mag-test-timeout: 60
  char tmp[] = "/tmp/mag-browser-recovery-XXXXXX";
  char *folder = mkdtemp(tmp);
  if (!folder) return 1;
  const auto executable = fs::path(folder) / "mag-browser";
  fs::copy_file("/bin/sleep", executable);
  fs::permissions(executable, fs::perms::owner_exec | fs::perms::owner_read |
                                     fs::perms::owner_write);

  // A valid unrelated process belonging to the same user must remain alive.
  pid_t unrelated = spawn("/bin/sleep");
  pid_t daemon = spawn(executable.string());
  int result = stop_browser_script();
  if (result != 0 || kill(unrelated, 0) != 0) {
    std::cerr << "TERM recovery failed: " << result << "\n";
    cleanup(daemon);cleanup(unrelated);fs::remove_all(folder);return 2;
  }
  cleanup(daemon);

  // A controller that ignores TERM must be selectively killed after the
  // bounded grace period; Chromium/other processes are untouched.
  daemon = spawn(executable.string(), true);
  result = stop_browser_script();
  if (result != 0 || kill(unrelated, 0) != 0) {
    std::cerr << "KILL fallback failed: " << result << "\n";
    cleanup(daemon);cleanup(unrelated);fs::remove_all(folder);return 3;
  }
  cleanup(daemon);

  // A defunct (zombie) mag-browser name must not block an upgrade.
  daemon = fork();
  if (daemon == 0) {
    execl(executable.c_str(), executable.c_str(), "0", nullptr);
    _exit(127);
  }
  usleep(300000); // intentionally don't reap child yet
  result = stop_browser_script();
  if (result != 0 || kill(unrelated, 0) != 0) {
    std::cerr << "Zombie recovery failed: " << result << "\n";
    cleanup(daemon);cleanup(unrelated);fs::remove_all(folder);return 4;
  }
  waitpid(daemon, nullptr, 0);
  cleanup(unrelated);
  fs::remove_all(folder);
  return 0;
}
