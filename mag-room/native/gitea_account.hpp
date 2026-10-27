#pragma once
// Bot Gitea accounts (magbot). A removed bot's account and repos are never
// deleted; they are locked with an unknown password and reused on re-add.
#include "common.hpp"

inline std::vector<std::string>
gitea_args(const std::vector<std::string> &args) {
  // MAGBOT_GITEA_TEST: fake gitea binary for tests (runs without runuser).
  const std::string fake = mag::env("MAGBOT_GITEA_TEST", "");
  if (!fake.empty()) {
    std::vector<std::string> command{fake, "admin", "user"};
    command.insert(command.end(), args.begin(), args.end());
    return command;
  }
  std::vector<std::string> command{"/usr/sbin/runuser",
                                   "-u",
                                   "git",
                                   "--",
                                   "/usr/local/bin/gitea",
                                   "--config",
                                   "/etc/gitea/app.ini",
                                   "admin",
                                   "user"};
  command.insert(command.end(), args.begin(), args.end());
  return command;
}
inline bool gitea_create_or_reuse(const std::string &username,
                                  const std::string &password) {
  auto created = mag::run(
      gitea_args({"create", "--username", username, "--email",
                  username + "@mag.local", "--user-type", "User", "--password",
                  password, "--must-change-password=false"}),
      30);
  if (!created.code) return true;
  auto list = mag::run(gitea_args({"list"}), 30);
  bool known = false;
  std::istringstream rows(list.out);
  for (std::string row; std::getline(rows, row);) {
    std::istringstream cols(row);
    std::string id, name;
    cols >> id >> name;
    known = known || name == username;
  }
  if (list.code || !known ||
      mag::run(gitea_args({"change-password", "--username", username,
                           "--password", password}), 30).code)
    return false;
  // Newer Gitea sets must-change-password on change-password; clear it.
  (void)mag::run(gitea_args({"must-change-password", "--unset", username}), 30);
  return true;
}
// Lock: the only credential (inside the archived VM disk) stops working.
inline bool gitea_lock(const std::string &username, const std::string &password) {
  if (mag::env("MAGBOT_GITEA_TEST", "").empty())
    return !mag::run({"/usr/local/sbin/mag-gitea-bot-auth","lock",username},60).code;
  return !mag::run(gitea_args({"change-password", "--username", username,
                               "--password", password}), 30).code;
}

