// Photo to 3D (MAG Idol on the MI50) as ordinary room messages.
// A job is one message from "idol": it shows the progress while the worker
// runs and carries the results (GLB, STL, PLY) as attachments afterwards, so
// a 3D result is stored, ordered, reloadable and deletable like any message.
// Deleting the message also deletes the job directory of the worker.
#pragma once
#include "http.hpp"
#include "room.hpp"

#include <cmath>
#include <functional>
#include <set>
#include <thread>

namespace mag {

class Idol {
public:
  explicit Idol(Room &room) : room_(room) {}

  // Starts a job for an image that is already a room file. Returns the id of
  // the 3D message, or "" with `error`.
  // `scene`: the photo's whole room is being made, one model per object;
  // this is one of them, with where it stands (see scene_part()).
  // `into`: a message that already stands for the order; it becomes the
  // job's message instead of a new one.
  std::string start(const Val &image, const std::string &quality, std::string &error, const Val &scene = Val(), const std::string &into = "") {
    if (quality != "fast" && quality != "normal" && quality != "high") { error = "Qualität muss fast, normal oder high sein."; return ""; }
    const std::string path = room_.file_path(jstr(image, "file"));
    std::string bytes;
    if (path.empty() || !read_file(path, bytes, 20u << 20) || bytes.size() < 8) { error = "Bild fehlt oder ist größer als 20 MB."; return ""; }
    http::ClientOptions opt = options("POST");
    opt.body = bytes;
    opt.headers.push_back({"Content-Type", "application/octet-stream"});
    opt.timeout_s = 60;
    const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/jobs?quality=" + quality, opt);
    const Val job = jparse(res.body);
    if (res.status != 202 || jstr(job, "id").empty()) {
      error = res.error.empty() ? jstr(job, "error", "3D-Dienst hat den Auftrag abgelehnt.") : "3D-Dienst nicht erreichbar.";
      return "";
    }
    Val m = Val::object();
    m["from"] = "idol";
    m["role"] = "service";
    m["state"] = "streaming";
    m["text"] = "";
    m["attachments"] = Val::array();
    Val m3 = Val::object();
    m3["job"] = jstr(job, "id");
    m3["state"] = "queued";
    m3["stage"] = "Warteschlange";
    m3["progress"] = 0;
    m3["quality"] = quality;
    m3["source"] = jstr(image, "name");
    m3["image"] = jstr(image, "file"); // the photo it is made from (a room file)
    if (scene.isObj()) m3["scene"] = scene;
    m["model3d"] = m3;
    std::string id = into;
    if (into.empty() || !room_.update(into, [&](Val &msg) { msg["model3d"] = m3; msg["state"] = "streaming"; })) id = jstr(room_.add(m), "id");
    watch(id, jstr(job, "id"));
    return id;
  }

  // A job of this worker: a model, a picture from words (kind "image") or a
  // video from a picture (kind "video"). (A scene is a 3D message too; it
  // belongs to Scenes.)
  static const Val *job_of(const Val &message) {
    const Val *m3 = message.find("model3d");
    const std::string kind = m3 ? jstr(*m3, "kind") : "";
    return m3 && !jstr(*m3, "job").empty() && (kind.empty() || kind == "image" || kind == "video") ? m3 : nullptr;
  }

  // A video with its sound (HyperFlow H3 Flash, a few-step MiniMax H3
  // licensed to Chris personally, on the MI50 in the worker's one queue):
  // 5 or 10 `seconds` of what `prompt` says happens and is heard. A picture
  // of the chat is its first frame or (use "ref") who or what is in it;
  // without one (`image` not an object) the words alone make it. The message
  // is there at once with its place in the line and its progress, and
  // becomes the video, marked as made by AI with MiniMax H3. Returns the
  // message id, or "" with `error`.
  std::string film(const Val &image, const std::string &prompt, const std::string &by, std::string &error,
                   int seconds = 5, const std::string &use = "first") {
    std::string bytes;
    if (image.isObj()) {
      const std::string path = room_.file_path(jstr(image, "file"));
      if (path.empty() || !read_file(path, bytes, 20u << 20) || bytes.size() < 8) { error = "Bild fehlt oder ist größer als 20 MB."; return ""; }
    }
    const std::string said = clip(trim(prompt), 1500, "");
    if (said.empty()) { error = "Was soll im Video geschehen?"; return ""; }
    http::ClientOptions opt = options("POST");
    opt.body = bytes;
    opt.headers.push_back({"Content-Type", "application/octet-stream"});
    opt.timeout_s = 60;
    std::string ask = "/api/idol/videos?prompt=" + url_encode(said) + "&seed=" + std::to_string(random_bytes(2)[0] * 256 + random_bytes(2)[1] + 1);
    ask += "&seconds=" + std::string(seconds < 10 ? "5" : "10") + "&use=" + (use == "ref" ? "ref" : "first");
    const auto res = http::fetch(room_.cfg.idol_url + ask, opt);
    const Val job = jparse(res.body);
    if (res.status != 202 || jstr(job, "id").empty()) {
      error = res.error.empty() ? jstr(job, "error", "Der Videodienst hat den Auftrag abgelehnt.") : "3D-Dienst nicht erreichbar.";
      return "";
    }
    Val m = Val::object(), m3 = Val::object();
    m["from"] = "idol";
    m["role"] = "service";
    m["state"] = "streaming";
    m["text"] = "";
    m["attachments"] = Val::array();
    m3["kind"] = "video";
    m3["job"] = jstr(job, "id");
    m3["state"] = "queued";
    m3["stage"] = "Warteschlange";
    m3["progress"] = 0;
    m3["prompt"] = said;
    if (image.isObj()) {
      m3["source"] = jstr(image, "name");
      m3["image"] = jstr(image, "file"); // the picture it is made from (a room file)
    }
    m3["model"] = "h3";                 // shown as made by AI with MiniMax H3 (its licence)
    m3["seconds"] = (double)(seconds < 10 ? 5 : 10);
    if (image.isObj() && use == "ref") m3["use"] = "ref";
    m3["by"] = by;
    m["model3d"] = m3;
    const std::string id = jstr(room_.add(m), "id");
    watch(id, jstr(job, "id"));
    return id;
  }

  // An H3 video of the chat goes on (the owner's "Fortsetzen", a bot's
  // video_generate with `continue`): its last frame begins a new part of
  // `seconds` that does what `prompt` says, and the new message holds the
  // whole video, joined without a seam (the worker does it, resumable like
  // every video). `name`: the video's file name as the chat shows it, or ""
  // for the newest. Returns the new message's id, or "" with `error`.
  std::string video_to_continue(const std::string &name, std::string &error) {
    const std::vector<Val> all = room_.all();
    for (size_t i = all.size(); i-- > 0;) {
      const Val &m = all[i];
      const Val *m3 = m.find("model3d");
      if (!m3 || jstr(*m3, "kind") != "video" || jstr(*m3, "model") != "h3" || jstr(*m3, "state") != "done") continue;
      if (const Val *atts = m.find("attachments"); atts && atts->isArr())
        for (auto &a : atts->asArr())
          if (jstr(a, "kind") == "video" && (name.empty() || jstr(a, "name") == name || jstr(a, "file") == name)) return jstr(m, "id");
    }
    error = name.empty() ? "Im Chat ist kein fertiges H3-Video." : "Im Chat gibt es kein fertiges H3-Video " + name + ".";
    return "";
  }

  std::string extend(const std::string &message_id, const std::string &prompt, const std::string &by, std::string &error, int seconds = 5) {
    Val before;
    if (!room_.get(message_id, before) || !before.find("model3d") || jstr(before["model3d"], "kind") != "video" ||
        jstr(before["model3d"], "model") != "h3" || jstr(before["model3d"], "state") != "done") {
      error = "Fortgesetzt wird ein fertiges H3-Video.";
      return "";
    }
    Val video;
    if (const Val *atts = before.find("attachments"); atts && atts->isArr())
      for (auto &a : atts->asArr())
        if (jstr(a, "kind") == "video") video = a;
    std::string bytes;
    const std::string path = video.isObj() ? room_.file_path(jstr(video, "file")) : "";
    if (path.empty() || !read_file(path, bytes, 500u << 20) || bytes.size() < 8) { error = "Das Video fehlt oder ist größer als 500 MB."; return ""; }
    const std::string said = clip(trim(prompt), 1500, "");
    if (said.empty()) { error = "Was soll im nächsten Teil geschehen?"; return ""; }
    // A worker from before continuing (in the minute of a deploy between the
    // room and the worker) would take the video for a picture and refuse it
    // as one: it is asked first, and the owner is told in words.
    {
      http::ClientOptions probe;
      probe.timeout_s = 3;
      probe.connect_timeout_s = 2;
      probe.headers.push_back({"X-Mag-Idol-UI", "1"});
      if (!jbool(jparse(http::fetch(room_.cfg.idol_url + "/api/idol/health", probe).body), "continue")) {
        error = "Der Videodienst wird gerade aktualisiert; bitte in einer Minute noch einmal.";
        return "";
      }
    }
    http::ClientOptions opt = options("POST");
    opt.body = bytes;
    opt.headers.push_back({"Content-Type", "application/octet-stream"});
    opt.timeout_s = 120;
    const std::string ask = "/api/idol/videos?continue=1&prompt=" + url_encode(said) + "&seed=" +
                            std::to_string(random_bytes(2)[0] * 256 + random_bytes(2)[1] + 1) + "&seconds=" + (seconds < 10 ? "5" : "10");
    const auto res = http::fetch(room_.cfg.idol_url + ask, opt);
    const Val job = jparse(res.body);
    if (res.status != 202 || jstr(job, "id").empty()) {
      error = res.error.empty() ? jstr(job, "error", "Der Videodienst hat den Auftrag abgelehnt.") : "3D-Dienst nicht erreichbar.";
      return "";
    }
    const Val &old = before["model3d"];
    const double total = (jnum(old, "total_seconds") > 0 ? jnum(old, "total_seconds") : jnum(old, "seconds", 5)) + (seconds < 10 ? 5 : 10);
    Val m = Val::object(), m3 = Val::object();
    m["from"] = "idol";
    m["role"] = "service";
    m["state"] = "streaming";
    m["text"] = "";
    m["attachments"] = Val::array();
    m3["kind"] = "video";
    m3["job"] = jstr(job, "id");
    m3["state"] = "queued";
    m3["stage"] = "Warteschlange";
    m3["progress"] = 0;
    m3["prompt"] = said;
    m3["source"] = jstr(video, "name");
    if (!jstr(old, "image").empty()) m3["image"] = jstr(old, "image");
    m3["continues"] = message_id;          // the video it goes on from
    m3["model"] = "h3";
    m3["seconds"] = (double)(seconds < 10 ? 5 : 10);
    m3["total_seconds"] = total;           // the whole joined video
    m3["by"] = by;
    m["model3d"] = m3;
    const std::string id = jstr(room_.add(m), "id");
    watch(id, jstr(job, "id"));
    return id;
  }

  // A picture from words (FLUX.2 klein on the MI50, in the worker's one
  // queue). The message is there at once with its progress and becomes the
  // picture: an ordinary image of the chat, which the bots see and which can
  // go on to photo to 3D. Returns the message id, or "" with `error`.
  // `then_video`: what should happen in a video made from the picture as
  // soon as it is there (text to video: the picture comes first).
  std::string imagine(const std::string &prompt, int width, int height, const std::string &by, std::string &error,
                      const std::string &then_video = "") {
    if (trim(prompt).empty()) { error = "Was soll auf dem Bild sein?"; return ""; }
    http::ClientOptions opt = options("POST");
    Val ask = Val::object();
    ask["prompt"] = clip(trim(prompt), 2000, "");
    ask["width"] = (double)std::max(256, std::min(1536, width));
    ask["height"] = (double)std::max(256, std::min(1536, height));
    ask["seed"] = (double)(random_bytes(2)[0] * 256 + random_bytes(2)[1] + 1);
    opt.body = mj::dump(ask);
    opt.headers.push_back({"Content-Type", "application/json"});
    const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/images", opt);
    const Val job = jparse(res.body);
    if (res.status != 202 || jstr(job, "id").empty()) {
      error = res.error.empty() ? jstr(job, "error", "Der Bilddienst hat den Auftrag abgelehnt.") : "3D-Dienst nicht erreichbar.";
      return "";
    }
    Val m = Val::object(), m3 = Val::object();
    m["from"] = "idol";
    m["role"] = "service";
    m["state"] = "streaming";
    m["text"] = "";
    m["attachments"] = Val::array();
    m3["kind"] = "image";
    m3["job"] = jstr(job, "id");
    m3["state"] = "queued";
    m3["stage"] = "Warteschlange";
    m3["progress"] = 0;
    m3["prompt"] = jstr(ask, "prompt");
    m3["by"] = by;
    if (!trim(then_video).empty()) m3["then_video"] = clip(trim(then_video), 1500, "");
    m["model3d"] = m3;
    const std::string id = jstr(room_.add(m), "id");
    watch(id, jstr(job, "id"));
    return id;
  }

  // A picture painted for a video (text to video) orders that video once,
  // when it is there; the picture's message notes which video it became.
  void then_video(const std::string &message_id, const Val &png) {
    Val m;
    if (!room_.get(message_id, m) || !m.find("model3d")) return;
    const std::string prompt = jstr(m["model3d"], "then_video");
    if (prompt.empty() || !jstr(m["model3d"], "video").empty()) return;
    std::string error;
    const std::string id = film(png, prompt, jstr(m["model3d"], "by", "chris"), error);
    room_.update(message_id, [&](Val &msg) {
      Val &m3 = msg["model3d"];
      m3.obj->erase(std::remove_if(m3.obj->begin(), m3.obj->end(), [](const auto &kv) { return kv.first == "then_video"; }), m3.obj->end());
      if (!id.empty()) m3["video"] = id;
      else m3["video_error"] = error.empty() ? "Das Video wurde abgelehnt." : error;
    });
  }

  // A waiting job one place forwards ("up") or backwards ("down") in the
  // MI50's one line. "" or why not, in words.
  std::string move(const Val &message, const std::string &to) {
    const Val *m3 = job_of(message);
    if (!m3) return "Das ist kein Auftrag der Grafikkarte.";
    if (to != "up" && to != "down") return "Nach vorn oder nach hinten?";
    const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/jobs/" + jstr(*m3, "job") + "/move?to=" + to, options("POST"));
    if (res.status != 200) return res.error.empty() ? jstr(jparse(res.body), "error", "Ließ sich nicht verschieben.") : "3D-Dienst nicht erreichbar.";
    return "";
  }

  void cancel(const Val &message) {
    if (const Val *m3 = job_of(message))
      http::fetch(room_.cfg.idol_url + "/api/idol/jobs/" + jstr(*m3, "job") + "/cancel", options("POST"));
  }

  // A job that failed or was cancelled is taken up again where it stopped:
  // what its finished stages left is still with the worker (after sampling,
  // the splat), so only what did not get done is done. Returns "" or why
  // not, in words.
  // `remesh`: a finished model is made anew from its splat (the mesh stage
  // alone; nothing is sampled again), and takes the place of the old one.
  std::string retry(const Val &message, bool remesh = false) {
    const Val *m3 = job_of(message);
    if (!m3) return "Das ist kein 3D-Auftrag.";
    if (jstr(message, "state") == "streaming") return "Der Auftrag läuft noch.";
    const bool done = jstr(*m3, "state") == "done";
    if (done && !remesh) return "Das Modell ist schon fertig.";
    const std::string job = jstr(*m3, "job");
    const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/jobs/" + job + "/retry" + (done ? "?remesh=1" : ""), options("POST"));
    if (res.status == 404) return "Den 3D-Auftrag gibt es nicht mehr. Bitte das Foto noch einmal schicken.";
    if (res.status != 200) return res.error.empty() ? jstr(jparse(res.body), "error", "Der 3D-Dienst hat abgelehnt.") : "3D-Dienst nicht erreichbar.";
    watch(jstr(message, "id"), job, true);   // it finds the job under way and takes the message along
    return "";
  }

  // The objects of a photo (a room file): the one at a place (x across and
  // y down, 0..1), or with x < 0 every one by itself. `found` gets a mask per
  // object, as large as the photo. Returns "" or why not, in words.
  std::string select(const std::string &file, double x, double y, Val &found, bool scene = false) {
    const std::string path = room_.file_path(file);
    std::string bytes;
    if (path.empty() || !read_file(path, bytes, 20u << 20) || bytes.size() < 8) return "Bild fehlt oder ist größer als 20 MB.";
    http::ClientOptions opt = options("POST");
    opt.body = bytes;
    opt.headers.push_back({"Content-Type", "application/octet-stream"});
    opt.timeout_s = 600;
    const std::string at = x < 0 ? (scene ? "?scene=1" : "") : "?x=" + std::to_string(std::min(1.0, x)) + "&y=" + std::to_string(std::min(1.0, std::max(0.0, y)));
    const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/select" + at, opt);
    found = jparse(res.body);
    if (res.status != 200 || !found.isObj()) return res.error.empty() ? jstr(found, "error", "Der 3D-Dienst hat abgelehnt.") : "3D-Dienst nicht erreichbar.";
    return "";
  }

  // What the page says about one object of a photo's room, taken as numbers
  // only: the group it belongs to, where it stands (metres from the camera:
  // x to the right, z away, lift above the floor), how tall it is, and the
  // room itself. Null: this is no part of a scene.
  static Val scene_part(const Val &in) {
    const Val *s = in.find("scene");
    const std::string group = s && s->isObj() ? jstr(*s, "group") : "";
    if (group.size() < 8 || group.size() > 40 || group.find_first_not_of("0123456789abcdefABCDEF-") != std::string::npos) return Val();
    auto within = [](double v, double lo, double hi) { return std::isfinite(v) ? std::min(hi, std::max(lo, v)) : lo; };
    Val out = Val::object();
    out["group"] = group;
    out["count"] = within(jnum(*s, "count"), 1, 12);
    out["index"] = within(jnum(*s, "index"), 0, 11);
    for (const char *key : {"x", "z"}) out[key] = within(jnum(*s, key), -30, 30);
    out["tall"] = within(jnum(*s, "tall"), 0.02, 10);
    out["lift"] = within(jnum(*s, "lift"), 0, 5);
    if (const Val *r = s->find("room"); r && r->isObj()) {
      Val room = Val::object();
      room["left"] = within(jnum(*r, "left"), -30, 0);
      room["right"] = within(jnum(*r, "right"), 0, 30);
      room["back"] = within(jnum(*r, "back"), 0.5, 40);
      room["height"] = within(jnum(*r, "height"), 2, 6);
      for (const char *key : {"floor", "wall"}) {
        Val colour = Val::array();
        const Val *c = r->find(key);
        for (size_t i = 0; i < 3; ++i) colour.push(within(c && c->isArr() && c->asArr().size() == 3 ? c->asArr()[i].asNum() : 0.7, 0, 1));
        room[key] = colour;
      }
      out["room"] = room;
    }
    return out;
  }
  // Called when a model of a photo's room is finished or has failed: (group).
  std::function<void(const std::string &)> on_scene_part;
  // The groups whose models are all through (done, failed, cancelled, deleted).
  void settle_scenes() {
    std::set<std::string> groups;
    for (auto &m : room_.all())
      if (const Val *m3 = job_of(m))
        if (const Val *s = m3->find("scene")) groups.insert(jstr(*s, "group"));
    for (auto &group : groups)
      if (on_scene_part) on_scene_part(group);
  }

  // The message is gone: remove what the worker keeps for it.
  void forget(const Val &removed) {
    const Val *m3 = job_of(removed);
    if (!m3) return;
    const std::string job = jstr(*m3, "job");
    // One object of a photo's room is gone: the others may be all there are now.
    if (const Val *part = m3->find("scene"); part && on_scene_part)
      std::thread([this, group = jstr(*part, "group")] { on_scene_part(group); }).detach();
    std::thread([this, job] { http::fetch(room_.cfg.idol_url + "/api/idol/jobs/" + job, options("DELETE")); }).detach();
  }
  // The room was emptied: no job has a message any more.
  void purge() {
    std::thread([this] { http::fetch(room_.cfg.idol_url + "/api/idol/purge", options("POST")); }).detach();
  }

  // The room restarted (a deploy, a crash, a reboot); the worker's jobs did
  // not. Every job that was under way is watched again and its message goes
  // on where the job is now. This also brings back a message that an earlier
  // room version declared failed just because the room itself restarted.
  void resume() {
    for (auto &m : room_.all()) {
      // An order whose photo an earlier version was still looking at: it is ordered as it is.
      if (const Val *waiting = m.find("model3d"); waiting && jbool(*waiting, "look", false) && jstr(*waiting, "job").empty() && jstr(m, "state") == "streaming") {
        Val image = Val::object();
        image["file"] = jstr(*waiting, "image");
        image["name"] = jstr(*waiting, "source");
        std::thread([this, id = jstr(m, "id"), image, quality = jstr(*waiting, "quality", "normal")] {
          std::string error;
          if (start(image, quality, error, Val(), id).empty()) finish(id, "error", error);
        }).detach();
        continue;
      }
      const Val *m3 = job_of(m);
      if (!m3) continue;
      const std::string state = jstr(*m3, "state");
      const bool under_way = jstr(m, "state") == "streaming" || state == "queued" || state == "running";
      const bool written_off = state == "error" && jstr(*m3, "error") == "Dienst neu gestartet";
      if (under_way || written_off) watch(jstr(m, "id"), jstr(*m3, "job"), true);
    }
  }

  bool available() {
    http::ClientOptions opt;
    opt.timeout_s = 3;
    opt.connect_timeout_s = 2;
    return http::fetch(room_.cfg.idol_url + "/api/idol/health", opt).status == 200;
  }

private:
  Room &room_;

  static http::ClientOptions options(const std::string &method) {
    http::ClientOptions opt;
    opt.method = method;
    opt.timeout_s = 20;
    opt.connect_timeout_s = 3;
    opt.headers.push_back({"X-Mag-Idol-UI", "1"});
    return opt;
  }

  // Streams one result file of the worker into the room's file store.
  Val fetch_result(const std::string &job, const std::string &remote, const std::string &name) {
    const std::string tmp = room_.cfg.state_dir + "/uploads/idol-" + random_hex(8);
    const int fd = open(tmp.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0640);
    if (fd < 0) return Val();
    http::ClientOptions opt = options("GET");
    opt.timeout_s = 600;
    bool ok = true;
    opt.on_data = [&](const char *data, size_t n) {
      ok = ok && http::write_file_all(fd, data, n);
      return ok;
    };
    const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/jobs/" + job + "/" + remote, opt);
    close(fd);
    if (!ok || res.status != 200 || !res.error.empty()) { unlink(tmp.c_str()); return Val(); }
    return room_.adopt_file(tmp, name, "idol");
  }

  // Follows one job of the worker and mirrors it into its message.
  // `picked_up`: the room is taking a job up again after its own restart.
  void watch(const std::string &message_id, const std::string &job, bool picked_up = false) {
    std::thread([this, message_id, job, picked_up] {
      std::string last;
      int failures = 0;
      bool revive = picked_up;
      for (;;) {
        std::this_thread::sleep_for(std::chrono::milliseconds(picked_up && last.empty() && !failures ? 200 : 1500));
        Val m;
        if (!room_.get(message_id, m)) return; // deleted: forget() cleans up
        http::ClientOptions opt = options("GET");
        opt.timeout_s = 5;
        const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/jobs/" + job, opt);
        const Val st = jparse(res.body);
        if (res.status == 404) {
          // The worker has no such job (any more). A message that was already
          // written off stays as it was.
          if (jstr(m, "state") == "streaming") finish(message_id, "error", "Der 3D-Auftrag existiert nicht mehr.");
          return;
        }
        if (res.status != 200 || !st.isObj()) {
          // The worker is restarting or briefly away: its jobs are on disk
          // and go on, so the message waits with it. Half an hour of silence
          // is a real failure.
          if (++failures == 8 && jstr(m, "state") == "streaming")
            room_.update(message_id, [&](Val &msg) { msg["model3d"]["stage"] = "Wartet auf den 3D-Dienst"; }, false);
          if (failures > 1200) {
            if (jstr(m, "state") == "streaming") finish(message_id, "error", "3D-Dienst antwortet nicht mehr.");
            return;
          }
          continue;
        }
        failures = 0;
        const std::string state = jstr(st, "state");
        if (revive) {
          revive = false;
          const bool alive = state == "queued" || state == "running" || state == "done";
          if (!alive && jstr(m, "state") != "streaming") return; // it really ended back then
          if (alive)
            room_.update(message_id, [&](Val &msg) {
              msg["state"] = "streaming";
              Val &m3 = msg["model3d"];
              m3["state"] = state == "done" ? "running" : state;
              m3["resumed"] = true; // shown as "fortgesetzt"
              m3.obj->erase(std::remove_if(m3.obj->begin(), m3.obj->end(), [](const auto &kv) { return kv.first == "error"; }), m3.obj->end());
            });
        }
        const std::string now = state + "|" + jstr(st, "stage") + "|" + std::to_string((int)jnum(st, "phase")) + "|" +
                                std::to_string((int)jnum(st, "step")) + "|" + std::to_string((int)jnum(st, "ahead"));
        if (now != last && (state == "queued" || state == "running")) {
          last = now;
          // What the worker reports, as it reports it: the phase of the
          // pipeline (k of n, with its name), the sampler's step counter and
          // the sampler's own estimate of the time left.
          room_.update(message_id, [&](Val &msg) {
            Val &m3 = msg["model3d"];
            m3["state"] = state;
            m3["stage"] = jstr(st, "stage");
            m3["phase"] = jnum(st, "phase");
            m3["phases"] = jnum(st, "phases");
            m3["step"] = jnum(st, "step");
            m3["steps"] = jnum(st, "total_steps", 50);
            m3["progress"] = jnum(st, "progress");
            m3["started"] = jnum(st, "started");
            m3["eta"] = jnum(st, "eta");
            m3["eta_at"] = (double)now_s();
            m3["ahead"] = state == "queued" ? jnum(st, "ahead") : 0; // jobs before it on the card
            if (jnum(st, "resumed") > 0) m3["resumed"] = true; // the worker took it up again after its own restart
          }, false);
        }
        if (state == "done" && jstr(m["model3d"], "kind") == "video") {
          Val mp4 = fetch_result(job, "video.mp4", "video-" + job.substr(0, 6) + ".mp4");
          if (!mp4.isObj()) { finish(message_id, "error", "Das Video konnte nicht übernommen werden."); return; }
          room_.update(message_id, [&](Val &msg) {
            Val atts = Val::array();
            atts.push(mp4);
            msg["attachments"] = atts;
            msg["state"] = "done";
            msg["text"] = "Video: " + jstr(msg["model3d"], "prompt", jstr(msg["model3d"], "source"));
            Val &m3 = msg["model3d"];
            m3["state"] = "done";
            m3["stage"] = "Fertig";
            m3["progress"] = 1;
            if (jnum(st, "started") > 0 && jnum(st, "finished") >= jnum(st, "started")) m3["took"] = jnum(st, "finished") - jnum(st, "started");
          });
          return;
        }
        if (state == "done" && jstr(m["model3d"], "kind") == "image") {
          Val png = fetch_result(job, "image.png", "bild-" + job.substr(0, 6) + ".png");
          if (!png.isObj()) { finish(message_id, "error", "Das Bild konnte nicht übernommen werden."); return; }
          room_.update(message_id, [&](Val &msg) {
            Val atts = Val::array();
            atts.push(png);
            msg["attachments"] = atts;
            msg["state"] = "done";
            msg["text"] = "Bild: " + jstr(msg["model3d"], "prompt");
            Val &m3 = msg["model3d"];
            m3["state"] = "done";
            m3["stage"] = "Fertig";
            m3["progress"] = 1;
            if (jnum(st, "started") > 0 && jnum(st, "finished") >= jnum(st, "started")) m3["took"] = jnum(st, "finished") - jnum(st, "started");
          });
          then_video(message_id, png);
          return;
        }
        if (state == "done") {
          room_.update(message_id, [&](Val &msg) { msg["model3d"]["stage"] = "Ergebnisse werden übernommen"; }, false);
          const std::string base = "3d-" + job.substr(0, 6);
          Val glb = fetch_result(job, "mesh.glb", base + ".glb");
          Val stl = fetch_result(job, "mesh.stl", base + ".stl");
          Val ply = fetch_result(job, "splat.ply", base + ".ply");
          if (!glb.isObj()) { finish(message_id, "error", "Das 3D-Modell konnte nicht übernommen werden."); return; }
          room_.update(message_id, [&](Val &msg) {
            Val atts = Val::array();
            atts.push(glb);
            if (stl.isObj()) atts.push(stl);
            if (ply.isObj()) atts.push(ply);
            msg["attachments"] = atts;
            msg["state"] = "done";
            msg["text"] = "3D-Modell";
            Val &m3 = msg["model3d"];
            m3["state"] = "done";
            m3["stage"] = "Fertig";
            m3["progress"] = 1;
            m3["glb"] = jstr(glb, "file");
            if (const Val *seen = st.find("seen_from"); seen && seen->isArr() && seen->asArr().size() == 3) m3["seen_from"] = *seen;
            if (jnum(st, "started") > 0 && jnum(st, "finished") >= jnum(st, "started")) m3["took"] = jnum(st, "finished") - jnum(st, "started");
          });
          part_done(message_id);
          return;
        }
        if (state == "error" || state == "cancelled") {
          finish(message_id, state, jstr(st, "error", state == "cancelled" ? "Abgebrochen" : "Fehler"));
          part_done(message_id);
          return;
        }
      }
    }).detach();
  }

  // A job is through. If it was one object of a photo's room, the room may be complete now.
  void part_done(const std::string &message_id) {
    Val m;
    if (!room_.get(message_id, m) || !on_scene_part) return;
    if (const Val *m3 = job_of(m))
      if (const Val *s = m3->find("scene")) on_scene_part(jstr(*s, "group"));
  }

  void finish(const std::string &message_id, const std::string &state, const std::string &error) {
    room_.update(message_id, [&](Val &msg) {
      msg["state"] = state == "cancelled" ? "stopped" : "error";
      Val &m3 = msg["model3d"];
      m3["state"] = state;
      m3["error"] = error;
      m3["stage"] = state == "cancelled" ? "Abgebrochen" : "Fehler";
    });
  }
};

} // namespace mag
