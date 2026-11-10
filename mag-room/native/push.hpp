// Web Push for the room: a notification on Chris' devices when a bot's
// answer took a while or failed. Devices subscribe from the settings page.
#pragma once
#include "http.hpp"
#include "room.hpp"

#include <openssl/bn.h>
#include <openssl/ec.h>
#include <openssl/ecdsa.h>
#include <openssl/kdf.h>
#include <openssl/obj_mac.h>
#include <openssl/param_build.h>
#include <thread>

namespace mag {
namespace pushcore {
using namespace std::chrono;
using mj::Val;
inline std::string STATE_DIR;
inline std::string env(const char *k, const std::string &d) { return mag::env(k, d); }
inline std::string trim(const std::string &s) { return mag::trim(s); }
inline std::string read_file(const std::string &path, bool *ok) {
  std::string out;
  const bool good = mag::read_file(path, out);
  if (ok) *ok = good;
  return out;
}
inline bool write_file(const std::string &path, const std::string &data) { return mag::write_atomic(path, data, 0600); }
struct CmdResult {
  int code = -1;
  std::string out;
};
inline CmdResult run_argv(const std::vector<std::string> &args, int timeout_s) {
  RunOptions opt;
  opt.timeout_s = timeout_s;
  const RunResult r = mag::run(args, opt);
  return {r.code, r.out};
}
#include "push_core.hpp"
} // namespace pushcore

class Push {
public:
  explicit Push(const Config &cfg) { pushcore::STATE_DIR = cfg.state_dir; }

  bool configured() { return pushcore::push_configured(); }
  Val vapid() {
    Val v = Val::object();
    v["configured"] = configured();
    v["publicKey"] = configured() ? pushcore::push_public_b64() : "";
    return v;
  }
  void subscribe(int fd, const std::string &body) {
    std::string err;
    int code = 200;
    const std::string ok = pushcore::push_subscribe_body(body, err, code);
    if (ok.empty()) return http::send_error(fd, code, err);
    http::send(fd, 200, "application/json; charset=utf-8", ok);
  }
  void unsubscribe(int fd, const std::string &body) {
    pushcore::push_unsubscribe(jstr(jparse(body), "endpoint"));
    http::send_ok(fd);
  }
  // kind: "run" (an answer is ready) or "error".
  void notify(const std::string &kind, const std::string &title, const std::string &body, const std::string &tag) {
    pushcore::notify(pushcore::PushNote(kind, title, body, "/", tag));
  }
};

} // namespace mag
