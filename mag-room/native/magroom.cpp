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
  engine.imagine = [&idol](const std::string &prompt, int w, int h, const std::string &by, std::string &error, const std::string &then_video) { return idol.imagine(prompt, w, h, by, error, then_video); };
  // A bot names a picture as the chat shows it; without a name the newest picture.
  const auto picture = [&room](const std::string &name, std::string &error) {
    Val image;
    for (auto &m : room.all())
      if (const Val *atts = m.find("attachments"); atts && atts->isArr())
        for (auto &a : atts->asArr())
          if (jstr(a, "kind") == "image" && (name.empty() || jstr(a, "name") == name || jstr(a, "file") == name)) image = a;
    if (!image.isObj()) error = name.empty() ? "Im Chat ist kein Bild." : "Im Chat gibt es kein Bild " + name + ".";
    return image;
  };
  engine.film = [&idol, picture](const std::string &name, const std::string &prompt, const std::string &by, std::string &error,
                                 int seconds, const std::string &use) {
    if (trim(name).empty()) return idol.film(Val(), prompt, by, error, seconds); // words alone, with its sound
    const Val image = picture(name, error);
    return image.isObj() ? idol.film(image, prompt, by, error, seconds, use) : std::string();
  };
  engine.extend = [&idol](const std::string &name, const std::string &prompt, const std::string &by, std::string &error, int seconds) {
    const std::string from = idol.video_to_continue(trim(name), error);
    return from.empty() ? std::string() : idol.extend(from, prompt, by, error, seconds);
  };
  engine.model = [&idol, picture](const std::string &name, const std::string &quality, std::string &error) {
    const Val image = picture(name, error);
    return image.isObj() ? idol.start(image, quality, error) : std::string();
  };
  // Approvals per action and routines: a routine that is due is a message of
  // the chat to its bot, answered in a normal turn.
  Assist assist(room);
  engine.assist = &assist;
  assist.fire = [&room, &engine](const Val &routine, const std::string &why) {
    Val m = Val::object(), to = Val::array();
    const std::string bot = jstr(routine, "bot");
    m["from"] = "routine";
    m["role"] = "user";
    m["text"] = "Routine »" + jstr(routine, "name") + "« (" + why + "): " + jstr(routine, "instruction");
    m["routine"] = jstr(routine, "id");
    to.push(bot);
    m["mentions"] = to;
    room.add(m);
    engine.on_routine(bot, jstr(m, "text"));
  };
  // For a file routine: the files below /home/bot of the bot's VM, read only.
  // Dot files and folders, node_modules and unfinished downloads are left
  // out; a file changed in the last 20 s is marked young. A VM that is off or
  // does not answer gives null (the guest helper never starts one). Each look
  // leaves its output log in ~/.mag-shell like every guest command; the look
  // removes the one of the look before, so they do not pile up.
  assist.vm_files = [&room, &guests](const std::string &bot) -> Val {
    const std::string vm = jstr(room.bot(bot), "vm");
    if (!vm::valid_name(vm) || guests.state(vm) != "ready") return Val();
    static const std::string script = "exec python3 -c " + shell_quote(R"PY(
import json, os, stat, time
home = '/home/bot'
shell = os.path.join(home, '.mag-shell')
mark = os.path.join(shell, 'file-routine-look')
me = os.path.realpath('/proc/self/fd/1')
try:
    with open(mark) as f:
        old = f.read().strip()
    if old != me and os.path.dirname(old) == shell and old.endswith('.log'):
        os.remove(old)
except OSError:
    pass
if os.path.dirname(me) == shell:
    try:
        with open(mark, 'w') as f:
            f.write(me)
    except OSError:
        pass
skip_dirs = {'node_modules', '__pycache__', 'site-packages'}
skip_ends = ('.crdownload', '.part', '.partial', '.download', '.tmp', '~')
now = time.time()
out, cut = [], False
for root, dirs, files in os.walk(home):
    dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in skip_dirs)
    for name in sorted(files):
        if name.startswith('.') or name.endswith(skip_ends):
            continue
        p = os.path.join(root, name)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        out.append([os.path.relpath(p, home), now - max(st.st_mtime, st.st_ctime) < 20])
        if len(out) >= 15000:
            cut = True
            break
    if cut:
        break
print(json.dumps({'files': out, 'cut': cut}))
)PY");
    std::string body;
    bool big = false;
    const auto res = guests.exec(vm, script, 60, nullptr, [&](const std::string &chunk) {
      if (body.size() + chunk.size() <= (6u << 20)) body += chunk;
      else big = true;
    });
    if (res.failed_to_start || res.timed_out || res.code != 0 || big) return Val();
    const Val v = jparse(body);
    return v.isObj() && v.find("files") ? v : Val();
  };
  // Is a bot in a turn (a file routine's own run is still going on)?
  assist.busy = [&engine](const std::string &bot) {
    const Val bots = engine.status();
    for (auto &b : bots.asArr())
      if (jstr(b, "id") == bot) return jbool(b, "busy");
    return false;
  };
  assist.time_busy = [&engine](const std::string &bot) { return engine.time_routine_busy(bot); };
  Vault vault(room);
  engine.vault = &vault;
  Api api(room, engine, guests, idol, scenes, plugins, gate, push, assist, vault);
  // The same terminal callback serves image, video, photo-to-3D, scenes and
  // scene films. It reads the STORED card, never a stale worker snapshot.
  const auto generation_finished = [&room, &push](const std::string &id, bool success) {
    Val m;
    if (!room.get(id, m) || !m.find("model3d")) return;
    const Val &m3 = m["model3d"];
    const std::string kind = jstr(m3, "kind");
    // A chained video may be rejected before it gets its own job/message.
    // In that case the surviving image card holds video_error, not an image error.
    const std::string video_error = !success ? jstr(m3, "video_error") : "";
    const std::string label = !video_error.empty() ? "Video"
                            : kind == "image" ? "Bild" : kind == "video" ? "Video"
                            : kind == "scene" ? (jstr(m3, "film").empty() ? "3D-Szene" : "Szenenfilm")
                            : "3D-Modell";
    const std::string detail = !video_error.empty() ? video_error
                               : jstr(m3, "prompt", jstr(m3, "title", jstr(m3, "source")));
    const std::string preview = clip(detail.empty() ? jstr(m3, "error") : detail, 115, "…");
    push.notify(success ? "job" : "error",
                success ? "Generierung abgeschlossen" : "Generierung fehlgeschlagen",
                label + (preview.empty() ? "" : ": " + preview),
                "gen-" + id, "/?m=" + id);
  };
  idol.on_finished = generation_finished;
  scenes.on_finished = generation_finished;
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
