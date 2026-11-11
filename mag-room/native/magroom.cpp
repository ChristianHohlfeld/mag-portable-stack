// MAG room: one process, one room, two bots.
#include "api.hpp"

#include <csignal>

int main() {
  using namespace mag;
  signal(SIGPIPE, SIG_IGN);
  curl_global_init(CURL_GLOBAL_DEFAULT);
  static std::atomic<bool> stop{false};
  struct sigaction sa{};
  sa.sa_handler = [](int) { stop = true; };
  sigaction(SIGTERM, &sa, nullptr);
  sigaction(SIGINT, &sa, nullptr);

  Config cfg = Config::from_env();
  Room room(cfg);
  vm::Guests guests(room.cfg);
  auth::Gate gate(room.cfg);
  Push push(room.cfg);
  Idol idol(room);
  Scenes scenes(room);
  Plugins plugins(room);
  Engine engine(room, guests, scenes, plugins);
  engine.imagine = [&idol](const std::string &prompt, int w, int h, const std::string &by, std::string &error) { return idol.imagine(prompt, w, h, by, error); };
  Api api(room, engine, guests, idol, scenes, plugins, gate, push);
  // A notification when an answer took a while (you probably looked away) or failed.
  const int64_t slow_s = envl("MAG_ROOM_PUSH_RUN_S", 20);
  engine.on_answer = [&](const std::string &bot, const std::string &text, int64_t seconds, bool failed) {
    if (failed) push.notify("error", bot, text, "error-" + bot);
    else if (seconds >= slow_s) push.notify("run", bot, text, "run-" + bot);
  };
  // A bot that waits for a yes or no should not wait unseen.
  engine.on_approval = [&](const std::string &bot, const std::string &what) {
    push.notify("approval", bot, "Wartet auf deine Freigabe: " + what, "approval-" + bot);
  };

  const int main_fd = http::listen_on(room.cfg.bind_host, room.cfg.port);
  if (main_fd < 0) {
    logf("magroom: cannot listen on " + room.cfg.bind_host + ":" + std::to_string(room.cfg.port));
    return 1;
  }

  engine.start();
  idol.resume();
  scenes.resume();
  logf("magroom " + api.version() + " on " + room.cfg.bind_host + ":" + std::to_string(room.cfg.port) + ", " +
       std::to_string(room.count()) + " messages");

  auto handler = [&](int fd, http::Request &r) { api.handle(fd, r); };
  // The bridge is how the bot VMs reach the room. Its address belongs to the
  // libvirt network, which may come up after the room (boot) or not exist at
  // all (CI): keep trying, and serve the chat meanwhile.
  std::thread bridge([&] {
    if (room.cfg.bridge_port <= 0) return;
    bool told = false;
    while (!stop) {
      const int fd = http::listen_on(room.cfg.bridge_ip, room.cfg.bridge_port);
      if (fd >= 0) {
        logf("magroom: VM bridge on " + room.cfg.bridge_ip + ":" + std::to_string(room.cfg.bridge_port));
        http::serve(fd, true, handler, stop);
        close(fd);
        return;
      }
      if (!told) logf("magroom: VM bridge " + room.cfg.bridge_ip + ":" + std::to_string(room.cfg.bridge_port) + " not available yet; retrying");
      told = true;
      for (int i = 0; i < 50 && !stop; ++i) usleep(100000);
    }
  });
  http::serve(main_fd, false, handler, stop);
  if (bridge.joinable()) bridge.join();
  engine.shutdown();
  // Connection threads are detached and may still be writing: leave without
  // running static destructors under them.
  _exit(0);
}
