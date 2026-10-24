// Bot Gitea accounts are never deleted: removal locks them, re-adding reuses
// an existing account. Uses a fake gitea (MAGBOT_GITEA_TEST); no server needed.
#include "../native/gitea_account.hpp"
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

static void require(bool ok, const char *why) {
  if (!ok) throw std::runtime_error(why);
}
static std::string slurp(const std::filesystem::path &p) {
  std::ifstream f(p);
  return {std::istreambuf_iterator<char>(f), {}};
}

int main() {
  namespace fs = std::filesystem;
  char tmpl[] = "/tmp/mag-gitea-test.XXXXXX";
  const fs::path dir = mkdtemp(tmpl);
  const fs::path fake = dir / "gitea", log = dir / "log";
  std::ofstream(fake) << "#!/bin/sh\n"
      "d=\"$(dirname \"$0\")\"\necho \"$@\" >> \"$d/log\"\n"
      "case \"$3\" in\n"
      " create) [ -f \"$d/exists\" ] && { echo 'user already exists' >&2; exit 1; }; touch \"$d/exists\";;\n"
      " list) echo 'ID   Username      Email'; [ -f \"$d/exists\" ] && echo '7    magbot-agent3 magbot-agent3@mag.local';;\n"
      " delete) exit 0;;\n"
      "esac\nexit 0\n";
  fs::permissions(fake, fs::perms::owner_all);
  setenv("MAGBOT_GITEA_TEST", fake.c_str(), 1);
  try {
    require(gitea_create_or_reuse("magbot-agent3", "first"), "fresh create");
    require(gitea_lock("magbot-agent3", "locked"), "lock on removal");
    require(gitea_create_or_reuse("magbot-agent3", "second"), "re-add reuses existing user");
    require(!gitea_create_or_reuse("magbot-agent4", "x"), "unknown user after failed create must fail");
    const std::string calls = slurp(log);
    require(calls.find("delete") == std::string::npos, "never deletes an account");
    require(calls.find("--purge") == std::string::npos, "never purges repos");
    require(calls.find("change-password --username magbot-agent3 --password locked") != std::string::npos, "lock resets credential");
    require(calls.find("change-password --username magbot-agent3 --password second") != std::string::npos, "re-add sets new credential");
    require(calls.find("must-change-password --unset magbot-agent3") != std::string::npos, "re-add clears forced change");
  } catch (const std::exception &e) {
    std::cerr << "FAIL: " << e.what() << '\n' << slurp(log);
    fs::remove_all(dir);
    return 1;
  }
  fs::remove_all(dir);
  std::cout << "gitea account PASS\n";
}
