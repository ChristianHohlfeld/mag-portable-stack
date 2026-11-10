// 3D scenes (Blender, mag-scene) as ordinary room messages.
// A bot describes a scene: which 3D models from the chat play in it and what
// they do. The scene worker builds it while the call waits, so a wrong
// description comes back as words the bot can act on. The built scene is a
// message of its own right away (an animated GLB the chat plays); its film is
// rendered afterwards and joins the same message. Like a 3D result the
// message is stored, deletable and survives restarts on both sides.
#pragma once
#include "http.hpp"
#include "room.hpp"

#include <thread>

namespace mag {

class Scenes {
public:
  explicit Scenes(Room &room) : room_(room) {}

  static bool is_scene(const Val &message) {
    const Val *m3 = message.find("model3d");
    return m3 && jstr(*m3, "kind") == "scene";
  }

  bool available() {
    http::ClientOptions opt;
    opt.timeout_s = 3;
    opt.connect_timeout_s = 2;
    const auto res = http::fetch(room_.cfg.scene_url + "/api/scene/health", opt);
    return res.status == 200 && jbool(jparse(res.body), "ok");
  }

  // What the bots are told about the models in the chat: where each one
  // comes from and which figures it shows.
  std::string models_text(const Cancel *cancel = nullptr) {
    std::string out;
    int count = 0;
    const auto all = room_.all();
    for (auto it = all.rbegin(); it != all.rend() && count < 8; ++it) {
      const Val *atts = it->find("attachments");
      if (is_scene(*it) || !atts || !atts->isArr()) continue;
      for (auto &a : atts->asArr()) {
        if (!ends_with(lower(jstr(a, "name")), ".glb")) continue;
        out += std::to_string(++count) + ". " + jstr(a, "name");
        if (const Val *m3 = it->find("model3d")) {
          out += " (aus dem Foto " + jstr(*m3, "source", "?");
          const std::string seen = picture(jstr(*m3, "image"));
          out += seen.empty() ? ")" : "; darauf: " + clip(seen, 300, "…") + ")";
        } else {
          out += " (hochgeladen)";
        }
        std::string error;
        const Val parts = parts_of(jstr(a, "file"), error, cancel);
        if (!parts.isArr() || parts.asArr().empty()) out += ": " + error + "\n";
        else if (parts.size() == 1) out += " zeigt eine Figur (" + figure(parts.asArr()[0]) + "); part ist nicht nötig.\n";
        else {
          out += " zeigt " + std::to_string(parts.size()) + " Figuren, jede kann ein eigener Darsteller sein:\n";
          for (auto &p : parts.asArr()) out += "   part " + std::to_string((int)jnum(p, "part")) + ": " + figure(p) + "\n";
        }
      }
    }
    if (out.empty()) return "Im Chat gibt es noch kein 3D-Modell. Chris erstellt eines aus einem Foto (Plus-Knopf, „Foto zu 3D“).";
    return "3D-Modelle im Chat, das neueste zuerst:\n" + out;
  }

  // Builds the scene a bot described (see ops/scene/scene.py) and posts it.
  // Returns the text for the bot; `ok` says whether there is a scene now.
  std::string start(Val spec, const std::string &by, bool &ok, const Cancel *cancel = nullptr) {
    ok = false;
    Val *actors = spec.find("actors");
    if (!actors || !actors->isArr() || actors->asArr().empty()) return "Eine Szene braucht Darsteller: actors = [{name, model, part}].";
    for (auto &actor : *actors->arr) {
      if (!actor.isObj()) return "Jeder Darsteller ist ein Objekt mit name und model.";
      const Val file = model(jstr(actor, "model"));
      if (!file.isObj()) return "Das Modell „" + jstr(actor, "model") + "“ gibt es im Chat nicht. " + models_text(cancel);
      actor["file"] = jstr(file, "file");
      actor["model"] = jstr(file, "name");
    }
    http::ClientOptions opt = options("POST");
    opt.body = mj::dump(spec);
    opt.headers.push_back({"Content-Type", "application/json"});
    opt.timeout_s = 240;
    opt.cancel = cancel;
    const auto res = http::fetch(room_.cfg.scene_url + "/api/scene/jobs", opt);
    const Val job = jparse(res.body);
    if (!res.error.empty() || !res.status) return "Der Szenen-Dienst (Blender) ist nicht erreichbar.";
    if (res.status != 201 || jstr(job, "id").empty()) return "Die Szene wurde nicht gebaut: " + clip(jstr(job, "error", "unbekannter Fehler"), 1500, "…");
    const std::string id = jstr(job, "id"), base = "szene-" + id.substr(0, 6);
    const Val glb = fetch_result(id, "scene.glb", base + ".glb");
    if (!glb.isObj()) {
      http::fetch(room_.cfg.scene_url + "/api/scene/jobs/" + id, options("DELETE"));
      return "Die Szene wurde gebaut, konnte aber nicht in den Chat übernommen werden.";
    }
    // Who plays, and what each one turned out to be (an animal on four legs, a thing ...).
    std::string names;
    Val cast = Val::array(), kinds = Val::array();
    if (const Val *list = job.find("actors"); list && list->isArr())
      for (auto &a : list->asArr()) {
        cast.push(jstr(a, "name"));
        kinds.push(jstr(a, "kind"));
        const std::string what = jstr(a, "notes", jstr(a, "kind"));
        names += (names.empty() ? "" : "; ") + jstr(a, "name") + (what.empty() ? "" : " (" + what + ")");
      }
    const std::string title = jstr(job, "title", "Szene");
    Val m = Val::object();
    m["from"] = "idol";
    m["role"] = "service";
    m["state"] = "streaming"; // until its film is there
    m["text"] = "3D-Szene: " + title;
    Val atts = Val::array();
    atts.push(glb);
    m["attachments"] = atts;
    Val m3 = Val::object();
    m3["kind"] = "scene";
    m3["job"] = id;
    m3["state"] = "queued";
    m3["stage"] = "Film wartet";
    m3["progress"] = 0;
    m3["title"] = title;
    m3["seconds"] = jnum(job, "seconds");
    m3["cast"] = cast;
    m3["kinds"] = kinds;
    m3["by"] = by;
    m3["glb"] = jstr(glb, "file");
    m3["started"] = jnum(job, "started");
    m3["phases"] = jnum(job, "phases", 3);
    m["model3d"] = m3;
    watch(jstr(room_.add(m), "id"), id);
    ok = true;
    char seconds[32];
    snprintf(seconds, sizeof seconds, "%.1f", jnum(job, "seconds"));
    return "Die Szene „" + title + "“ steht jetzt im Chat (" + seconds + " Sekunden; Darsteller: " + names +
           "). Chris kann sie dort von allen Seiten ansehen und abspielen; ihr Film wird noch gerendert und erscheint in derselben Nachricht. "
           "Baue sie nicht noch einmal, außer Chris möchte etwas anders haben.";
  }

  void cancel(const Val &message) {
    if (is_scene(message)) http::fetch(room_.cfg.scene_url + "/api/scene/jobs/" + jstr(*message.find("model3d"), "job") + "/cancel", options("POST"));
  }
  // The film failed or was cancelled: render it again (what was rendered stays).
  bool retry(const Val &message) {
    if (!is_scene(message) || jstr(message, "state") == "streaming") return false;
    const std::string job = jstr(*message.find("model3d"), "job");
    const auto res = http::fetch(room_.cfg.scene_url + "/api/scene/jobs/" + job + "/retry", options("POST"));
    if (res.status != 200 || !jbool(jparse(res.body), "queued")) return false;
    room_.update(jstr(message, "id"), [&](Val &msg) {
      msg["state"] = "streaming";
      Val &m3 = msg["model3d"];
      m3["state"] = "queued";
      m3["stage"] = "Film wartet";
      m3["started"] = (double)now_s();
      m3.obj->erase(std::remove_if(m3.obj->begin(), m3.obj->end(), [](const auto &kv) { return kv.first == "error" || kv.first == "eta"; }), m3.obj->end());
    });
    watch(jstr(message, "id"), job);
    return true;
  }
  // The message is gone: remove what the worker keeps for it.
  void forget(const Val &removed) {
    if (!is_scene(removed)) return;
    const std::string job = jstr(*removed.find("model3d"), "job");
    std::thread([this, job] { http::fetch(room_.cfg.scene_url + "/api/scene/jobs/" + job, options("DELETE")); }).detach();
  }
  // The room was emptied: no scene has a message any more.
  void purge() {
    std::thread([this] { http::fetch(room_.cfg.scene_url + "/api/scene/purge", options("POST")); }).detach();
  }
  // The room restarted; the worker and its films did not. Follow them again.
  void resume() {
    for (auto &m : room_.all())
      if (is_scene(m) && jstr(m, "state") == "streaming") watch(jstr(m, "id"), jstr(*m.find("model3d"), "job"));
  }

private:
  Room &room_;

  static http::ClientOptions options(const std::string &method) {
    http::ClientOptions opt;
    opt.method = method;
    opt.timeout_s = 20;
    opt.connect_timeout_s = 3;
    opt.headers.push_back({"X-Mag-Scene-UI", "1"});
    return opt;
  }

  // A 3D model in the chat by the name the chat shows ("3d-0d4b6d.glb"), the
  // newest first; the name may come without its ending or as a part of it.
  // No name: the newest model. Scenes themselves are not models.
  Val model(const std::string &name) {
    std::string want = lower(trim(name));
    if (ends_with(want, ".glb")) want.resize(want.size() - 4);
    const auto all = room_.all();
    for (int pass = 0; pass < 2; ++pass)
      for (auto it = all.rbegin(); it != all.rend(); ++it) {
        const Val *atts = it->find("attachments");
        if (is_scene(*it) || !atts || !atts->isArr()) continue;
        for (auto &a : atts->asArr()) {
          std::string n = lower(jstr(a, "name"));
          if (!ends_with(n, ".glb")) continue;
          n.resize(n.size() - 4);
          if (want.empty() || (pass == 0 ? n == want : n.find(want) != std::string::npos)) return a;
        }
      }
    return Val();
  }

  static std::string figure(const Val &p) {
    char text[200];
    snprintf(text, sizeof text, "%s, %.2f hoch, %.2f lang", jstr(p, "shape").c_str(), jnum(p, "height"), jnum(p, "length"));
    const std::string side = jstr(p, "side"), kind = jstr(p, "kind");
    return (kind.empty() ? "" : kind + ", ") + text + (side.empty() ? std::string() : ", steht im Bild " + side);
  }
  // What a bot with eyes wrote down about a picture in the chat.
  std::string picture(const std::string &stored) {
    if (stored.empty()) return "";
    for (auto &m : room_.all())
      if (const Val *atts = m.find("attachments"); atts && atts->isArr())
        for (auto &a : atts->asArr())
          if (jstr(a, "file") == stored) return jstr(a, "description");
    return "";
  }
  // The figures of one model file, as the worker tells them apart.
  Val parts_of(const std::string &stored, std::string &error, const Cancel *cancel) {
    http::ClientOptions opt = options("POST");
    opt.timeout_s = 200;
    opt.cancel = cancel;
    const auto res = http::fetch(room_.cfg.scene_url + "/api/scene/parts?file=" + url_encode(stored), opt);
    const Val out = jparse(res.body);
    if (res.status == 200 && out.find("parts")) return *out.find("parts");
    error = res.error.empty() && res.status ? jstr(out, "error", "Die Figuren ließen sich nicht bestimmen.") : "Der Szenen-Dienst (Blender) ist nicht erreichbar.";
    return Val();
  }

  // Streams one result file of the worker into the room's file store.
  Val fetch_result(const std::string &job, const std::string &remote, const std::string &name) {
    const std::string tmp = room_.cfg.state_dir + "/uploads/scene-" + random_hex(8);
    const int fd = open(tmp.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0640);
    if (fd < 0) return Val();
    http::ClientOptions opt = options("GET");
    opt.timeout_s = 600;
    bool ok = true;
    opt.on_data = [&](const char *data, size_t n) {
      ok = ok && http::write_file_all(fd, data, n);
      return ok;
    };
    const auto res = http::fetch(room_.cfg.scene_url + "/api/scene/jobs/" + job + "/" + remote, opt);
    close(fd);
    if (!ok || res.status != 200 || !res.error.empty()) { unlink(tmp.c_str()); return Val(); }
    return room_.adopt_file(tmp, name, "idol");
  }

  // The film did not come about. The scene itself is in the message and stays playable.
  void finish(const std::string &message_id, const std::string &state, const std::string &error) {
    room_.update(message_id, [&](Val &msg) {
      msg["state"] = state == "cancelled" ? "stopped" : "error";
      Val &m3 = msg["model3d"];
      m3["state"] = state;
      m3["error"] = error;
      m3["stage"] = state == "cancelled" ? "Abgebrochen" : "Fehler";
    });
  }

  // Follows the film of one scene and mirrors it into its message.
  void watch(const std::string &message_id, const std::string &job) {
    std::thread([this, message_id, job] {
      std::string last;
      int failures = 0;
      for (;;) {
        std::this_thread::sleep_for(std::chrono::milliseconds(last.empty() && !failures ? 300 : 1500));
        Val m;
        if (!room_.get(message_id, m)) return; // deleted: forget() cleans up
        http::ClientOptions opt = options("GET");
        opt.timeout_s = 5;
        const auto res = http::fetch(room_.cfg.scene_url + "/api/scene/jobs/" + job, opt);
        const Val st = jparse(res.body);
        if (res.status == 404) { finish(message_id, "error", "Den Film-Auftrag gibt es nicht mehr."); return; }
        if (res.status != 200 || !st.isObj()) {
          // The worker is restarting or briefly away: its render goes on, and so does the message.
          if (++failures > 1200) { finish(message_id, "error", "Der Szenen-Dienst antwortet nicht mehr."); return; }
          continue;
        }
        failures = 0;
        const std::string state = jstr(st, "state");
        const std::string now = state + "|" + jstr(st, "stage") + "|" + std::to_string((int)jnum(st, "step"));
        if (now != last && (state == "queued" || state == "running")) {
          last = now;
          room_.update(message_id, [&](Val &msg) {
            Val &m3 = msg["model3d"];
            m3["state"] = state;
            m3["stage"] = jstr(st, "stage");
            m3["phase"] = jnum(st, "phase");
            m3["phases"] = jnum(st, "phases");
            m3["step"] = jnum(st, "step");
            m3["steps"] = jnum(st, "total_steps");
            m3["progress"] = jnum(st, "progress");
            m3["eta"] = jnum(st, "eta");
            m3["eta_at"] = (double)now_s();
            if (jnum(st, "resumed") > 0) m3["resumed"] = true;
          }, false);
        }
        if (state == "done") {
          const std::string base = "szene-" + job.substr(0, 6);
          const Val film = fetch_result(job, "scene.mp4", base + ".mp4");
          if (!film.isObj()) { finish(message_id, "error", "Der Film konnte nicht übernommen werden."); return; }
          const Val blend = fetch_result(job, "scene.blend", base + ".blend");
          room_.update(message_id, [&](Val &msg) {
            msg["attachments"].push(film);
            if (blend.isObj()) msg["attachments"].push(blend);
            msg["state"] = "done";
            Val &m3 = msg["model3d"];
            m3["state"] = "done";
            m3["stage"] = "Fertig";
            m3["progress"] = 1;
            m3["film"] = jstr(film, "file");
            if (jnum(st, "started") > 0 && jnum(st, "finished") >= jnum(st, "started")) m3["took"] = jnum(st, "finished") - jnum(st, "started");
          });
          return;
        }
        if (state == "error" || state == "cancelled") {
          finish(message_id, state, jstr(st, "error", state == "cancelled" ? "Abgebrochen" : "Fehler"));
          return;
        }
      }
    }).detach();
  }
};

} // namespace mag
