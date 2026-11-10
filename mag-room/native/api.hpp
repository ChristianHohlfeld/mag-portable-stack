// HTTP routes of the room: the page and its assets, the JSON API, the event
// stream, and the small bridge the bot VMs may reach.
#pragma once
#include "auth.hpp"
#include "engine.hpp"
#include "idol.hpp"
#include "push.hpp"

namespace mag {

class Api {
public:
  Api(Room &room, Engine &engine, vm::Guests &guests, Idol &idol, auth::Gate &gate, Push &push)
      : room_(room), engine_(engine), guests_(guests), idol_(idol), gate_(gate), push_(push) {
    // The version is the cache key of every asset: it must change with each
    // release. A release carries its commit; outside a release (tests,
    // development) the environment or the start time stands in.
    version_ = trim(slurp(room_.cfg.web_dir + "/../SOURCE_COMMIT")).substr(0, 12);
    if (version_.empty()) version_ = env("MAG_ROOM_VERSION");
    if (version_.empty()) version_ = "dev" + std::to_string(now_s());
  }

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
      if (starts_with(p, "/static/") || starts_with(p, "/app/")) return static_file(fd, r);
      if (p == "/api/version") { Val v = Val::object(); v["version"] = version_; return http::send_json(fd, 200, v); }
      if (p == "/api/state") return state(fd, r, who);
      if (p == "/api/messages") return messages(fd, r);
      if (p == "/api/events") return events(fd);
      if (starts_with(p, "/api/file/")) return file(fd, r);
      if (starts_with(p, "/api/screen/")) return screen(fd, r);
      if (p == "/api/push/vapid") return http::send_json(fd, 200, push_.vapid());
      if (starts_with(p, "/api/bot/") && ends_with(p, "/memory")) return memory_get(fd, r);
      if (p == "/api/system") return system_info(fd);
      if (starts_with(p, "/api/vm/")) {
        if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
        if (ends_with(p, "/files")) return vm_files(fd, r);
        if (ends_with(p, "/file")) return vm_file(fd, r);
      }
      return http::send_error(fd, 404, "Nicht gefunden");
    }

    // Everything below changes something.
    if (r.method == "POST" && p == "/api/message") return post_message(fd, r, who);
    if (r.method == "POST" && p == "/api/stop") return post_stop(fd, r);
    if (r.method == "POST" && p == "/api/upload") return upload(fd, r);
    if (r.method == "POST" && p == "/api/stt") return stt(fd, r);
    if ((r.method == "POST" || r.method == "DELETE") && p == "/api/stt/live") return stt_live(fd, r);
    if (r.method == "POST" && starts_with(p, "/api/3d/") && ends_with(p, "/cancel")) {
      idol_.cancel(p.substr(8, p.size() - 8 - 7));
      return http::send_ok(fd);
    }
    if (r.method == "POST" && p == "/api/browser-live/offer") return live_offer(fd, r);
    if (r.method == "POST" && starts_with(p, "/api/browser/")) return browser_action(fd, r);
    if (r.method == "POST" && p == "/api/push/subscribe") return push_.subscribe(fd, r.body);
    if (r.method == "DELETE" && p == "/api/push/subscribe") return push_.unsubscribe(fd, r.body);

    // Destructive or configuring: the owner only.
    if (!who.owner()) return http::send_error(fd, 403, "Nur für den Besitzer");
    if (r.method == "POST" && p == "/api/room/clear") return clear(fd);
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
    if (r.method == "DELETE" && starts_with(p, "/api/bot/") && p.find("/memory/") != std::string::npos) return memory_delete(fd, r);
    // The owner's own look into a bot's VM: a terminal and its files.
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
  auth::Gate &gate_;
  Push &push_;
  std::string version_;

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
    replace_all(html, "{{V}}", version_);
    const std::string meta = "<meta name=\"mag-token\" content=\"" + gate_.ui_token() + "\">\n<meta name=\"mag-version\" content=\"" + version_ + "\">";
    const size_t head = html.find("<head>");
    if (head != std::string::npos) html.insert(head + 6, "\n" + meta);
    http::send(fd, 200, "text/html; charset=utf-8", html, page_headers());
  }

  void asset(int fd, const http::Request &r, const std::string &path, bool templated, http::Headers extra = {}) {
    extra.push_back({"Cross-Origin-Resource-Policy", "same-origin"});
    if (templated) {
      std::string body = slurp(path);
      if (body.empty()) return http::send_error(fd, 404, "Nicht gefunden");
      replace_all(body, "{{V}}", version_);
      return http::send(fd, 200, mime_for(path), body, extra);
    }
    extra.push_back({"Cache-Control", r.param("v") == version_ ? "public, max-age=31536000, immutable" : "no-cache"});
    http::send_file(fd, r, path, mime_for(path), extra);
  }

  void static_file(int fd, const http::Request &r) {
    const std::string rel = url_decode(r.path);
    if (rel.find("..") != std::string::npos || rel.find('\\') != std::string::npos || rel.find('\0') != std::string::npos)
      return http::send_error(fd, 404, "Nicht gefunden");
    asset(fd, r, room_.cfg.web_dir + rel, false);
  }

  void manifest(int fd) {
    const std::string v = "?v=" + version_;
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
    if (mode == "3d") {
      // A 3D order is for the worker: the bots read it as context, it asks them nothing.
      m["silent"] = true;
      m["order3d"] = jstr(in, "quality", "normal");
    }
    const Val stored = room_.add(m);
    Val out = Val::object();
    out["message"] = stored;
    if (mode == "3d") {
      const Val *image = nullptr;
      for (auto &a : atts.asArr())
        if (jstr(a, "kind") == "image") { image = &a; break; }
      std::string error;
      if (!image) error = "Für Foto zu 3D wird ein Bild gebraucht.";
      else out["model3d"] = idol_.start(*image, jstr(in, "quality", "normal"), error);
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
    } else {
      engine_.on_human(stored, to);
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
    idol_.forget(removed);
    engine_.forget(id);
    http::send_ok(fd);
  }

  void clear(int fd) {
    std::vector<Val> removed;
    Val report = engine_.clear(&removed);
    idol_.purge();
    Val v = Val::object();
    v["ok"] = true;
    v["removed"] = (double)removed.size();
    v["bots"] = report;
    http::send_json(fd, 200, v);
  }

  // ---- files --------------------------------------------------------------
  void upload(int fd, const http::Request &r) {
    if (r.body.empty()) return http::send_error(fd, 400, "Leere Datei");
    std::string name = r.param("name");
    if (name.empty()) name = "datei";
    const Val att = room_.store_file(name, r.body, "chris");
    if (!att.isObj()) return http::send_error(fd, 500, "Datei konnte nicht gespeichert werden");
    room_.stage(att);
    http::send_json(fd, 201, att);
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
                       {"Content-Disposition", std::string(r.param("download") == "1" || (active && r.param("view") != "1") ? "attachment" : "inline") +
                                                   "; filename*=UTF-8''" + url_encode(name)}};
    http::send_file(fd, r, path, mime, h);
  }

  // ---- the bots' sites ----------------------------------------------------
  // Static files of the www folder the bot's VM published, served under the
  // bot's current name. An earlier name, the bot's id or its VM name redirect
  // there (temporarily: a name can change again).
  void bot_site(int fd, const http::Request &r, const std::string &name) {
    static const http::Headers open = {{"Access-Control-Allow-Origin", "*"}, {"Cache-Control", "no-cache"}};
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
      if (const Val *v = in.find("web"); v && v->type == Val::BOOL) { s["web"] = v->b; net = true; }
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
      if (const Val *v = in.find("thinking"); v && v->type == Val::BOOL) b["thinking"] = v->b;
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
        "print(json.dumps({'ok':True}))\n", path, 60, 1 << 16);
    if (!res.error.empty()) return http::send_error(fd, 502, "VM: " + res.error);
    const Val v = jparse(res.body);
    if (!jbool(v, "ok")) return http::send_error(fd, 400, jstr(v, "error", "Löschen fehlgeschlagen"));
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
      const std::string path = room_.file_path(url_decode(r.path.substr(7)));
      if (path.empty() || !exists(path)) return http::send_error(fd, 404, "Unknown file");
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
