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
      return http::send_error(fd, 404, "Nicht gefunden");
    }

    // Everything below changes something.
    if (r.method == "POST" && p == "/api/message") return post_message(fd, r, who);
    if (r.method == "POST" && p == "/api/stop") return post_stop(fd, r);
    if (r.method == "POST" && p == "/api/upload") return upload(fd, r);
    if (r.method == "POST" && p == "/api/stt") return stt(fd, r);
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
    features["stt"] = exists(room_.cfg.whisper_cli) && exists(room_.cfg.whisper_model);
    features["push"] = push_.configured();
    v["features"] = features;
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
    bool disabled = false;
    const bool ok = room_.update_bot(id, [&](Val &b) {
      if (const Val *v = in.find("enabled"); v && v->type == Val::BOOL) { b["enabled"] = v->b; disabled = !v->b; }
      if (const Val *v = in.find("thinking"); v && v->type == Val::BOOL) b["thinking"] = v->b;
      if (const Val *v = in.find("effort"); v && (v->asStr() == "low" || v->asStr() == "medium" || v->asStr() == "high")) b["effort"] = v->asStr();
      if (const Val *v = in.find("persona"); v && v->type == Val::STR) b["persona"] = clip(v->str, 2000);
    });
    if (!ok) return http::send_error(fd, 404, "Bot nicht gefunden");
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

  // ---- speech to text (whisper.cpp on the host) ---------------------------
  void stt(int fd, const http::Request &r) {
    if (r.body.size() < 200) return http::send_error(fd, 400, "Keine Aufnahme");
    if (!exists(room_.cfg.whisper_cli) || !exists(room_.cfg.whisper_model)) return http::send_error(fd, 503, "Spracherkennung ist nicht installiert");
    const std::string base = room_.cfg.state_dir + "/uploads/stt-" + random_hex(8);
    struct Cleanup {
      std::string base;
      ~Cleanup() { for (const char *ext : {".audio", ".wav", ".txt"}) unlink((base + ext).c_str()); }
    } cleanup{base};
    if (!write_atomic(base + ".audio", r.body, 0600)) return http::send_error(fd, 500, "Aufnahme konnte nicht gespeichert werden");
    RunOptions opt;
    opt.timeout_s = 60;
    if (run({"ffmpeg", "-nostdin", "-v", "error", "-y", "-i", base + ".audio", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", base + ".wav"}, opt).code != 0)
      return http::send_error(fd, 415, "Aufnahmeformat wird nicht unterstützt");
    opt.timeout_s = 300;
    std::string lang = r.param("lang");
    if (lang != "en" && lang != "auto") lang = "de";
    const RunResult res = run({room_.cfg.whisper_cli, "-m", room_.cfg.whisper_model, "-f", base + ".wav", "-l", lang, "-t", "8", "-otxt", "-of", base, "-nt"}, opt);
    if (res.code != 0) return http::send_error(fd, 500, "Spracherkennung fehlgeschlagen");
    Val v = Val::object();
    v["text"] = trim(slurp(base + ".txt"));
    http::send_json(fd, 200, v);
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
