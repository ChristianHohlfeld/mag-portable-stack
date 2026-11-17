// HTTP routes of the room: the page and its assets, the JSON API, the event
// stream, and the small bridge the bot VMs may reach.
#pragma once
#include "auth.hpp"
#include "engine.hpp"
#include "idol.hpp"
#include "voiceover.hpp"
#include "push.hpp"
#include "vault.hpp"

#include <regex>

namespace mag {

class Api {
public:
  Api(Room &room, Engine &engine, vm::Guests &guests, Idol &idol, Scenes &scenes, Plugins &plugins, auth::Gate &gate, Push &push, Assist &assist,
      Vault &vault)
      : room_(room), engine_(engine), guests_(guests), idol_(idol), scenes_(scenes), plugins_(plugins), gate_(gate), push_(push), assist_(assist),
        vault_(vault) {
    // A photo's whole room: when the last of its models is through, they become one scene.
    idol_.on_scene_part = [this](const std::string &group) { assemble(group); };
    std::thread([this] { idol_.settle_scenes(); }).detach();
    // The version is the cache key of every asset: it must change with each
    // release. A release carries its commit; outside a release (tests,
    // development) the environment or the start time stands in.
    version_ = trim(slurp(room_.cfg.web_dir + "/../SOURCE_COMMIT")).substr(0, 12);
    if (version_.empty()) version_ = env("MAG_ROOM_VERSION");
    if (version_.empty()) version_ = "dev" + std::to_string(now_s());
    // The assets' cache key is what they are, not the release: a release that
    // changes nothing of the page (most change a service behind it) leaves
    // the page and its voice where they are, and an open page is not
    // reloaded for it (a reload costs the phone a minute to make its voice
    // ready again).
    web_ = content_key(room_.cfg.web_dir);
    if (web_.empty()) web_ = version_;
  }

  // FNV-1a over the names and contents of every file below a folder, in order.
  static std::string content_key(const std::string &dir) {
    namespace fs = std::filesystem;
    std::error_code ec;
    std::vector<std::string> files;
    for (fs::recursive_directory_iterator it(dir, ec), end; !ec && it != end; it.increment(ec))
      if (it->is_regular_file(ec)) files.push_back(it->path().string());
    if (files.empty()) return "";
    std::sort(files.begin(), files.end());
    uint64_t h = 1469598103934665603ull;
    auto mix = [&h](const std::string &s) { for (unsigned char c : s) { h ^= c; h *= 1099511628211ull; } h ^= 0xff; h *= 1099511628211ull; };
    for (const auto &f : files) { mix(f.substr(dir.size())); mix(slurp(f)); }
    char out[17];
    snprintf(out, sizeof out, "%016llx", (unsigned long long)h);
    return std::string(out, 12);
  }
  // What an open page must reload for: its own files.
  std::string ui_key() const { return web_; }

  void handle(int fd, http::Request &r) {
    if (r.bridge) return bridge(fd, r);
    // A bot's site, <name>.<zone>: nginx hands these requests over (tailnet
    // only). They get that bot's published files and nothing of the room.
    std::string host = lower(r.header("host"));
    host = host.substr(0, host.find(':'));
    const std::string zone = "." + site::zone();
    if (host.size() > zone.size() && ends_with(host, zone)) return bot_site(fd, r, host.substr(0, host.size() - zone.size()));
    const std::string &p = r.path;
    if (p == "/_internal/tailscale/whois") return gate_.whois(fd, r);
    if (p == "/healthz") return http::send(fd, 200, "text/plain; charset=utf-8", "ok\n");
    if (p == "/readyz") return readyz(fd);
    if (!gate_.same_site(r)) return http::send_error(fd, 403, "Ungültige Herkunft der Anfrage (Seite neu laden)");
    const auth::Actor who = gate_.identify(r);
    if (!who.ok) return http::send(fd, 403, "text/html; charset=utf-8", kNoAccess);

    if (r.method == "GET" || r.method == "HEAD") {
      if (p == "/" || p == "/index.html") return page(fd);
      if (p == "/sw.js") return asset(fd, r, room_.cfg.web_dir + "/sw.js", true, {{"Service-Worker-Allowed", "/"}});
      if (p == "/manifest.webmanifest") return manifest(fd);
      if (p == "/favicon.ico" || p == "/favicon.svg" || p == "/apple-touch-icon.png")
        return asset(fd, r, room_.cfg.web_dir + "/static" + p, false);
      if (p == "/app/code-preview.html") {
        // The only page allowed to render edited, untrusted HTML/CSS/JS.
        // Serve it as a unique-origin document; never relax the MAG app's
        // own CSP, and never grant allow-same-origin to this sandbox.
        const http::Headers h = {
            {"Content-Security-Policy",
             "sandbox allow-scripts; default-src 'none'; "
             "script-src 'unsafe-inline' 'unsafe-eval' https: blob:; "
             "style-src 'unsafe-inline' https:; img-src https: data: blob:; "
             "font-src https: data:; media-src https: data: blob:; "
             "connect-src https: wss:; worker-src blob: https:; "
             "frame-src 'self' data: blob: https:; base-uri https:; "
             "object-src 'none'; form-action 'none'"},
            {"Referrer-Policy", "no-referrer"},
            {"Cache-Control", "no-store"},
            {"Cross-Origin-Resource-Policy", "same-origin"}};
        return http::send_file(fd, r, room_.cfg.web_dir + p, "text/html; charset=utf-8", h);
      }
      if (starts_with(p, "/static/") || starts_with(p, "/app/")) return static_file(fd, r);
      if (p == "/api/version") { Val v = Val::object(); v["version"] = version_; return http::send_json(fd, 200, v); }
      if (p == "/api/state") return state(fd, r, who);
      if (p == "/api/messages") return messages(fd, r);
      if (p == "/api/media") return media_messages(fd, r);
      if (p == "/api/events") return events(fd);
      if (starts_with(p, "/api/file/") && ends_with(p, "/thumb")) return file_thumb(fd, url_decode(p.substr(10, p.size() - 10 - 6)));
      if (starts_with(p, "/api/file/") && ends_with(p, "/excerpt")) return file_excerpt(fd, url_decode(p.substr(10, p.size() - 10 - 8)));
      if (starts_with(p, "/api/file/")) return file(fd, r);
      if (p == "/api/files") return files(fd);
      if (p == "/api/files.zip") return files_zip(fd, r);
      if (starts_with(p, "/api/screen/")) return screen(fd, r);
      if (p == "/api/push/vapid") return http::send_json(fd, 200, push_.vapid());
      if (starts_with(p, "/api/bot/") && ends_with(p, "/memory")) return memory_get(fd, r);
      if (starts_with(p, "/api/shot/")) {
        // The browser as a step left it: <message>-<step>.jpg.
        const std::string name = p.substr(10);
        const size_t dash = name.rfind('-');
        if (dash == std::string::npos || !is_id(name.substr(0, dash)) || !ends_with(name, ".jpg") ||
            name.find_first_not_of("0123456789", dash + 1) != name.size() - 4)
          return http::send_error(fd, 404, "Kein Bild");
        const std::string path = room_.cfg.state_dir + "/shots/" + name;
        struct stat st{};
        if (stat(path.c_str(), &st) != 0) return http::send_error(fd, 404, "Kein Bild");
        http::Headers h;
        h.push_back({"Cache-Control", "private, max-age=86400"});
        return http::send_file(fd, r, path, "image/jpeg", h);
      }
      if (p == "/api/skills") { Val v = Val::object(); v["skills"] = room_.skills(); return http::send_json(fd, 200, v); }
      if (p == "/api/routines") {
        // The routines with when they run next, and the rules of approval with what was spent today.
        Val v = Val::object(), list = Val::array();
        const Val all = assist_.routines();
        for (auto r : all.asArr()) {
          r["when_text"] = Assist::describe_when(r["when"]);
          list.push(r);
        }
        v["routines"] = list;
        v["rules"] = assist_.rules();
        if (who.owner()) v["journal"] = assist_.journal_tail();
        return http::send_json(fd, 200, v);
      }
      if (p == "/api/plugins") {
        if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
        Val v = Val::object();
        v["plugins"] = plugins_.list();
        return http::send_json(fd, 200, v);
      }
      if (p == "/api/vault/logins") {
        // Chris's kept logins: site and account, never the password.
        if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
        Val v = Val::object();
        v["logins"] = vault_.logins();
        return http::send_json(fd, 200, v);
      }
      if (p == "/api/pay/cards") {
        // Chris's saved cards as the page shows them: brand, last four digits, expiry, name.
        if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
        Val v = Val::object();
        v["cards"] = vault_.list();
        return http::send_json(fd, 200, v);
      }
      if (p == "/api/system") return system_info(fd);
      if (p == "/api/backup") return http::send_json(fd, 200, backup::status(room_, guests_));
      if (starts_with(p, "/api/vm/")) {
        if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
        if (ends_with(p, "/files")) return vm_files(fd, r);
        if (ends_with(p, "/file")) return vm_file(fd, r);
        if (ends_with(p, "/saves")) {
          Val v = Val::object();
          v["saves"] = engine_.vm_saves(p.substr(8, p.size() - 8 - 6));
          return http::send_json(fd, 200, v);
        }
      }
      return http::send_error(fd, 404, "Nicht gefunden");
    }

    // Everything below changes something.
    if (r.method == "POST" && p == "/api/message") return post_message(fd, r, who);
    if (r.method == "POST" && p == "/api/stop") return post_stop(fd, r);
    if (r.method == "POST" && p == "/api/upload") return upload(fd, r);
    if (r.method == "POST" && p == "/api/video/voiceover") {
      if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
      return voiceover_upload(fd, r);
    }
    if (r.method == "POST" && p == "/api/video/voiceover/cue") {
      if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
      return voiceover_cue(fd, r);
    }
    if (r.method == "POST" && p == "/api/video/bake") {
      if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
      return voiceover_bake(fd, r);
    }
    if (r.method == "POST" && p == "/api/stt") return stt(fd, r);
    if ((r.method == "POST" || r.method == "DELETE") && p == "/api/stt/live") return stt_live(fd, r);
    if (r.method == "POST" && starts_with(p, "/api/3d/") && ends_with(p, "/cancel")) {
      // A 3D job or the film of a scene: each is cancelled where it runs.
      Val m;
      if (room_.get(p.substr(8, p.size() - 8 - 7), m)) {
        idol_.cancel(m);
        scenes_.cancel(m);
      }
      return http::send_ok(fd);
    }
    if (r.method == "POST" && p == "/api/video/retry") {
      // Retry only the chosen H3 segment with its stored prompt and duration.
      // A continuation reuses its ORIGINAL predecessor (not the completed
      // composite or the latest chat video); the previous result stays intact.
      if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
      const std::string message_id = jstr(jparse(r.body), "message");
      Val original;
      if (message_id.empty() || !room_.get(message_id, original) || !original.find("model3d"))
        return http::send_error(fd, 404, "Videoteil nicht gefunden.");
      const Val &m3 = original["model3d"];
      const std::string state = jstr(m3, "state");
      if (jstr(m3, "kind") != "video" || jstr(m3, "model") != "h3" ||
          (state != "done" && state != "error" && state != "cancelled"))
        return http::send_error(fd, 409, "Nur abgeschlossene H3-Videoteile können erneut generiert werden.");
      const std::string prompt = jstr(m3, "prompt");
      if (trim(prompt).empty()) return http::send_error(fd, 409, "Originalprompt fehlt.");
      const int seconds = (int)jnum(m3, "seconds", 5);
      std::string error, id;
      const std::string predecessor = jstr(m3, "continues");
      if (!predecessor.empty()) {
        // extend reads the stored predecessor's video file and appends exactly
        // this segment once, with a newly sampled seed.
        id = idol_.extend(predecessor, prompt, "chris", error, seconds);
      } else {
        Val image;
        const std::string source_file = jstr(m3, "image");
        if (!source_file.empty()) {
          for (const auto &entry : room_.all()) {
            if (const Val *atts = entry.find("attachments"); atts && atts->isArr()) {
              for (const auto &a : atts->asArr()) {
                if (jstr(a, "file") == source_file && jstr(a, "kind") == "image") {
                  image = a;
                  break;
                }
              }
            }
            if (image.isObj()) break;
          }
          // Never silently turn image-to-video into text-to-video.
          if (!image.isObj()) return http::send_error(fd, 409, "Ursprungsbild nicht mehr vorhanden.");
        }
        id = idol_.film(image, prompt, "chris", error, seconds, jstr(m3, "use", "first"));
      }
      if (id.empty()) return http::send_error(fd, 409, error.empty() ? "Videoteil konnte nicht neu gestartet werden." : error);
      Val out = Val::object();
      out["model3d"] = id;
      return http::send_json(fd, 200, out);
    }
    if (r.method == "POST" && p == "/api/video/continue") {
      // An H3 video goes on ({"message", "prompt", "seconds"}): a new part from
      // its last frame, and a new message with the whole video, joined.
      const Val in = jparse(r.body);
      std::string error;
      const std::string id = idol_.extend(jstr(in, "message"), jstr(in, "prompt"), "chris", error, (int)jnum(in, "seconds", 5));
      if (id.empty()) return http::send_error(fd, 409, error.empty() ? "Der Auftrag wurde abgelehnt." : error);
      Val out = Val::object();
      out["model3d"] = id;
      return http::send_json(fd, 200, out);
    }
    if (r.method == "POST" && p == "/api/image/act") {
      // A picture of the chat (uploaded, painted or a bot's) goes on: to a 3D
      // model ({"do": "3d", "quality"}) or to a video ({"do": "video",
      // "prompt": what should happen}). Each is a job of the MI50's one line.
      const Val in = jparse(r.body);
      const std::string file = jstr(in, "file"), what = jstr(in, "do");
      Val image;
      for (auto &m : room_.all())
        if (const Val *atts = m.find("attachments"); atts && atts->isArr() && !image.isObj())
          for (auto &a : atts->asArr())
            if (jstr(a, "file") == file && jstr(a, "kind") == "image") { image = a; break; }
      if (!image.isObj()) return http::send_error(fd, 404, "Bild nicht gefunden");
      std::string error, id;
      if (what == "3d") id = idol_.start(image, jstr(in, "quality", "normal"), error);
      else if (what == "video") id = idol_.film(image, jstr(in, "prompt"), "chris", error, (int)jnum(in, "seconds", 5), jstr(in, "use", "first"));
      else return http::send_error(fd, 400, "3d oder video");
      if (id.empty()) return http::send_error(fd, 409, error.empty() ? "Der Auftrag wurde abgelehnt." : error);
      Val out = Val::object();
      out["model3d"] = id;
      return http::send_json(fd, 200, out);
    }
    if (r.method == "POST" && p == "/api/3d/select") {
      // Which object of a photo is meant: the one at a place, the one with a name, or every one by itself.
      const Val in = jparse(r.body);
      Val found;
      double x = in.find("x") ? jnum(in, "x") : -1, y = jnum(in, "y");
      // Named instead of tapped: a bot that sees says where it is.
      std::string why = jstr(in, "name").empty() ? "" : engine_.locate(jstr(in, "file"), jstr(in, "name"), x, y);
      if (why.empty()) why = idol_.select(jstr(in, "file"), x, y, found, jbool(in, "scene", false));
      return why.empty() ? http::send_json(fd, 200, found) : http::send_error(fd, 409, why);
    }
    if (r.method == "POST" && starts_with(p, "/api/3d/") && ends_with(p, "/move")) {
      // A waiting job of the MI50's line, one place forwards or backwards.
      Val m;
      if (!room_.get(p.substr(8, p.size() - 8 - 5), m)) return http::send_error(fd, 404, "Nachricht nicht gefunden");
      const std::string why = idol_.move(m, jstr(jparse(r.body), "to"));
      return why.empty() ? http::send_ok(fd) : http::send_error(fd, 409, why);
    }
    if (r.method == "POST" && starts_with(p, "/api/3d/") && ends_with(p, "/retry")) {
      // A 3D job that failed or was cancelled, taken up again where it stopped.
      Val m;
      if (!room_.get(p.substr(8, p.size() - 8 - 6), m)) return http::send_error(fd, 404, "Nachricht nicht gefunden");
      const std::string why = idol_.retry(m, jbool(jparse(r.body), "remesh", false));
      return why.empty() ? http::send_ok(fd) : http::send_error(fd, 409, why);
    }
    if (r.method == "POST" && starts_with(p, "/api/3d/") && (ends_with(p, "/film") || ends_with(p, "/blend"))) {
      // Of a scene, when the owner asks: its film is rendered, its file for Blender fetched.
      const size_t cut = p.rfind('/');
      Val m;
      if (!room_.get(p.substr(8, cut - 8), m) || !Scenes::is_scene(m)) return http::send_error(fd, 404, "Szene nicht gefunden");
      if (p.substr(cut) == "/film") {
        const std::string why = scenes_.film(m, jstr(jparse(r.body), "voice"));
        return why.empty() ? http::send_ok(fd) : http::send_error(fd, 409, why);
      }
      const Val file = scenes_.blend(m);
      if (!file.isObj()) return http::send_error(fd, 502, "Die Datei für Blender ließ sich nicht holen");
      Val out = Val::object();
      out["file"] = file;
      return http::send_json(fd, 200, out);
    }
    if (r.method == "POST" && p == "/api/browser-live/offer") return live_offer(fd, r);
    if (r.method == "POST" && starts_with(p, "/api/browser/")) return browser_action(fd, r);
    if (r.method == "POST" && p == "/api/push/subscribe") return push_.subscribe(fd, r.body);
    if (r.method == "DELETE" && p == "/api/push/subscribe") return push_.unsubscribe(fd, r.body);

    // Destructive or configuring: the owner only.
    if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
    if (r.method == "POST" && p == "/api/room/clear") return clear(fd);
    if (r.method == "POST" && starts_with(p, "/api/file/") && ends_with(p, "/describe")) {
      // A fresh look at a picture whose description is wrong.
      if (!engine_.redescribe(url_decode(p.substr(10, p.size() - 10 - 9)))) return http::send_error(fd, 404, "Bild nicht gefunden");
      return http::send_ok(fd);
    }
    if (r.method == "DELETE" && starts_with(p, "/api/message/")) return remove(fd, p.substr(13));
    if (r.method == "PUT" && p == "/api/settings") return put_settings(fd, r);
    if (r.method == "PUT" && starts_with(p, "/api/bot/")) return put_bot(fd, r);
    if (r.method == "POST" && starts_with(p, "/api/bot/") && ends_with(p, "/model")) return model_control(fd, r);
    if (r.method == "POST" && starts_with(p, "/api/bot/") && ends_with(p, "/vm")) {
      const Val bot = room_.bot(bot_of(p));
      if (!bot.isObj()) return http::send_error(fd, 404, "Bot nicht gefunden");
      if (vm::valid_name(jstr(bot, "vm"))) return http::send_error(fd, 409, "Dieser Bot hat schon eine VM");
      if (!engine_.create_vm(bot_of(p))) return http::send_error(fd, 409, "Die VM wird schon erstellt");
      return http::send_ok(fd);
    }
    if (r.method == "POST" && starts_with(p, "/api/bot/") && ends_with(p, "/memory")) return memory_add(fd, r);
    if (r.method == "DELETE" && starts_with(p, "/api/bot/") && p.find("/memory/") != std::string::npos) return memory_delete(fd, r);
    if (starts_with(p, "/api/plugins")) return plugins(fd, r);
    if (r.method == "POST" && starts_with(p, "/api/approval/")) {
      // Chris's answer to a plugin call that waits for him: allow, always or deny.
      const std::string id = p.substr(14), decision = jstr(jparse(r.body), "decision");
      if (!plugins_.decide(id, decision) && !assist_.decide(id, decision)) return http::send_error(fd, 409, "Diese Freigabe wartet nicht mehr");
      return http::send_ok(fd);
    }
    if (starts_with(p, "/api/pay/cards")) {
      // Chris's saved cards: brand and last four digits out, a whole card in, sealed.
      if (r.method == "DELETE" && p.size() > 15) return vault_.remove(p.substr(15)) ? http::send_ok(fd) : http::send_error(fd, 404, "Karte nicht gefunden");
      if (r.method == "POST") {
        Val in = jparse(r.body);
        const std::string why = vault_.add(jstr(in, "number"), jstr(in, "exp"), jstr(in, "name"));
        in = Val();
        if (!why.empty()) return http::send_error(fd, 422, why);
        Val v = Val::object();
        v["cards"] = vault_.list();
        return http::send_json(fd, 200, v);
      }
    }
    if (starts_with(p, "/api/vault/logins/")) {
      // Chris's kept logins: changed, removed, or one password shown to him on his tap.
      std::string rest = p.substr(18), what;
      if (const size_t cut = rest.find('/'); cut != std::string::npos) { what = rest.substr(cut + 1); rest = rest.substr(0, cut); }
      if (r.method == "DELETE") return vault_.remove_login(rest) ? http::send_ok(fd) : http::send_error(fd, 404, "Login nicht gefunden");
      if (r.method == "PUT") {
        Val in = jparse(r.body);
        const bool ok = vault_.change_login(rest, jstr(in, "user"), jstr(in, "password"));
        in = Val();
        return ok ? http::send_ok(fd) : http::send_error(fd, 404, "Login nicht gefunden");
      }
      if (r.method == "POST" && what == "reveal") {
        Val login = vault_.open_login(rest);
        if (!login.isObj()) return http::send_error(fd, 404, "Login nicht gefunden");
        Val v = Val::object();
        v["password"] = jstr(login, "password");
        http::Headers h;
        h.push_back({"Cache-Control", "no-store"});
        return http::send_json(fd, 200, v, h);
      }
      return http::send_error(fd, 405, "Ändern, löschen oder zeigen");
    }
    if (r.method == "POST" && starts_with(p, "/api/pay/fill/")) {
      // At a checkout in a bot's browser: the chosen card, with the security
      // code Chris gives now, typed into the page by the room.
      const Val card = assist_.waiting_card(p.substr(14));
      if (!card.isObj() || jstr(card, "action") != "pay") return http::send_error(fd, 409, "Diese Bezahlung wartet nicht mehr");
      Val in = jparse(r.body);
      Val saved = vault_.open(jstr(in, "card"));
      std::string cvc = jstr(in, "cvc");
      in = Val();
      if (!saved.isObj()) return http::send_error(fd, 404, "Karte nicht gefunden");
      const std::string why = fill_card(jstr(room_.bot(jstr(card, "bot")), "vm"), saved, cvc, card);
      saved = Val();
      std::fill(cvc.begin(), cvc.end(), '\0');
      if (!why.empty()) return http::send_error(fd, 422, why);
      assist_.decide(jstr(card, "id"), "done");
      return http::send_ok(fd);
    }
    if (r.method == "POST" && p == "/api/routines") {
      // A job Chris sets up himself in a bot's sheet ({name, instruction, when, bot}): no card, he is the one who allows.
      const Val in = jparse(r.body);
      Val made;
      const std::string why = assist_.add(in, jstr(in, "bot"), made);
      if (!why.empty()) return http::send_error(fd, 422, why);
      made["when_text"] = Assist::describe_when(made["when"]);
      return http::send_json(fd, 200, made);
    }
    if (r.method == "PUT" && starts_with(p, "/api/routines/")) {
      // A job changed by Chris: any of name, instruction, when, bot.
      Val made;
      const std::string why = assist_.update(url_decode(p.substr(14)), jparse(r.body), made);
      if (!why.empty()) return http::send_error(fd, 422, why);
      made["when_text"] = Assist::describe_when(made["when"]);
      return http::send_json(fd, 200, made);
    }
    if (starts_with(p, "/api/routines/") && (r.method == "DELETE" || r.method == "POST")) {
      // A routine paused, resumed, run now or removed by Chris.
      std::string rest = p.substr(14), what = "remove";
      if (r.method == "POST") {
        const size_t cut = rest.rfind('/');
        if (cut == std::string::npos) return http::send_error(fd, 400, "pause, resume oder run");
        what = rest.substr(cut + 1);
        rest = rest.substr(0, cut);
        if (what != "pause" && what != "resume" && what != "run") return http::send_error(fd, 400, "pause, resume oder run");
      }
      const std::string why = assist_.act(url_decode(rest), what);
      return why.empty() ? http::send_ok(fd) : http::send_error(fd, 404, why);
    }
    if (r.method == "DELETE" && starts_with(p, "/api/skills/"))
      return room_.remove_skill(url_decode(p.substr(12))) ? http::send_ok(fd) : http::send_error(fd, 404, "Skill nicht gefunden");
    // The owner's own look into a bot's VM: a terminal and its files.
    if (r.method == "POST" && starts_with(p, "/api/vm/") && ends_with(p, "/reset")) {
      // The bot back to its first day: a fresh VM, nothing remembered.
      const std::string bot = p.substr(8, p.size() - 8 - 6);
      if (!room_.bot(bot).isObj()) return http::send_error(fd, 404, "Bot nicht gefunden");
      const std::string why = engine_.reset_vm(bot);
      return why.empty() ? http::send_ok(fd) : http::send_error(fd, 409, why);
    }
    if (r.method == "POST" && p == "/api/backup") {
      // Everything now: the room, Gitea and configuration, and every bot VM.
      const std::string why = backup::start(guests_, jstr(jparse(r.body), "scope") == "system");
      return why.empty() ? http::send_ok(fd) : http::send_error(fd, 409, why);
    }
    if (r.method == "POST" && starts_with(p, "/api/vm/") && ends_with(p, "/save")) {
      // A save of the bot's VM as it is now; it works on meanwhile.
      const std::string bot = p.substr(8, p.size() - 8 - 5);
      if (!room_.bot(bot).isObj()) return http::send_error(fd, 404, "Bot nicht gefunden");
      const std::string why = engine_.save_vm(bot);
      return why.empty() ? http::send_ok(fd) : http::send_error(fd, 409, why);
    }
    if (r.method == "POST" && starts_with(p, "/api/vm/") && ends_with(p, "/restore")) {
      // The bot's VM as it was in one of its saves.
      const std::string bot = p.substr(8, p.size() - 8 - 8);
      if (!room_.bot(bot).isObj()) return http::send_error(fd, 404, "Bot nicht gefunden");
      const std::string id = jstr(jparse(r.body), "save");
      bool known = false;
      for (auto &one : engine_.vm_saves(bot).asArr()) known = known || jstr(one, "id") == id;
      if (!known) return http::send_error(fd, 404, "Diese Sicherung gibt es nicht");
      const std::string why = engine_.restore_vm(bot, id);
      return why.empty() ? http::send_ok(fd) : http::send_error(fd, 409, why);
    }
    if (r.method == "POST" && starts_with(p, "/api/vm/") && ends_with(p, "/run")) return vm_run(fd, r);
    if (r.method == "DELETE" && starts_with(p, "/api/vm/") && ends_with(p, "/file")) return vm_delete(fd, r);
    return http::send_error(fd, 404, "Nicht gefunden");
  }

  const std::string &version() const { return version_; }

private:
  Room &room_;
  Engine &engine_;
  vm::Guests &guests_;
  Idol &idol_;
  Scenes &scenes_;
  Plugins &plugins_;
  auth::Gate &gate_;
  Push &push_;
  Assist &assist_;
  Vault &vault_;
  std::string version_, web_;
  // Serializes bake reads against removal of superseded lane files.
  std::mutex voiceover_render_mu_;

  static constexpr const char *kNoAccess =
      "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
      "<title>MAG</title><body style='font:16px system-ui;background:#17181b;color:#ececee;display:grid;place-items:center;height:100vh;margin:0'>"
      "<p>Kein Zugang. Dieser Raum gehört Chris.</p>";

  static const http::Headers &page_headers() {
    // The policy keeps model output from loading or running anything that is
    // not part of this release. The one exception is deliberate: pages of the
    // bots run in frames from their own sites (another origin, tailnet only).
    static const http::Headers h = {
        {"Referrer-Policy", "no-referrer"},
        // Isolated from other origins: shared memory for the threads of the speech model.
        {"Cross-Origin-Opener-Policy", "same-origin"},
        {"Content-Security-Policy",
         "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
         "script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; connect-src 'self'; frame-src 'self' https://*." +
             site::zone() + "; object-src 'none'; base-uri 'none'; form-action 'self'"}};
    return h;
  }

  // ---- page and assets ----------------------------------------------------
  void page(int fd) {
    std::string html = slurp(room_.cfg.web_dir + "/index.html");
    if (html.empty()) return http::send_error(fd, 500, "Oberfläche fehlt in diesem Release");
    replace_all(html, "{{V}}", web_);
    const std::string meta = "<meta name=\"mag-token\" content=\"" + gate_.ui_token() + "\">\n<meta name=\"mag-version\" content=\"" + version_ +
                             "\">\n<meta name=\"mag-ui\" content=\"" + ui_key() + "\">\n<meta name=\"mag-assets\" content=\"" + web_ + "\">";
    const size_t head = html.find("<head>");
    if (head != std::string::npos) html.insert(head + 6, "\n" + meta);
    http::send(fd, 200, "text/html; charset=utf-8", html, page_headers());
  }

  void asset(int fd, const http::Request &r, const std::string &path, bool templated, http::Headers extra = {}) {
    extra.push_back({"Cross-Origin-Resource-Policy", "same-origin"});
    if (templated) {
      std::string body = slurp(path);
      if (body.empty()) return http::send_error(fd, 404, "Nicht gefunden");
      replace_all(body, "{{V}}", web_);
      return http::send(fd, 200, mime_for(path), body, extra);
    }
    extra.push_back({"Cache-Control", r.param("v") == web_ ? "public, max-age=31536000, immutable" : "no-cache"});
    http::send_file(fd, r, path, mime_for(path), extra);
  }

  void static_file(int fd, const http::Request &r) {
    const std::string rel = url_decode(r.path);
    if (rel.find("..") != std::string::npos || rel.find('\\') != std::string::npos || rel.find('\0') != std::string::npos)
      return http::send_error(fd, 404, "Nicht gefunden");
    asset(fd, r, room_.cfg.web_dir + rel, false);
  }

  void manifest(int fd) {
    const std::string v = "?v=" + web_;
    const std::string body =
        "{\"name\":\"MAG\",\"short_name\":\"MAG\",\"start_url\":\"/\",\"scope\":\"/\",\"display\":\"standalone\","
        "\"background_color\":\"#17181b\",\"theme_color\":\"#17181b\",\"lang\":\"de\",\"icons\":["
        "{\"src\":\"/static/icon-192.png" + v + "\",\"sizes\":\"192x192\",\"type\":\"image/png\",\"purpose\":\"any\"},"
        "{\"src\":\"/static/icon-512.png" + v + "\",\"sizes\":\"512x512\",\"type\":\"image/png\",\"purpose\":\"any\"},"
        "{\"src\":\"/static/icon-maskable-192.png" + v + "\",\"sizes\":\"192x192\",\"type\":\"image/png\",\"purpose\":\"maskable\"},"
        "{\"src\":\"/static/icon-maskable-512.png" + v + "\",\"sizes\":\"512x512\",\"type\":\"image/png\",\"purpose\":\"maskable\"}]}";
    http::send(fd, 200, "application/manifest+json; charset=utf-8", body);
  }

  void readyz(int fd) {
    // The room is ready when it serves; a bot that is downloading, loading or
    // paused is a normal state and reported, never a failure.
    Val v = Val::object();
    v["ok"] = true;
    v["version"] = version_;
    Val bots = Val::object();
    const Val st = engine_.status();
    for (auto &b : st.asArr()) bots[jstr(b, "id")] = b.find("model") ? jstr(*b.find("model"), "state") : "unknown";
    v["bots"] = bots;
    http::send_json(fd, 200, v);
  }

  // ---- state and events ---------------------------------------------------
  void state(int fd, const http::Request &r, const auth::Actor &who) {
    uint64_t seq = 0;
    bool more = false;
    size_t limit = (size_t)atoi(r.param("limit").c_str());
    if (limit < 1 || limit > 500) limit = 120;
    Val v = Val::object();
    v["messages"] = room_.page("", limit, &seq, &more);
    v["seq"] = (double)seq;
    v["more"] = more;
    v["version"] = version_;
    v["ui"] = ui_key();
    v["token"] = gate_.ui_token();      // a page that outlives a restart of the room keeps working with the new one
    v["bots"] = engine_.status();
    v["settings"] = room_.settings();
    Val you = Val::object();
    you["role"] = who.role;
    you["name"] = who.name;
    v["you"] = you;
    Val features = Val::object();
    features["search"] = !room_.cfg.brave_key.empty();
    features["stt"] = stt_ready();
    features["push"] = push_.configured();
    v["features"] = features;
    v["zone"] = site::zone(); // the bots' sites are <name>.<zone>
    http::send_json(fd, 200, v);
  }

  void messages(int fd, const http::Request &r) {
    bool more = false;
    size_t limit = (size_t)atoi(r.param("limit").c_str());
    if (limit < 1 || limit > 500) limit = 120;
    Val v = Val::object();
    v["messages"] = room_.page(r.param("before"), limit, nullptr, &more);
    v["more"] = more;
    http::send_json(fd, 200, v);
  }

  // Dedicated newest-media snapshot: the ordinary 150-message chat window
  // can be all text while older or currently processing media exists.
  // Subsequent updates still arrive through the same SSE message stream.
  void media_messages(int fd, const http::Request &r) {
    size_t limit = (size_t)atoi(r.param("limit").c_str());
    if (limit < 1 || limit > 500) limit = 200;
    const std::string before = r.param("before");
    const auto all = room_.all();
    Val page = Val::array();
    bool more = false;
    for (auto it = all.rbegin(); it != all.rend(); ++it) {
      const Val &m = *it;
      const std::string id = jstr(m, "id");
      if (!before.empty() && id >= before) continue;
      bool media = m.find("model3d") != nullptr;
      if (!media) {
        if (const Val *atts = m.find("attachments"); atts && atts->isArr())
          for (const Val &a : atts->asArr())
            if (jstr(a, "voiceover_kind").empty() &&
                (jstr(a, "kind") == "video" || jstr(a, "kind") == "audio" ||
                 (jstr(a, "kind") == "image" && jstr(m, "role") != "user"))) {
              media = true; break;
            }
      }
      if (!media) continue;
      if (page.asArr().size() >= limit) { more = true; break; }
      page.push(m);
    }
    // Keep the same chronological order as /api/messages.
    std::reverse(page.arr->begin(), page.arr->end());
    Val out = Val::object();
    out["messages"] = page;
    out["more"] = more;
    http::send_json(fd, 200, out);
  }

  void events(int fd) {
    auto sub = room_.bus.subscribe();
    const std::string head = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                             "X-Accel-Buffering: no\r\nConnection: keep-alive\r\n\r\n: hello\n\n";
    bool alive = http::write_all(fd, head);
    timeval tv{20, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    const int64_t opened = mono_ms();
    while (alive) {
      std::deque<std::string> batch;
      bool overflow = false;
      {
        std::unique_lock<std::mutex> lk(sub->mu);
        sub->cv.wait_for(lk, std::chrono::seconds(15), [&] { return !sub->queue.empty() || sub->overflow; });
        batch.swap(sub->queue);
        overflow = sub->overflow;
      }
      if (overflow) break; // the client reconnects and resyncs from a snapshot
      if (batch.empty()) {
        alive = http::write_all(fd, ": ping\n\n");
      } else {
        std::string out;
        for (auto &f : batch) out += f;
        alive = http::write_all(fd, out);
      }
      // A fresh connection now and then keeps proxies and phones honest.
      if (mono_ms() - opened > 30 * 60 * 1000) break;
    }
    room_.bus.unsubscribe(sub);
  }

  // ---- messages -----------------------------------------------------------
  void post_message(int fd, const http::Request &r, const auth::Actor &who) {
    const Val in = jparse(r.body);
    if (!in.isObj()) return http::send_error(fd, 400, "JSON erwartet");
    std::string text = trim(jstr(in, "text"));
    if (text.size() > 64000) return http::send_error(fd, 413, "Nachricht zu lang");
    Val atts = Val::array();
    if (const Val *list = in.find("attachments"); list && list->isArr())
      for (auto &a : list->asArr()) {
        Val att;
        if (room_.take_staged(a.asStr(), att)) atts.push(att);
      }
    if (text.empty() && atts.asArr().empty()) return http::send_error(fd, 400, "Leere Nachricht");
    const std::string mode = jstr(in, "mode");
    Val m = Val::object();
    m["from"] = "chris";
    m["role"] = "user";
    m["name"] = who.name;
    m["text"] = text;
    m["attachments"] = atts;
    const std::string client_id = jstr(in, "client_id");
    if (!client_id.empty() && client_id.size() <= 64) m["client_id"] = client_id;
    std::vector<std::string> to;
    if (const Val *list = in.find("to"); list && list->isArr())
      for (auto &b : list->asArr()) to.push_back(b.asStr());
    // "@Lena" asks Lena and nobody else. Who is meant is settled now and kept
    // with the message, so a later rename changes neither the past nor a chip.
    bool mentions = false;
    if (to.empty() && mode != "3d") {
      std::vector<group_chat::Named> roster;
      const Val bots = room_.bots();
      for (auto &b : bots.asArr()) {
        group_chat::Named n{jstr(b, "id"), {lower(jstr(b, "name")), jstr(b, "id")}};
        if (const Val *old = b.find("aliases"); old && old->isArr())
          for (auto &a : old->asArr()) n.names.push_back(a.asStr());
        roster.push_back(n);
      }
      to = group_chat::mentioned(text, roster);
      if (!to.empty()) {
        mentions = true;
        Val ids = Val::array();
        for (auto &id : to) ids.push(id);
        m["mentions"] = ids;
      }
    }
    if (mode == "3d") {
      // A 3D order is for the worker: the bots read it as context, it asks them nothing.
      m["silent"] = true;
      m["order3d"] = jstr(in, "quality", "normal");
    } else if (mode == "image") {
      // A picture from words: for the worker, like a 3D order.
      m["silent"] = true;
      m["order_image"] = true;
    } else if (mode == "video") {
      // A video: from the picture that comes with it, or from words alone.
      m["silent"] = true;
      m["order_video"] = true;
    } else if (const std::string about = jstr(in, "about"); is_id(about)) {
      // Written at a scene or a 3D model in the chat: the message says what it is about.
      Val ref;
      if (room_.get(about, ref) && !Scenes::about(ref).empty()) {
        scenes_.recipe(ref);
        m["about"] = about;
      }
    }
    const Val stored = room_.add(m);
    Val out = Val::object();
    out["message"] = stored;
    if (mode == "image") {
      std::string error;
      const std::string shape = jstr(in, "shape", "square");
      const int w = shape == "wide" ? 1344 : shape == "tall" ? 768 : 1024, h = shape == "wide" ? 768 : shape == "tall" ? 1344 : 1024;
      out["model3d"] = idol_.imagine(jstr(in, "text"), w, h, "chris", error);
      if (!error.empty()) {
        Val note = Val::object(), m3 = Val::object();
        note["from"] = "idol";
        note["role"] = "service";
        note["state"] = "error";
        note["silent"] = true;
        note["text"] = "";
        m3["kind"] = "image";
        m3["state"] = "error";
        m3["error"] = error;
        m3["stage"] = "Fehler";
        note["model3d"] = m3;
        room_.add(note);
      }
    }
    if (mode == "video") {
      // With a picture the video starts from it (or has it as its figure);
      // words alone make it too, with its sound. It waits in the MI50's line.
      const Val *image = nullptr;
      for (auto &a : atts.asArr())
        if (jstr(a, "kind") == "image") { image = &a; break; }
      std::string error;
      const int seconds = (int)jnum(in, "seconds", 5);
      if (trim(jstr(in, "text")).empty()) error = "Was soll im Video geschehen?";
      else if (image) out["model3d"] = idol_.film(*image, jstr(in, "text"), "chris", error, seconds, jstr(in, "use", "first"));
      else out["model3d"] = idol_.film(Val(), jstr(in, "text"), "chris", error, seconds);
      if (!error.empty()) {
        Val note = Val::object(), m3 = Val::object();
        note["from"] = "idol";
        note["role"] = "service";
        note["state"] = "error";
        note["silent"] = true;
        note["text"] = "";
        m3["kind"] = "video";
        m3["state"] = "error";
        m3["error"] = error;
        m3["stage"] = "Fehler";
        note["model3d"] = m3;
        room_.add(note);
      }
    }
    if (mode == "3d") {
      const Val *image = nullptr;
      for (auto &a : atts.asArr())
        if (jstr(a, "kind") == "image") { image = &a; break; }
      std::string error;
      if (!image) error = "Für Foto zu 3D wird ein Bild gebraucht.";
      // The photo is ordered as it is (Chris: no splitting unasked); what he chose in "Objekt wählen" is a photo of its own.
      else out["model3d"] = idol_.start(*image, jstr(in, "quality", "normal"), error, Idol::scene_part(in));
      if (!error.empty()) {
        Val note = Val::object();
        note["from"] = "idol";
        note["role"] = "service";
        note["state"] = "error";
        note["silent"] = true;
        note["text"] = "";
        Val m3 = Val::object();
        m3["state"] = "error";
        m3["error"] = error;
        m3["stage"] = "Fehler";
        note["model3d"] = m3;
        room_.add(note);
      }
    } else if (mode != "image" && mode != "video") {
      // A picture or a video is an order for the worker, like a 3D order above:
      // the bots read it as context and are not asked, so they do not answer
      // and do not order the same thing again.
      engine_.on_human(stored, to, mentions);
    }
    http::send_json(fd, 201, out);
  }

  void post_stop(int fd, const http::Request &r) {
    engine_.stop(jstr(jparse(r.body), "bot"));
    http::send_ok(fd);
  }

  void remove(int fd, const std::string &id) {
    if (!is_id(id)) return http::send_error(fd, 400, "Ungültige Nachricht");
    Val removed;
    if (!room_.remove(id, &removed)) return http::send_error(fd, 404, "Nachricht nicht gefunden");
    forget_shots(id);
    idol_.forget(removed);
    scenes_.forget(removed);
    engine_.forget(id);
    http::send_ok(fd);
  }

  void clear(int fd) {
    std::vector<Val> removed;
    Val report = engine_.clear(&removed);
    idol_.purge();
    scenes_.purge();
    {
      std::error_code ec;
      std::filesystem::remove_all(room_.cfg.state_dir + "/shots", ec); // the pictures of browser steps go with their messages
    }
    Val v = Val::object();
    v["ok"] = true;
    v["removed"] = (double)removed.size();
    v["bots"] = report;
    http::send_json(fd, 200, v);
  }

  // A photo's whole room ("Ganze Szene"): every object of the photo was
  // ordered as a model of its own, each with where it stood. When none of
  // them is under way any more, those that were made are put together as
  // one scene: each as tall as it was, where it stood, turned the way the
  // photo saw it, with the floor and the walls around them. Each stays a
  // figure of its own in that scene. Once per group; a restart in between
  // changes nothing.
  void assemble(const std::string &group) {
    if (group.empty()) return;
    std::lock_guard<std::mutex> lk(assemble_mu_);
    Val actors = Val::array(), walls;
    std::string video; // the photo was the first frame of this video: its figures do what it shows
    int unnamed = 0;
    for (auto &m : room_.all()) {
      const Val *m3 = m.find("model3d");
      if (!m3) continue;
      if (jstr(*m3, "kind") == "scene" && jstr(*m3, "group") == group) return; // it stands already
      const Val *part = m3->find("scene");
      if (!part || jstr(*part, "group") != group) continue;
      const std::string state = jstr(*m3, "state");
      if (jstr(m, "state") == "streaming" || state == "queued" || state == "running") return; // not all through yet
      if (state != "done" || actors.asArr().size() >= 8) continue;
      std::string glb;
      if (const Val *atts = m.find("attachments"); atts && atts->isArr())
        for (auto &a : atts->asArr())
          if (glb.empty() && ends_with(lower(jstr(a, "name")), ".glb")) glb = jstr(a, "name");
      if (glb.empty()) continue;
      Val actor = Val::object(), at = Val::array();
      std::string name = thing_named(jstr(*m3, "image"));
      if (name.empty()) unnamed++;
      if (name.empty()) name = "Objekt " + std::to_string((int)jnum(*part, "index") + 1);
      for (int n = 2; std::any_of(actors.asArr().begin(), actors.asArr().end(), [&](const Val &a) { return jstr(a, "name") == name; }); n++)
        name = (n == 2 ? name : name.substr(0, name.rfind(' '))) + " " + std::to_string(n);
      actor["name"] = name;
      actor["model"] = glb;
      at.push(jnum(*part, "z"));   // away from the camera
      at.push(-jnum(*part, "x"));  // the camera's right is the scene's -y
      actor["at"] = at;
      actor["tall"] = jnum(*part, "tall");
      actor["lift"] = jnum(*part, "lift");
      if (const Val *seen = m3->find("seen_from")) actor["seen_from"] = *seen;
      actors.push(actor);
      if (const Val *r = part->find("room")) walls = *r;
      if (video.empty()) video = jstr(*part, "video");
    }
    if (actors.asArr().empty()) return;
    // Its figures are called what they are: a cut-out nobody has described
    // yet is looked at first (a bot that sees, when it is free); after eight
    // minutes the room stands anyway, with "Objekt N" for what is unnamed.
    // Without a bot that sees, nobody would name them: it stands at once.
    if (unnamed > 0 && engine_.anyone_sees()) {
      auto [at, fresh] = naming_.try_emplace(group, now_s() + 480);
      if (now_s() < at->second) {
        engine_.look_at_new_pictures(); // four pictures a pass; asked again on each call
        if (fresh) {
          std::thread([this, group] {
            for (int i = 0; i < 25; i++) {
              std::this_thread::sleep_for(std::chrono::seconds(20));
              { std::lock_guard<std::mutex> again(assemble_mu_); if (!naming_.count(group)) return; }
              assemble(group);
            }
          }).detach();
        }
        return;
      }
    }
    naming_.erase(group);
    Val spec = Val::object();
    spec["title"] = video.empty() ? "Raum aus dem Foto" : "Szene aus dem Video";
    spec["actors"] = actors;
    spec["light"] = "studio";
    spec["seconds"] = 4;
    spec["group"] = group;
    if (walls.isObj()) spec["room"] = walls;
    if (!video.empty()) spec["video"] = video;
    bool ok = false;
    const std::string said = scenes_.start(spec, "idol", ok);
    if (ok) return;
    logf("api: the room of a photo was not put together: " + clip(said, 400));
    Val note = Val::object(), m3 = Val::object();
    note["from"] = "idol";
    note["role"] = "service";
    note["state"] = "error";
    note["silent"] = true;
    note["text"] = "";
    m3["kind"] = "scene";
    m3["group"] = group; // so it is not tried again and again
    m3["state"] = "error";
    m3["error"] = clip(said, 300, "…");
    m3["stage"] = "Fehler";
    note["model3d"] = m3;
    room_.add(note);
  }
  std::mutex assemble_mu_;

  // Types a saved card into the checkout in a bot's browser. Fields the page
  // itself has are found by their names; a card form in a frame of the
  // payment provider is reached by the field the bot named (its number or
  // point): clicked, the number typed, then Tab and each further field in
  // the order the bot gave. Nothing of it is logged or returned.
  std::string fill_card(const std::string &vm, const Val &saved, const std::string &cvc, const Val &card) {
    if (!vm::valid_name(vm)) return "Dieser Bot hat keinen Browser.";
    const auto type = [&](const Val &target, const std::string &text) {
      Val body = Val::object();
      body["text"] = text;
      if (target.find("n")) body["n"] = jnum(target, "n");
      return guests_.browser(vm, "POST", "/type", mj::dump(body), 60, nullptr).status == 200;
    };
    const std::string number = jstr(saved, "number"), exp = jstr(saved, "exp"), name = jstr(saved, "name");
    // 1. Fields of the page itself, by their names.
    const auto r = guests_.browser(vm, "GET", "/elements", "", 30, nullptr);
    if (r.status != 200) return "Der Browser des Bots antwortet nicht.";
    const Val v = jparse(r.body);
    int n_number = 0, n_exp = 0, n_cvc = 0, n_name = 0;
    static const std::regex is_number("kartennummer|card ?number|cardnumber|cc-?number|nummer der karte", std::regex::icase),
        is_exp("ablauf|gültig|expir|mm ?/ ?(yy|jj)|cc-?exp", std::regex::icase), is_cvc("cvc|cvv|prüf|sicherheitscode|security code|cc-?csc", std::regex::icase),
        is_name("inhaber|name on card|cardholder|cc-?name|name auf der karte", std::regex::icase);
    if (const Val *els = v.find("elements"); els && els->isArr())
      for (auto &e : els->asArr()) {
        const std::string kind = jstr(e, "kind"), label = jstr(e, "label");
        if (kind != "text" && kind != "tel" && kind != "number" && kind != "password") continue;
        const int n = (int)jnum(e, "n");
        if (!n_number && std::regex_search(label, is_number)) n_number = n;
        else if (!n_exp && std::regex_search(label, is_exp)) n_exp = n;
        else if (!n_cvc && std::regex_search(label, is_cvc)) n_cvc = n;
        else if (!n_name && std::regex_search(label, is_name)) n_name = n;
      }
    const auto at = [](int n) { Val t = Val::object(); t["n"] = (double)n; return t; };
    if (n_number) {
      if (!type(at(n_number), number)) return "Die Kartennummer ließ sich nicht eintragen.";
      if (n_exp && !type(at(n_exp), exp)) return "Das Ablaufdatum ließ sich nicht eintragen.";
      if (n_cvc && !cvc.empty() && !type(at(n_cvc), cvc)) return "Die Prüfnummer ließ sich nicht eintragen.";
      if (n_name && !name.empty() && !type(at(n_name), name)) return "Der Name ließ sich nicht eintragen.";
      return "";
    }
    // 2. A form in a frame: by the field the bot named, then Tab from field to field.
    const Val target = Assist::at(card, "target");
    if (!target.isObj() || (!target.find("n") && !target.find("x"))) return "Das Kartenfeld ist auf der Seite nicht zu finden. Übernimm den Browser selbst.";
    Val click = Val::object();
    for (const char *k : {"n", "x", "y"}) if (target.find(k)) click[k] = jnum(target, k);
    if (guests_.browser(vm, "POST", "/click", mj::dump(click), 45, nullptr).status != 200) return "Das Kartenfeld ließ sich nicht anklicken.";
    usleep(300000);
    if (!type(Val::object(), number)) return "Die Kartennummer ließ sich nicht eintragen.";
    std::vector<std::string> order = {"exp", "cvc", "name"};
    if (const Val *o = card.find("order"); o && o->isArr() && !o->asArr().empty()) {
      order.clear();
      for (auto &x : o->asArr()) if (x.type == Val::STR) order.push_back(x.asStr());
    }
    for (auto &field : order) {
      const std::string text = field == "exp" ? exp : field == "cvc" ? cvc : field == "name" ? name : "";
      if (text.empty()) continue;
      guests_.browser(vm, "GET", "/key?key=Tab", "", 30, nullptr);
      usleep(150000);
      if (!type(Val::object(), text)) return "Ein Kartenfeld ließ sich nicht eintragen.";
    }
    return "";
  }

  // A deleted message takes the pictures of its browser steps along.
  void forget_shots(const std::string &id) {
    std::error_code ec;
    for (auto &f : std::filesystem::directory_iterator(room_.cfg.state_dir + "/shots", ec))
      if (starts_with(f.path().filename().string(), id + "-")) std::filesystem::remove(f.path(), ec);
  }
  std::map<std::string, int64_t> naming_; // group -> until when its figures' names are waited for

  // What a cut-out object is, as the bot that saw it began its description
  // ("Roter Stuhl: …"); "" while nobody has looked at it yet.
  std::string thing_named(const std::string &file) {
    if (file.empty()) return "";
    for (auto &m : room_.all())
      if (const Val *atts = m.find("attachments"); atts && atts->isArr())
        for (auto &a : atts->asArr()) {
          if (jstr(a, "file") != file) continue;
          const std::string said = jstr(a, "description");
          const size_t colon = said.find(':');
          if (colon == std::string::npos || colon > 40) continue;
          std::string head = trim(said.substr(0, colon));
          for (auto &c : head) if (c == '"' || c == '*' || c == '.' || c == '\'') c = ' ';
          head = trim(head);
          if (head.empty() || std::count(head.begin(), head.end(), ' ') > 3) continue;
          return head;
        }
    return "";
  }

  // ---- voiceovers ---------------------------------------------------------
  // New takes are *attachments* of their source message: SSE, backups, room
  // deletion and the Files gallery use the existing infrastructure. Each
  // new lane recording supersedes that lane's earlier take only. Baking
  // never changes the original video or any active source take.
  void voiceover_upload(int fd, const http::Request &r) {
    const std::string id = r.param("message"), video = r.param("video"), lane = r.param("lane");
    if (!is_id(id)) return http::send_error(fd, 400, "Ungültige Nachricht");
    if (!voiceover::lane(lane)) return http::send_error(fd, 400, "Spur muss voice, melody oder effects sein");
    Val m;
    if (!room_.get(id, m) || !voiceover::original(m, video))
      return http::send_error(fd, 404, "Originalvideo in dieser Nachricht nicht gefunden");
    const bool music = lane == "melody";
    const size_t limit = (music ? 32u : 12u) << 20;
    if (r.body.size() < 128 || r.body.size() > limit)
      return http::send_error(fd, 413, music ? "Melodie muss zwischen 128 Byte und 32 MB liegen"
                                              : "Audioaufnahme muss zwischen 128 Byte und 12 MB liegen");
    const std::string mime = lower(r.header("content-type"));
    const std::string name = safe_name(r.param("name"));
    const std::string suffix = name.find_last_of('.') == std::string::npos ? ""
                                 : lower(name.substr(name.find_last_of('.') + 1));
    // The native recorder keeps its existing formats and limits. For melody
    // files accept common music formats and octet-stream only when the
    // original extension is one we explicitly support. ffprobe validates
    // the *actual bytes* below, never trusting a client MIME declaration.
    std::string ext = starts_with(mime, "audio/webm") ? "weba"
                    : starts_with(mime, "audio/mp4") ? "m4a"
                    : starts_with(mime, "audio/ogg") ? "ogg" : "";
    if (music) {
      if (starts_with(mime, "audio/mpeg") || starts_with(mime, "audio/mp3")) ext = "mp3";
      else if (starts_with(mime, "audio/wav") || starts_with(mime, "audio/wave") ||
               starts_with(mime, "audio/x-wav") || starts_with(mime, "audio/x-pn-wav") ||
               starts_with(mime, "audio/vnd.wave")) ext = "wav";
      else if (starts_with(mime, "audio/flac") || starts_with(mime, "audio/x-flac")) ext = "flac";
      else if ((mime.empty() || starts_with(mime, "application/octet-stream")) &&
               (suffix == "mp3" || suffix == "wav" || suffix == "m4a" ||
                suffix == "ogg" || suffix == "weba" || suffix == "flac")) ext = suffix;
    }
    if (ext.empty()) return http::send_error(fd, 415, "Audioformat nicht unterstützt");
    // Keep the verified format as the stored extension even if a client
    // supplies a mismatching filename or a truncated 80-char basename.
    const std::string display = music && suffix == ext ? name
        : "nachvertonung-" + lane + "-" + std::to_string(now_ms()) + "." + ext;
    Val a = room_.store_file(display, r.body, "chris");
    if (!a.isObj()) return http::send_error(fd, 500, "Audio konnte nicht gespeichert werden");
    const std::string path = room_.file_path(jstr(a, "file"));
    const double duration = voiceover::seconds(path);
    if (!voiceover::audio_ok(path) || !duration) {
      unlink(path.c_str());
      return http::send_error(fd, 422, "Keine lesbare Audioaufnahme oder länger als zehn Minuten");
    }
    a["voiceover_kind"] = "take";
    a["voiceover_for"] = video;
    a["voiceover_lane"] = lane;
    if (music) {
      a["voiceover_at"] = 0.0;
      a["voiceover_from"] = 0.0;
    }
    a["duration"] = duration;
    a["created"] = (double)now_s();
    // Only one current take per (original video, lane). Replace atomically in
    // the room snapshot so SSE, backups and the file gallery agree immediately.
    // A concurrent bake may still be reading an earlier take. Never unlink
    // that file until ffmpeg finishes; room startup GC removes any leftovers.
    std::unique_lock<std::mutex> deletion_guard(voiceover_render_mu_, std::try_to_lock);
    std::vector<std::string> superseded;
    if (!room_.update(id, [&](Val &message) {
      Val next = Val::array();
      if (const Val *old = message.find("attachments"); old && old->isArr()) {
        for (const Val &item : old->asArr()) {
          const bool same_lane = jstr(item, "voiceover_kind") == "take" &&
              jstr(item, "voiceover_for") == video &&
              jstr(item, "voiceover_lane", "voice") == lane;
          if (same_lane) {
            const std::string old_file = jstr(item, "file");
            if (!old_file.empty()) superseded.push_back(old_file);
          } else next.push(item);
        }
      }
      next.push(a);
      message["attachments"] = next;
    })) {
      unlink(path.c_str());
      return http::send_error(fd, 404, "Nachricht wurde entfernt");
    }
    if (deletion_guard.owns_lock())
      for (const std::string &old_file : superseded) {
        const std::string prior = room_.file_path(old_file);
        if (!prior.empty() && old_file != jstr(a, "file")) unlink(prior.c_str());
      }
    http::send_json(fd, 201, a);
  }

  // Adjust the current melody without uploading it again. The two offsets
  // live on the melody attachment, so SSE/restart and the bake always agree.
  void voiceover_cue(int fd, const http::Request &r) {
    const Val arg = jparse(r.body);
    const std::string id = jstr(arg, "message"), video = jstr(arg, "video"),
                      file = jstr(arg, "file");
    const Val *at_arg = arg.find("at"), *from_arg = arg.find("from");
    if (!is_id(id) || !at_arg || !from_arg ||
        at_arg->type != Val::NUM || from_arg->type != Val::NUM)
      return http::send_error(fd, 400, "Melodie-Positionen müssen Zahlen in Sekunden sein");
    const double at = jnum(arg, "at"), from = jnum(arg, "from");
    if (!std::isfinite(at) || !std::isfinite(from) || at < 0 || from < 0)
      return http::send_error(fd, 400, "Ungültige Melodie-Position");
    // Lock against ffmpeg snapshotting the old offsets; retakes revalidate
    // the exact stored attachment ID inside room_.update below.
    std::unique_lock<std::mutex> guard(voiceover_render_mu_, std::try_to_lock);
    if (!guard.owns_lock()) return http::send_error(fd, 409, "Video wird gerade gemischt");
    Val m;
    if (!room_.get(id, m) || !voiceover::original(m, video) ||
        !voiceover::take(m, video, file, "melody"))
      return http::send_error(fd, 404, "Aktuelle Melodie-Spur nicht gefunden");
    const std::string video_path = room_.file_path(video), song_path = room_.file_path(file);
    const double video_length = voiceover::seconds(video_path), song_length = voiceover::seconds(song_path);
    if (!video_length || !song_length || at >= video_length || from >= song_length)
      return http::send_error(fd, 400, "Position muss innerhalb von Video und Song liegen");
    bool found = false;
    if (!room_.update(id, [&](Val &message) {
      if (Val *atts = message.find("attachments"); atts && atts->isArr())
        for (Val &a : *atts->arr)
          if (jstr(a, "file") == file && jstr(a, "voiceover_kind") == "take" &&
              jstr(a, "voiceover_lane") == "melody" && jstr(a, "voiceover_for") == video) {
            a["voiceover_at"] = at;
            a["voiceover_from"] = from;
            found = true;
          }
    }) || !found) return http::send_error(fd, 404, "Melodie wurde inzwischen ersetzt");
    Val result = Val::object();
    result["at"] = at;
    result["from"] = from;
    http::send_json(fd, 200, result);
  }

  void voiceover_bake(int fd, const http::Request &r) {
    const Val arg = jparse(r.body);
    const std::string id = jstr(arg, "message"), video = jstr(arg, "video"),
                      mode = jstr(arg, "mode", "mix");
    if (!is_id(id) || (mode != "mix" && mode != "replace"))
      return http::send_error(fd, 400, "Ungültige Nachvertonung");
    // Hold before reading attachment IDs: a new take must not remove a file
    // between the snapshot validation and ffmpeg opening that input.
    std::unique_lock<std::mutex> render_guard(voiceover_render_mu_, std::try_to_lock);
    if (!render_guard.owns_lock()) return http::send_error(fd, 409, "Ein Video wird bereits vertont");
    Val m;
    if (!room_.get(id, m) || !voiceover::original(m, video))
      return http::send_error(fd, 404, "Originalvideo nicht in dieser Nachricht");
    const Val *chosen = arg.find("lanes");
    if (!chosen || !chosen->isObj()) return http::send_error(fd, 400, "Spuren fehlen");
    std::vector<voiceover::LaneInput> lanes;
    Val saved = Val::object();
    for (const char *lane : {"voice", "melody", "effects"}) {
      const std::string file = jstr(*chosen, lane);
      if (file.empty()) continue;
      if (!voiceover::take(m, video, file, lane))
        return http::send_error(fd, 404, std::string("Die ausgewählte ") + lane + "-Tonspur gehört nicht zu diesem Video");
      const std::string path = room_.file_path(file);
      if (path.empty() || !exists(path)) return http::send_error(fd, 404, "Tonspur fehlt");
      voiceover::LaneInput input;
      input.name = lane;
      input.path = path;
      if (input.name == "melody") {
        const Val *take = voiceover::attachment(m, file);
        input.at = jnum(*take, "voiceover_at");
        input.from = jnum(*take, "voiceover_from");
      }
      lanes.push_back(input);
      saved[lane] = file;
    }
    if (lanes.empty()) return http::send_error(fd, 400, "Mindestens eine Tonspur aufnehmen");
    const std::string source = room_.file_path(video);
    if (source.empty() || !exists(source)) return http::send_error(fd, 404, "Originalvideo fehlt");
    const std::string base = room_.cfg.state_dir + "/voiceover-" + random_hex(12);
    const std::string mp4 = base + ".mp4", m4a = base + ".m4a";
    std::string error;
    if (!voiceover::bake(source, lanes, mp4, mode, error)) {
      unlink(mp4.c_str());
      return http::send_error(fd, 422, error);
    }
    if (!voiceover::extract_mix(mp4, m4a)) {
      unlink(mp4.c_str());
      unlink(m4a.c_str());
      return http::send_error(fd, 422, "Gemischte Tonspur konnte nicht erstellt werden");
    }
    Val mixed = room_.adopt_file(m4a, "tonmix-" + random_hex(4) + ".m4a", "chris");
    Val baked = room_.adopt_file(mp4, "video-nachvertonung-" + random_hex(4) + ".mp4", "chris");
    if (!mixed.isObj() || !baked.isObj()) {
      if (mixed.isObj()) unlink(room_.file_path(jstr(mixed, "file")).c_str());
      if (baked.isObj()) unlink(room_.file_path(jstr(baked, "file")).c_str());
      unlink(mp4.c_str());
      unlink(m4a.c_str());
      return http::send_error(fd, 500, "Nachvertonung konnte nicht gespeichert werden");
    }
    mixed["voiceover_kind"] = "mix";
    mixed["voiceover_for"] = video;
    mixed["voiceover_lanes"] = saved;
    baked["voiceover_kind"] = "baked";
    baked["voiceover_for"] = video;
    baked["voiceover_mix"] = jstr(mixed, "file");
    baked["voiceover_lanes"] = saved;
    baked["voiceover_mode"] = mode;
    if (!room_.update(id, [&](Val &message) {
      Val &atts = message["attachments"];
      if (!atts.isArr()) atts = Val::array();
      atts.push(mixed);
      atts.push(baked);
    })) {
      unlink(room_.file_path(jstr(mixed, "file")).c_str());
      unlink(room_.file_path(jstr(baked, "file")).c_str());
      return http::send_error(fd, 404, "Nachricht wurde entfernt");
    }
    Val response = Val::object();
    response["mix"] = mixed;
    response["video"] = baked;
    return http::send_json(fd, 201, response);
  }

  // ---- files --------------------------------------------------------------
  void upload(int fd, const http::Request &r) {
    if (r.body.empty()) return http::send_error(fd, 400, "Leere Datei");
    std::string name = r.param("name");
    if (name.empty()) name = "datei";
    // A picture whose file ends in the middle of the picture is not taken as
    // it is: the page reads the file again in a moment (a phone may still be
    // writing a photo it has just taken) and only then insists on it.
    const bool cut = image_cut(r.body);
    if (cut && r.param("force") != "1") {
      Val v = Val::object();
      v["error"] = "Das Bild ist unvollständig angekommen: seine Datei endet mitten im Bild.";
      v["code"] = "truncated";
      return http::send_json(fd, 422, v);
    }
    Val att = room_.store_file(name, r.body, "chris");
    if (!att.isObj()) return http::send_error(fd, 500, "Datei konnte nicht gespeichert werden");
    if (cut) att["cut"] = true;
    room_.stage(att);
    http::send_json(fd, 201, att);
  }

  static std::string download_disposition(const std::string &name, bool attachment) {
    std::string ascii;
    for (unsigned char c : name)
      ascii += ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                (c >= '0' && c <= '9') || c == '.' || c == '-' || c == '_') ? (char)c : '_';
    if (ascii.empty()) ascii = "download";
    ascii.resize(std::min<size_t>(ascii.size(), 128));
    return std::string(attachment ? "attachment" : "inline") +
           "; filename=\"" + ascii + "\"; filename*=UTF-8''" + url_encode(name);
  }

  void file(int fd, const http::Request &r) {
    const std::string stored = url_decode(r.path.substr(10));
    const std::string path = room_.file_path(stored);
    if (path.empty() || !exists(path)) return http::send_error(fd, 404, "Datei nicht gefunden");
    const size_t us = stored.find('_');
    const std::string name = us == std::string::npos ? stored : stored.substr(us + 1);
    const std::string mime = mime_for(name);
    // Files come from uploads and from the bots: never let one run as a page
    // of this origin.
    const bool active = starts_with(mime, "text/html") || mime == "image/svg+xml" || starts_with(mime, "text/javascript");
    http::Headers h = {{"Content-Security-Policy", r.param("view") == "1" && starts_with(mime, "text/html") ? "sandbox allow-scripts" : "sandbox"},
                       {"Cross-Origin-Resource-Policy", "same-origin"},
                       {"Cache-Control", "private, max-age=31536000, immutable"},
                       {"Content-Disposition", download_disposition(name,
                         r.param("download") == "1" || (active && r.param("view") != "1"))}};
    http::send_file(fd, r, path, mime, h);
  }

  // ---- previews of the chat's files --------------------------------------
  // Each file as it really is, small: a 3D file rendered by Blender (the
  // scene worker), a picture scaled down, a video's first frame, a sound's
  // waveform, a PDF's first page (ffmpeg, pdftoppm). Made once per file in
  // STATE/thumbs; 404 for a file that has no picture (its text is the excerpt).
  static std::string file_ext(const std::string &stored) {
    const size_t dot = stored.find_last_of('.');
    return dot == std::string::npos ? "" : lower(stored.substr(dot + 1));
  }
  void file_thumb(int fd, const std::string &stored) {
    const std::string src = room_.file_path(stored);
    if (src.empty() || !exists(src)) return http::send_error(fd, 404, "Datei nicht gefunden");
    const std::string dir = room_.cfg.state_dir + "/thumbs", out = dir + "/" + stored + ".png";
    static const http::Headers cached = {{"Cache-Control", "private, max-age=31536000, immutable"}};
    if (!exists(out)) {
      static std::mutex mu; // one preview is made at a time; the others wait for it
      std::lock_guard<std::mutex> lk(mu);
      if (!exists(out)) {
        std::error_code ec;
        fs::create_directories(dir, ec);
        const std::string ext = file_ext(stored), mime = mime_for(stored), part = out + ".part.png";
        std::string error;
        RunOptions opt;
        opt.timeout_s = 120;
        auto tool = [&](std::vector<std::string> args) { // CPUs 28-55 like the room, at low priority
          args.insert(args.begin(), {"nice", "-n", "10"});
          return run(args, opt).code == 0 && exists(part);
        };
        bool made = false;
        if (ext == "glb" || ext == "gltf" || ext == "stl" || ext == "ply" || ext == "obj" || ext == "fbx" || ext == "blend") {
          const std::string png = scenes_.thumb(stored, error);
          made = !png.empty() && write_atomic(part, png, 0640);
        } else if (starts_with(mime, "image/") && ext != "svg") {
          made = tool({"ffmpeg", "-v", "error", "-y", "-i", src, "-vf", "scale='min(400,iw)':-2", "-frames:v", "1", part});
        } else if (starts_with(mime, "video/")) {
          made = tool({"ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", src, "-vf", "scale='min(400,iw)':-2", "-frames:v", "1", part}) ||
                 tool({"ffmpeg", "-v", "error", "-y", "-i", src, "-vf", "scale='min(400,iw)':-2", "-frames:v", "1", part});
        } else if (starts_with(mime, "audio/")) {
          made = tool({"ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex",
                       "aformat=channel_layouts=mono,showwavespic=s=400x240:colors=0x7c8cff:scale=sqrt", "-frames:v", "1", part});
        } else if (ext == "pdf") {
          const std::string base = out + ".part";
          made = tool({"pdftoppm", "-png", "-f", "1", "-l", "1", "-scale-to", "400", "-singlefile", src, base});
        }
        if (!made) {
          fs::remove(part, ec);
          return http::send_error(fd, 404, error.empty() ? "Keine Vorschau" : error);
        }
        fs::rename(part, out, ec);
        if (ec) return http::send_error(fd, 500, "Vorschau nicht gespeichert");
      }
    }
    std::string png;
    read_file(out, png, 16u << 20);
    http::send(fd, 200, "image/png", png, cached);
  }
  // The beginning of what a file says, for files whose content is text: a
  // text file as it is, a document (PDF, Word, OpenDocument, PowerPoint) by
  // its text. {"text": ...}, or "" when there is none.
  void file_excerpt(int fd, const std::string &stored) {
    const std::string src = room_.file_path(stored);
    if (src.empty() || !exists(src)) return http::send_error(fd, 404, "Datei nicht gefunden");
    const std::string mime = mime_for(stored), ext = file_ext(stored);
    std::string text;
    static const std::set<std::string> plain = {"txt", "md", "csv", "json", "xml", "yaml", "yml", "html", "htm", "css", "js", "ts", "py",
                                                "sh", "c", "cc", "cpp", "h", "hpp", "go", "rs", "java", "toml", "ini", "log", "svg", "tex"};
    if (starts_with(mime, "text/") || plain.count(ext)) {
      read_file(src, text, 4096);
    } else {
      Val a = Val::object();
      a["file"] = stored;
      const size_t us = stored.find('_');
      a["name"] = us == std::string::npos ? stored : stored.substr(us + 1);
      text = engine_.text_of(a);
    }
    Val v = Val::object();
    v["text"] = mj::utf8_cut(text, 1500);
    http::send_json(fd, 200, v, {{"Cache-Control", "private, max-age=86400"}});
  }

  // ---- the files of the chat ---------------------------------------------
  // Everything the chat holds as a file (uploads, pictures, models, scenes,
  // films, videos, documents), newest first, with the message it is in. The
  // page shows them as one folder; the files stay where they are.
  static std::string file_group(const Val &a, const Val *m3) {
    const std::string kind = jstr(a, "kind"), name = jstr(a, "name");
    if (kind == "image") return "Bilder";
    if (kind == "video") return "Videos";
    if (kind == "audio") return "Audio";
    if (m3 && jstr(*m3, "kind") == "scene") return "Szenen";
    if (kind == "model") return "3D";
    return "Dokumente";
  }
  Val chat_files() {
    Val out = Val::array();
    const auto all = room_.all();
    for (auto m = all.rbegin(); m != all.rend(); ++m) {
      const Val *atts = m->find("attachments");
      if (!atts || !atts->isArr()) continue;
      const Val *m3 = m->find("model3d");
      for (auto &a : atts->asArr()) {
        if (jstr(a, "file").empty()) continue;
        Val f = Val::object();
        for (const char *k : {"file", "name", "mime", "kind", "description"})
          if (!jstr(a, k).empty()) f[k] = jstr(a, k);
        f["size"] = (double)jnum(a, "size");
        f["group"] = file_group(a, m3);
        f["message"] = jstr(*m, "id");
        f["from"] = jstr(*m, "from");
        f["ts"] = (double)jnum(*m, "ts");
        if (m3) {
          if (!jstr(*m3, "title").empty()) f["title"] = jstr(*m3, "title");
          if (!jstr(*m3, "image").empty()) f["image"] = jstr(*m3, "image");
        }
        out.push(f);
      }
    }
    return out;
  }
  void files(int fd) {
    Val v = Val::object();
    v["files"] = chat_files();
    http::send_json(fd, 200, v);
  }
  // Several files of the chat as one ZIP (stored, not compressed: they are
  // pictures, models and videos), folders by kind. Its length is known before
  // the first byte, the checksums are worked out while it streams.
  void files_zip(int fd, const http::Request &r) {
    std::set<std::string> wanted;
    for (std::istringstream in(r.param("files")); !in.eof();) {
      std::string one;
      std::getline(in, one, ',');
      if (!one.empty()) wanted.insert(one);
    }
    struct Entry { std::string path, name; uint64_t size; uint32_t when; uint32_t crc = 0; uint64_t offset = 0; };
    std::vector<Entry> entries;
    std::set<std::string> names;
    for (auto &f : chat_files().asArr()) {
      const std::string stored = jstr(f, "file");
      if (!wanted.empty() && !wanted.count(stored)) continue;
      const std::string path = room_.file_path(stored);
      struct stat st{};
      if (path.empty() || stat(path.c_str(), &st) != 0 || !S_ISREG(st.st_mode)) continue;
      std::string name = jstr(f, "name", stored);
      for (auto &c : name) if (c == '/' || c == '\\' || (unsigned char)c < 32) c = '_';
      const time_t t = (time_t)(jnum(f, "ts") / 1000);
      char day[16];
      std::strftime(day, sizeof day, "%Y-%m-%d", std::localtime(&t));
      std::string full = jstr(f, "group") + "/" + day + " " + name;
      for (int i = 2; names.count(full); ++i) full = jstr(f, "group") + "/" + day + " " + std::to_string(i) + " " + name;
      names.insert(full);
      std::tm lt{};
      localtime_r(&t, &lt);
      const uint32_t when = (uint32_t)(((std::max(lt.tm_year - 80, 0) << 9) | ((lt.tm_mon + 1) << 5) | lt.tm_mday) << 16) |
                            (uint32_t)((lt.tm_hour << 11) | (lt.tm_min << 5) | (lt.tm_sec / 2));
      entries.push_back({path, full, (uint64_t)st.st_size, when});
      if (st.st_size >= 0xffffffffLL) return http::send_error(fd, 413, "Eine Datei ist für ein ZIP zu groß");
    }
    if (entries.empty()) return http::send_error(fd, 404, "Keine Dateien");
    uint64_t total = 22, central = 0;
    for (auto &e : entries) total += 30 + e.name.size() + e.size + 16, central += 46 + e.name.size();
    total += central;
    if (total >= 0xffffffffULL) return http::send_error(fd, 413, "Zu viele Daten für ein ZIP; bitte weniger auswählen");
    static uint32_t table[256];
    static std::once_flag once;
    std::call_once(once, [] {
      for (uint32_t i = 0; i < 256; ++i) {
        uint32_t c = i;
        for (int k = 0; k < 8; ++k) c = c & 1 ? 0xedb88320u ^ (c >> 1) : c >> 1;
        table[i] = c;
      }
    });
    auto le = [](std::string &out, uint64_t v, int bytes) { for (int i = 0; i < bytes; ++i) out += (char)((v >> (8 * i)) & 0xff); };
    char stamp[32];
    const time_t now = time(nullptr);
    std::strftime(stamp, sizeof stamp, "%Y-%m-%d", std::localtime(&now));
    // A temporary regular file supports byte-range retries from Android's
    // download manager, unlike a live HTTP stream. Never expose partial ZIPs.
    const std::string temp = room_.cfg.state_dir + "/mag-zip-" + random_hex(16) + ".zip";
    const int zipfd = ::open(temp.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (zipfd < 0) return http::send_error(fd, 500, "ZIP-Datei konnte nicht erstellt werden");
    const auto zipwrite = [&](const void *data, size_t left) {
      const char *p = static_cast<const char *>(data);
      while (left) {
        ssize_t n = ::write(zipfd, p, left);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        left -= (size_t)n; p += n;
      }
      return true;
    };
    bool zipok = true;
    uint64_t at = 0;
    std::vector<char> buf(1 << 20);
    for (auto &e : entries) {
      e.offset = at;
      std::string local;
      le(local, 0x04034b50, 4); le(local, 20, 2); le(local, 0x0808, 2); le(local, 0, 2); // stored, data descriptor, UTF-8 name
      le(local, e.when, 4); le(local, 0, 4); le(local, 0, 4); le(local, 0, 4);
      le(local, e.name.size(), 2); le(local, 0, 2);
      local += e.name;
      if (!zipwrite(local.data(), local.size())) { zipok = false; break; }
      at += local.size();
      const int file = open(e.path.c_str(), O_RDONLY | O_CLOEXEC);
      uint64_t left = e.size;
      uint32_t c = 0xffffffffu;
      while (file >= 0 && left > 0) {
        const ssize_t got = ::read(file, buf.data(), std::min<uint64_t>(buf.size(), left));
        if (got <= 0) break;
        for (ssize_t i = 0; i < got; ++i) c = table[(c ^ (unsigned char)buf[i]) & 0xff] ^ (c >> 8);
        if (!zipwrite(buf.data(), (size_t)got)) { zipok = false; break; }
        left -= (uint64_t)got;
      }
      if (file >= 0) close(file);
      if (left > 0 || !zipok) { zipok = false; break; } // reject a short input
      e.crc = c ^ 0xffffffffu;
      std::string desc;
      le(desc, 0x08074b50, 4); le(desc, e.crc, 4); le(desc, e.size, 4); le(desc, e.size, 4);
      if (!zipwrite(desc.data(), desc.size())) { zipok = false; break; }
      at += e.size + desc.size();
    }
    if (!zipok) { ::close(zipfd); unlink(temp.c_str()); return http::send_error(fd, 500, "ZIP konnte nicht vollständig erstellt werden"); }
    std::string dir;
    for (auto &e : entries) {
      le(dir, 0x02014b50, 4); le(dir, 20, 2); le(dir, 20, 2); le(dir, 0x0808, 2); le(dir, 0, 2);
      le(dir, e.when, 4); le(dir, e.crc, 4); le(dir, e.size, 4); le(dir, e.size, 4);
      le(dir, e.name.size(), 2); le(dir, 0, 2); le(dir, 0, 2); le(dir, 0, 2); le(dir, 0, 2); le(dir, 0, 4);
      le(dir, e.offset, 4);
      dir += e.name;
    }
    le(dir, 0x06054b50, 4); le(dir, 0, 2); le(dir, 0, 2); le(dir, entries.size(), 2); le(dir, entries.size(), 2);
    le(dir, central, 4); le(dir, at, 4); le(dir, 0, 2);
    zipok = zipwrite(dir.data(), dir.size());
    if (::close(zipfd) != 0) zipok = false;
    if (!zipok || file_size(temp) != total) {
      unlink(temp.c_str());
      return http::send_error(fd, 500, "ZIP konnte nicht vollständig erstellt werden");
    }
    timeval tv{300, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    const std::string name = std::string("MAG Dateien ") + stamp + ".zip";
    const http::Headers h = {{"Content-Disposition", download_disposition(name, true)},
                             {"Cache-Control", "no-store"}};
    http::send_file(fd, r, temp, "application/zip", h);
    unlink(temp.c_str());
  }

  // ---- the bots' sites ----------------------------------------------------
  // Static files of the www folder the bot's VM published, served under the
  // bot's current name. An earlier name, the bot's id or its VM name redirect
  // there (temporarily: a name can change again).
  void bot_site(int fd, const http::Request &r, const std::string &name) {
    // A bot's page runs in a frame of the room's page, which is cross-origin isolated: it says that it may.
    static const http::Headers open = {{"Access-Control-Allow-Origin", "*"}, {"Cache-Control", "no-cache"}, {"Cross-Origin-Resource-Policy", "cross-origin"}};
    auto missing = [&] {
      http::send(fd, 404, "text/html; charset=utf-8",
                 "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Nicht gefunden</title>"
                 "<body style='font:16px system-ui;margin:14vh auto;max-width:30em;padding:0 20px'><h1 style='font-size:20px'>Nicht gefunden</h1>"
                 "<p>Unter dieser Adresse liegt nichts.</p>", open);
    };
    if (r.method != "GET" && r.method != "HEAD") return http::send(fd, 405, "text/plain; charset=utf-8", "Nur lesen\n", open);
    bool current = false;
    const Val bot = site::resolve(room_.bots(), name, current);
    const std::string vm = jstr(bot, "vm"), query = r.query.empty() ? "" : "?" + r.query;
    if (!bot.isObj() || !vm::valid_name(vm)) return missing();
    auto moved = [&](int code, const std::string &where) {
      http::Headers h = open;
      h.push_back({"Location", where});
      http::send(fd, code, "text/plain; charset=utf-8", "Umgezogen\n", h);
    };
    if (!current) return moved(302, site::base_url(bot) + r.path + query);
    const std::string rel = url_decode(r.path);
    // No way up, no dot files, nothing odd.
    if (rel.empty() || rel[0] != '/' || rel.find('\0') != std::string::npos || rel.find('\\') != std::string::npos ||
        rel.find("/.") != std::string::npos)
      return missing();
    const std::string root = site::www_root() + "/" + vm;
    std::string path = root + rel;
    struct stat st{};
    if (stat(path.c_str(), &st) != 0) return missing();
    if (S_ISDIR(st.st_mode)) {
      if (rel.back() != '/') return moved(301, r.path + "/" + query);
      path += "index.html";
      if (stat(path.c_str(), &st) != 0) return missing();
    }
    // A link inside the folder must not lead out of it.
    char real[PATH_MAX], real_root[PATH_MAX];
    if (!S_ISREG(st.st_mode) || !realpath(path.c_str(), real) || !realpath(root.c_str(), real_root) ||
        !starts_with(real, std::string(real_root) + "/"))
      return missing();
    http::Headers h = open;
    const std::string etag = "\"" + std::to_string((long long)st.st_mtime) + "-" + std::to_string((long long)st.st_size) + "\"";
    h.push_back({"ETag", etag});
    if (r.header("if-none-match") == etag) return http::send(fd, 304, "", "", h);
    http::send_file(fd, r, path, mime_for(path), h);
  }

  // ---- settings and bots --------------------------------------------------
  void put_settings(int fd, const http::Request &r) {
    const Val in = jparse(r.body);
    if (!in.isObj()) return http::send_error(fd, 400, "JSON erwartet");
    bool net = false;
    room_.update_settings([&](Val &s) {
      if (const Val *v = in.find("bot_rounds"); v && v->type == Val::NUM) s["bot_rounds"] = std::max(0.0, std::min(1000.0, v->num));
      if (const Val *v = in.find("auto_chat"); v && v->type == Val::BOOL) s["auto_chat"] = v->b;
      if (const Val *v = in.find("auto_chat_visible"); v && v->type == Val::BOOL) s["auto_chat_visible"] = v->b;
      if (const Val *v = in.find("pass_enabled"); v && v->type == Val::BOOL) s["pass_enabled"] = v->b;
      if (const Val *v = in.find("web"); v && v->type == Val::BOOL) { s["web"] = v->b; net = true; }
      if (const Val *v = in.find("assist"); v && v->isObj()) s["assist"] = Assist::clean_rules(*v);
    });
    if (net) engine_.apply_netmode();
    engine_.settings_changed();
    http::send_json(fd, 200, room_.settings());
  }

  static std::string bot_of(const std::string &path) { // /api/bot/<id>[/...]
    const std::string rest = path.substr(9);
    return rest.substr(0, rest.find('/'));
  }

  void put_bot(int fd, const http::Request &r) {
    const Val in = jparse(r.body);
    const std::string id = bot_of(r.path);
    if (!in.isObj()) return http::send_error(fd, 400, "JSON erwartet");
    // The name is the one source of truth: the address of the bot's site
    // follows it, and what it was called before keeps leading there.
    std::string rename, slug;
    if (const Val *v = in.find("name"); v && v->type == Val::STR) {
      rename = trim(v->str);
      slug = lower(rename);
      const std::string problem = site::name_problem(rename);
      if (!problem.empty()) return http::send_error(fd, 400, problem);
      const Val all = room_.bots();
      for (auto &o : all.asArr())
        if (jstr(o, "id") != id && (site::slug_of(o) == slug || jstr(o, "id") == slug))
          return http::send_error(fd, 409, "Diesen Namen trägt schon ein anderer Bot.");
    }
    // Which plugins the bot may use, and how freely.
    if (const Val *v = in.find("plugins")) {
      const std::string problem = plugins_.bind(id, *v);
      if (!problem.empty()) return http::send_error(fd, 400, problem);
    }
    bool disabled = false, renamed = false;
    const bool ok = room_.update_bot(id, [&](Val &b) {
      if (!rename.empty() && rename != jstr(b, "name")) {
        const std::string before = site::slug_of(b);
        b["name"] = rename;
        if (before != slug) {
          Val keep = Val::array();
          if (const Val *old = b.find("aliases"); old && old->isArr())
            for (auto &a : old->asArr())
              if (a.asStr() != slug && a.asStr() != before) keep.push(a);
          keep.push(before);
          while (keep.arr->size() > 16) keep.arr->erase(keep.arr->begin());
          b["aliases"] = keep;
          renamed = true;
        }
      }
      if (const Val *v = in.find("enabled"); v && v->type == Val::BOOL) { b["enabled"] = v->b; disabled = !v->b; }
      if (const Val *v = in.find("thinking"); v && v->type == Val::BOOL) {
        b["thinking"] = v->b;
        // Persist the displayed default even if the user only touched the
        // Thinking switch; otherwise "Kurz" is only a visual placeholder.
        if (v->b && jstr(b, "effort").empty()) b["effort"] = "low";
      }
      if (const Val *v = in.find("effort"); v && (v->asStr() == "low" || v->asStr() == "medium" || v->asStr() == "high")) b["effort"] = v->asStr();
      if (const Val *v = in.find("persona"); v && v->type == Val::STR) b["persona"] = clip(v->str, 2000);
    });
    if (!ok) return http::send_error(fd, 404, "Bot nicht gefunden");
    if (renamed) {
      // A name in use belongs to its bot alone, not to another one's past.
      const Val all = room_.bots();
      for (auto &o : all.asArr())
        if (jstr(o, "id") != id)
          room_.update_bot(jstr(o, "id"), [&](Val &b) {
            Val *old = b.find("aliases");
            if (!old || !old->isArr()) return;
            old->arr->erase(std::remove_if(old->arr->begin(), old->arr->end(), [&](const Val &a) { return a.asStr() == slug; }), old->arr->end());
          });
    }
    if (disabled) engine_.stop(id);
    engine_.settings_changed();
    http::send_ok(fd);
  }

  // The owner's plugins (remote MCP servers): connect one, read its tools again, remove it.
  void plugins(int fd, const http::Request &r) {
    const std::string rest = r.path.size() > 13 ? r.path.substr(13) : ""; // after "/api/plugins/"
    Val out = Val::object();
    if (r.method == "POST" && rest.empty()) {
      Val saved;
      const std::string problem = plugins_.save(jparse(r.body), saved);
      if (!problem.empty()) return http::send_error(fd, 400, problem);
      out["plugin"] = saved;
    } else if (r.method == "POST" && ends_with(rest, "/refresh")) {
      Val now;
      if (!plugins_.refresh(rest.substr(0, rest.size() - 8), now)) return http::send_error(fd, 404, "Plugin nicht gefunden");
      out["plugin"] = now;
    } else if (r.method == "DELETE" && !rest.empty()) {
      if (!plugins_.remove(rest)) return http::send_error(fd, 404, "Plugin nicht gefunden");
    } else {
      return http::send_error(fd, 404, "Nicht gefunden");
    }
    engine_.settings_changed(); // the bots' tools follow at their next turn
    http::send_json(fd, 200, out);
  }

  void model_control(int fd, const http::Request &r) {
    const std::string action = jstr(jparse(r.body), "action");
    if (!engine_.model_control(bot_of(r.path), action)) return http::send_error(fd, 400, "Aktion nicht möglich");
    http::send_ok(fd);
  }

  void memory_get(int fd, const http::Request &r) {
    const Val bot = room_.bot(bot_of(r.path));
    if (!bot.isObj()) return http::send_error(fd, 404, "Bot nicht gefunden");
    Val v = Val::object();
    v["memory"] = bot.find("memory") ? *bot.find("memory") : Val::array();
    http::send_json(fd, 200, v);
  }

  // Memories are owned and persisted per bot. The same input rules apply
  // whether it was written by the owner here or remembered by a bot.
  void memory_add(int fd, const http::Request &r) {
    const std::string id = bot_of(r.path);
    if (!room_.bot(id).isObj()) return http::send_error(fd, 404, "Bot nicht gefunden");
    const Val input = jparse(r.body);
    if (!input.isObj() || !input.find("text") || input.find("text")->type != Val::STR)
      return http::send_error(fd, 400, "Text der Erinnerung fehlt");
    std::string text = trim(jstr(input, "text"));
    for (char &c : text) if (c == '\n' || c == '\r' || c == '\t') c = ' ';
    text = trim(text);
    if (text.empty() || text.size() > 500)
      return http::send_error(fd, 400, "Erinnerung muss 1 bis 500 Zeichen lang sein");
    bool full = false, duplicate = false, added = false;
    room_.update_bot(id, [&](Val &b) {
      Val &mem = b["memory"];
      if (!mem.isArr()) mem = Val::array();
      for (const auto &m : mem.asArr()) if (jstr(m, "text") == text) { duplicate = true; return; }
      if (mem.asArr().size() >= 64) { full = true; return; }
      Val entry = Val::object();
      entry["text"] = text;
      mem.push(entry);
      added = true;
    });
    if (duplicate) return http::send_error(fd, 409, "Diese Erinnerung existiert bereits");
    if (full) return http::send_error(fd, 409, "Maximal 64 Erinnerungen je Bot");
    if (!added) return http::send_error(fd, 500, "Erinnerung konnte nicht gespeichert werden");
    // On the next model turn sync_memory appends the new bot-specific note
    // without mutating its stable system prefix or the other bot's context.
    engine_.settings_changed();
    http::send_ok(fd);
  }

  void memory_delete(int fd, const http::Request &r) {
    const std::string id = bot_of(r.path);
    const size_t at = r.path.rfind('/');
    const std::string index = r.path.substr(at + 1);
    bool done = false;
    room_.update_bot(id, [&](Val &b) {
      Val &mem = b["memory"];
      if (!mem.isArr()) return;
      if (index == "all") { mem = Val::array(); done = true; return; }
      const size_t i = (size_t)atoi(index.c_str());
      if (index.find_first_not_of("0123456789") != std::string::npos || i >= mem.arr->size()) return;
      mem.arr->erase(mem.arr->begin() + (long)i);
      done = true;
    });
    if (!done) return http::send_error(fd, 404, "Erinnerung nicht gefunden");
    engine_.settings_changed();
    http::send_ok(fd);
  }

  void system_info(int fd) {
    Val v = Val::object();
    v["version"] = version_;
    v["idol"] = idol_.available();
    v["scene"] = scenes_.available();
    Val backup = read_json(env("MAG_BACKUP_STATUS", "/var/lib/mag-backup/status.json"));
    if (backup.isObj()) {
      Val b = Val::object();
      b["last_success"] = jnum(backup, "last_success");
      b["healthy"] = jbool(backup, "healthy") && now_s() - (int64_t)jnum(backup, "last_success") < 26 * 3600;
      v["backup"] = b;
    }
    http::send_json(fd, 200, v);
  }

  // ---- the bots' browsers (live view) ------------------------------------
  std::string vm_of(const std::string &bot) { return jstr(room_.bot(bot), "vm"); }

  void screen(int fd, const http::Request &r) {
    const std::string vm = vm_of(r.path.substr(12));
    if (!vm::valid_name(vm)) return http::send_error(fd, 404, "Dieser Bot hat keinen Browser");
    const auto res = guests_.browser(vm, "GET", "/screenshot.jpg", "", 12);
    if (!res.ok()) return http::send_error(fd, res.error.empty() ? 502 : 503, res.error.empty() ? "Kein Bild vom Browser" : res.error);
    http::send(fd, 200, "image/jpeg", res.body);
  }

  void browser_action(int fd, const http::Request &r) {
    // /api/browser/<bot>/<action>
    const std::string rest = r.path.substr(13);
    const size_t slash = rest.find('/');
    if (slash == std::string::npos) return http::send_error(fd, 404, "Nicht gefunden");
    const std::string vm = vm_of(rest.substr(0, slash)), action = rest.substr(slash + 1);
    static const std::set<std::string> allowed = {"navigate", "click", "type", "scroll", "back", "forward", "reload", "key", "read", "pointer", "state"};
    if (!vm::valid_name(vm) || !allowed.count(action)) return http::send_error(fd, 404, "Nicht gefunden");
    const auto res = guests_.browser(vm, "POST", "/" + action, r.body.empty() ? "{}" : r.body, 45);
    if (!res.error.empty()) return http::send_error(fd, 503, res.error);
    http::send(fd, (int)res.status, "application/json; charset=utf-8", res.body);
  }

  // ---- the owner's terminal and file view of a bot's VM -------------------
  std::string vm_of_path(const std::string &path) { // /api/vm/<bot>/<what>
    const std::string rest = path.substr(8);
    const std::string vm = vm_of(rest.substr(0, rest.find('/')));
    return vm::valid_name(vm) ? vm : "";
  }

  // One command as user bot, its output streamed as it comes. The last line
  // is a record (0x1e + JSON): exit code and the directory the shell ended
  // in, so the next command starts there. Closing the request stops it.
  void vm_run(int fd, const http::Request &r) {
    const std::string vm = vm_of_path(r.path);
    const Val in = jparse(r.body);
    const std::string command = jstr(in, "command");
    std::string cwd = jstr(in, "cwd");
    if (vm.empty()) return http::send_error(fd, 404, "Dieser Bot hat keine VM");
    if (trim(command).empty() || command.size() > 16000) return http::send_error(fd, 400, "Befehl fehlt oder ist zu lang");
    if (cwd.empty() || cwd[0] != '/' || cwd.size() > 2000) cwd = "~";
    if (!http::write_all(fd, "HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\n"
                             "X-Accel-Buffering: no\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n"))
      return;
    Cancel cancel;
    std::atomic<bool> finished{false};
    // The reader left (Stop, closed sheet): end the command in the VM.
    std::thread watcher([&] {
      while (!finished) {
        pollfd p{fd, POLLIN | POLLRDHUP, 0};
        char byte;
        if (poll(&p, 1, 300) > 0 && ((p.revents & (POLLHUP | POLLRDHUP | POLLERR)) || recv(fd, &byte, 1, MSG_PEEK | MSG_DONTWAIT) == 0)) {
          cancel.stop();
          return;
        }
      }
    });
    std::string where = cwd;
    const std::string script = "trap 'printf \"\\036cwd %s\\n\" \"$PWD\"' EXIT\ncd " + (cwd == "~" ? cwd : shell_quote(cwd)) + " 2>/dev/null || cd ~\n" + command + "\n";
    const auto res = guests_.exec(vm, script, 900, &cancel, [&](const std::string &chunk) {
      std::string text = chunk;
      // The shell's own last word: where it ended. Not part of the output.
      for (size_t at = text.find("\x1e" "cwd "); at != std::string::npos; at = text.find("\x1e" "cwd ")) {
        const size_t end = text.find('\n', at);
        where = trim(text.substr(at + 5, end == std::string::npos ? std::string::npos : end - at - 5));
        text.erase(at, end == std::string::npos ? std::string::npos : end - at + 1);
      }
      if (!text.empty() && !http::write_all(fd, mj::utf8_fix(text))) cancel.stop();
    });
    finished = true;
    watcher.join();
    Val tail = Val::object();
    tail["exit"] = res.code;
    tail["cwd"] = where;
    if (res.cancelled) tail["cancelled"] = true;
    if (res.timed_out) tail["timeout"] = true;
    if (res.failed_to_start) tail["error"] = res.out;
    http::write_all(fd, "\x1e" + mj::dump(tail) + "\n");
  }

  // A script in the VM that prints one JSON document about a path below the
  // bot's home; anything outside is refused there.
  http::ClientResult vm_python(const std::string &vm, const std::string &code, const std::string &path, int timeout_s, size_t cap) {
    http::ClientResult out;
    const std::string script = "exec python3 -c " + shell_quote(
        "import json,os,sys\nhome=os.path.realpath(os.path.expanduser('~'))\np=os.path.realpath(os.path.join(home,sys.argv[1]))\n"
        "if p!=home and not p.startswith(home+os.sep): print(json.dumps({'error':'Außerhalb des Bot-Verzeichnisses'})); sys.exit(0)\n" + code) +
        " " + shell_quote(path);
    const auto res = guests_.exec(vm, script, timeout_s, nullptr, [&](const std::string &chunk) {
      if (out.body.size() + chunk.size() <= cap) out.body += chunk;
      else out.error = "zu groß";
    });
    if (res.failed_to_start || res.timed_out) out.error = res.timed_out ? "Zeitlimit" : res.out;
    else if (res.code != 0 && out.error.empty()) out.error = clip(trim(out.body), 300);
    out.status = out.error.empty() ? 200 : 502;
    return out;
  }

  void vm_files(int fd, const http::Request &r) {
    const std::string vm = vm_of_path(r.path);
    if (vm.empty()) return http::send_error(fd, 404, "Dieser Bot hat keine VM");
    const auto res = vm_python(vm,
        "if not os.path.isdir(p): print(json.dumps({'error':'Kein Verzeichnis'})); sys.exit(0)\n"
        "rows=[]\n"
        "for n in sorted(os.listdir(p), key=str.lower)[:3000]:\n"
        "    try:\n"
        "        st=os.lstat(os.path.join(p,n)); d=os.path.isdir(os.path.join(p,n))\n"
        "        rows.append({'name':n,'dir':d,'size':0 if d else st.st_size,'mtime':int(st.st_mtime)})\n"
        "    except OSError: pass\n"
        "print(json.dumps({'path':p,'home':home,'entries':rows}))\n", r.param("path").empty() ? "." : r.param("path"), 30, 4u << 20);
    if (!res.error.empty()) return http::send_error(fd, 502, "VM: " + res.error);
    const Val v = jparse(res.body);
    if (!v.isObj()) return http::send_error(fd, 502, "VM antwortet nicht verständlich");
    if (!jstr(v, "error").empty()) return http::send_error(fd, 400, jstr(v, "error"));
    http::send_json(fd, 200, v);
  }

  // One file of the VM (at most 48 MB), to look at or to download. It is
  // never run as a page of this origin.
  void vm_file(int fd, const http::Request &r) {
    const std::string vm = vm_of_path(r.path), path = r.param("path");
    if (vm.empty() || path.empty()) return http::send_error(fd, 404, "Datei nicht gefunden");
    const auto res = vm_python(vm,
        "import base64\n"
        "if not os.path.isfile(p): print(json.dumps({'error':'Keine Datei'})); sys.exit(0)\n"
        "if os.path.getsize(p)>48*1024*1024: print(json.dumps({'error':'Größer als 48 MB'})); sys.exit(0)\n"
        "sys.stdout.write('ok\\n'); sys.stdout.flush()\n"
        "with open(p,'rb') as f: sys.stdout.write(base64.b64encode(f.read()).decode())\n", path, 300, 70u << 20);
    if (!res.error.empty()) return http::send_error(fd, 502, "VM: " + res.error);
    if (!starts_with(res.body, "ok\n")) return http::send_error(fd, 400, jstr(jparse(res.body), "error", "Datei nicht lesbar"));
    const std::string bytes = b64_decode(res.body.substr(3));
    const std::string name = path.substr(path.find_last_of('/') == std::string::npos ? 0 : path.find_last_of('/') + 1), mime = mime_for(name);
    const bool show = r.param("download") != "1" && (starts_with(mime, "image/") || starts_with(mime, "text/plain") || mime == "application/pdf");
    http::Headers h = {{"Content-Security-Policy", "sandbox"},
                       {"Content-Disposition", std::string(show ? "inline" : "attachment") + "; filename*=UTF-8''" + url_encode(name)}};
    http::send(fd, 200, r.param("text") == "1" ? "text/plain; charset=utf-8" : mime, r.param("text") == "1" ? mj::utf8_fix(bytes) : bytes, h);
  }

  void vm_delete(int fd, const http::Request &r) {
    const std::string vm = vm_of_path(r.path), path = r.param("path");
    if (vm.empty() || path.empty()) return http::send_error(fd, 404, "Datei nicht gefunden");
    const auto res = vm_python(vm,
        "import shutil\n"
        "if p==home: print(json.dumps({'error':'Das Home-Verzeichnis bleibt'})); sys.exit(0)\n"
        "if os.path.isdir(p) and not os.path.islink(p): shutil.rmtree(p)\n"
        "else: os.remove(p)\n"
        "print(json.dumps({'ok':True,'path':p}))\n", path, 60, 1 << 16);
    if (!res.error.empty()) return http::send_error(fd, 502, "VM: " + res.error);
    const Val v = jparse(res.body);
    if (!jbool(v, "ok")) return http::send_error(fd, 400, jstr(v, "error", "Löschen fehlgeschlagen"));
    // The published site is a separate host-side copy of the VM's www.
    // Removing only the guest file leaves a stale, still-reachable URL.
    // Republish the whole current www tree (also after deleting www itself).
    const std::string deleted = jstr(v, "path");
    const std::string www = env("MAG_ROOM_GUEST_HOME", "/home/bot") + "/www";
    if (deleted == www || starts_with(deleted, www + "/")) {
      const std::string why = site::publish(guests_, vm);
      if (!why.empty()) return http::send_error(fd, 502, "Datei gelöscht, aber Website noch nicht aktualisiert: " + why);
    }
    http::send_ok(fd);
  }

  void live_offer(int fd, const http::Request &r) {
    const std::string vm = vm_of(r.param("bot"));
    if (!vm::valid_name(vm)) return http::send_error(fd, 404, "Dieser Bot hat keinen Browser");
    if (r.body.empty() || r.body.size() > 65536) return http::send_error(fd, 400, "SDP-Angebot fehlt");
    std::string error;
    if (guests_.ensure_browser(vm, error).empty()) return http::send_error(fd, 503, "Browser-Start: " + error);
    http::ClientOptions opt;
    opt.method = "POST";
    opt.body = r.body;
    opt.timeout_s = 15;
    opt.headers.push_back({"Content-Type", "application/json"});
    const auto res = http::fetch(room_.cfg.rtc_url + "/offer?bot=" + vm, opt);
    if (!res.error.empty()) return http::send_error(fd, 503, "Live-Ansicht nicht erreichbar");
    if (res.status != 200) return http::send_error(fd, 502, "Live-Ansicht abgelehnt");
    http::send(fd, 200, "application/json; charset=utf-8", res.body);
  }

  // ---- speech to text (mag-stt, the recognizer service on this host) ------
  // Is the speech service there? Asked at most every few seconds.
  bool stt_ready() {
    static std::mutex mu;
    static int64_t asked = 0;
    static bool ready = false;
    std::lock_guard<std::mutex> lk(mu);
    if (!asked || mono_ms() - asked > 5000) {
      http::ClientOptions opt;
      opt.timeout_s = 2;
      opt.connect_timeout_s = 1;
      ready = http::fetch(room_.cfg.stt_url + "/health", opt).ok();
      asked = mono_ms();
    }
    return ready;
  }
  void stt_answer(int fd, const http::ClientResult &res) {
    if (!res.error.empty() || !res.status) return http::send_error(fd, 503, "Spracherkennung ist nicht erreichbar");
    http::send(fd, (int)res.status, "application/json; charset=utf-8", res.body);
  }

  // A dictation while it is spoken: the page sends 16 kHz PCM chunk by chunk
  // and reads the text so far from every answer.
  void stt_live(int fd, const http::Request &r) {
    const std::string sid = r.param("sid");
    if (sid.size() < 8 || sid.size() > 64 || !std::all_of(sid.begin(), sid.end(), [](unsigned char c) { return isalnum(c); }))
      return http::send_error(fd, 400, "Ungültige Diktat-Kennung");
    http::ClientOptions opt;
    opt.method = r.method;
    opt.timeout_s = 100;
    std::string url = room_.cfg.stt_url + "/v1/stream/" + sid;
    if (r.method == "POST") {
      if (r.body.size() > (8u << 20)) return http::send_error(fd, 413, "Zu viel Audio in einem Stück");
      opt.body = r.body;
      opt.headers.push_back({"Content-Type", "application/octet-stream"});
      url += "?seq=" + std::to_string(atoi(r.param("seq").c_str())) + "&lang=" + url_encode(r.param("lang")) + (r.param("end") == "1" ? "&end=1" : "");
    }
    stt_answer(fd, http::fetch(url, opt));
  }

  // A finished recording in any format ffmpeg reads.
  void stt(int fd, const http::Request &r) {
    if (r.body.size() < 200) return http::send_error(fd, 400, "Keine Aufnahme");
    const std::string base = room_.cfg.state_dir + "/uploads/stt-" + random_hex(8);
    struct Cleanup {
      std::string base;
      ~Cleanup() { for (const char *ext : {".audio", ".pcm"}) unlink((base + ext).c_str()); }
    } cleanup{base};
    if (!write_atomic(base + ".audio", r.body, 0600)) return http::send_error(fd, 500, "Aufnahme konnte nicht gespeichert werden");
    RunOptions run_opt;
    run_opt.timeout_s = 120;
    if (run({"ffmpeg", "-nostdin", "-v", "error", "-y", "-i", base + ".audio", "-ar", "16000", "-ac", "1", "-f", "s16le", base + ".pcm"}, run_opt).code != 0)
      return http::send_error(fd, 415, "Aufnahmeformat wird nicht unterstützt");
    http::ClientOptions opt;
    opt.method = "POST";
    opt.timeout_s = 600;
    opt.headers.push_back({"Content-Type", "application/octet-stream"});
    if (!read_file(base + ".pcm", opt.body, 40u << 20)) return http::send_error(fd, 413, "Die Aufnahme ist zu lang");
    stt_answer(fd, http::fetch(room_.cfg.stt_url + "/v1/transcribe?lang=" + url_encode(r.param("lang")), opt));
  }

  // ---- bridge (192.168.122.1:8767): only for the bot VMs -----------------
  void bridge(int fd, http::Request &r) {
    std::string token = r.param("token");
    const std::string authz = r.header("authorization");
    if (token.empty() && starts_with(authz, "Bearer ")) token = authz.substr(7);
    std::vector<std::string> vms;
    const Val bots = room_.bots();
    for (auto &b : bots.asArr())
      if (vm::valid_name(jstr(b, "vm"))) vms.push_back(jstr(b, "vm"));
    const std::string vm = guests_.vm_for_token(token, vms);
    if (vm.empty()) return http::send_error(fd, 401, "Bridge token required");
    std::string bot;
    for (auto &b : bots.asArr())
      if (jstr(b, "vm") == vm) bot = jstr(b, "id");
    if (r.method == "GET" && r.path == "/tool/mag-browser")
      return http::send_file(fd, r, room_.cfg.browser_binary, "application/octet-stream");
    if (r.method == "GET" && starts_with(r.path, "/files/")) {
      const std::string stored = url_decode(r.path.substr(7));
      std::string path = room_.file_path(stored);
      if (path.empty() || !exists(path)) return http::send_error(fd, 404, "Unknown file");
      // ?text=1: what the document says, as plain text (made by the engine).
      if (r.param("text") == "1") path = room_.cfg.state_dir + "/text/" + stored + ".txt";
      if (!exists(path)) return http::send_error(fd, 404, "No text");
      return http::send_file(fd, r, path, "application/octet-stream");
    }
    if (r.method == "POST" && r.path == "/ingest") {
      if (r.body.empty()) return http::send_error(fd, 400, "Empty file");
      // Which bot this is follows from the token, not from anything it says.
      const Val att = room_.store_file(r.param("name").empty() ? "datei" : r.param("name"), r.body, bot);
      if (!att.isObj()) return http::send_error(fd, 500, "Store failed");
      engine_.attach_to_current(bot, att, "");
      return http::send_json(fd, 201, att);
    }
    http::send_error(fd, 403, "Route not available on VM bridge");
  }
};

} // namespace mag
