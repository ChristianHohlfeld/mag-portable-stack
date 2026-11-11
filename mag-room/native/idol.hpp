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
  // `into`: the message that stood for the order while the photo was looked
  // at (see look()); it becomes the job's message instead of a new one.
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

  // A plain order (no object chosen): the photo is looked at first. Idol
  // makes one model of one thing; a photo of two people side by side became
  // one lump of both, each a third as large as it could be. So what stands
  // apart in the foreground is told apart (the worker's own foreground
  // model, on the CPU, half a minute), and each piece is ordered by itself,
  // filling its picture, as a part of the photo's scene. One thing only: the
  // order goes on as it is. The message of the order is there at once and
  // says what happens; a room that restarts while it looks, looks again.
  std::string look(const Val &image, const std::string &quality, std::string &error) {
    if (quality != "fast" && quality != "normal" && quality != "high") { error = "Qualität muss fast, normal oder high sein."; return ""; }
    if (room_.file_path(jstr(image, "file")).empty()) { error = "Bild fehlt oder ist größer als 20 MB."; return ""; }
    Val m = Val::object(), m3 = Val::object();
    m["from"] = "idol";
    m["role"] = "service";
    m["state"] = "streaming";
    m["text"] = "";
    m["attachments"] = Val::array();
    m3["state"] = "queued";
    m3["stage"] = "Sieht nach, was im Foto ist";
    m3["progress"] = 0;
    m3["quality"] = quality;
    m3["source"] = jstr(image, "name");
    m3["image"] = jstr(image, "file");
    m3["look"] = true;                   // no job yet: the photo is being looked at
    m["model3d"] = m3;
    const std::string id = jstr(room_.add(m), "id");
    std::thread([this, id, image, quality] { looked(id, image, quality); }).detach();
    return id;
  }

  // A job of this worker. (A scene is a 3D message too; it belongs to Scenes.)
  static const Val *job_of(const Val &message) {
    const Val *m3 = message.find("model3d");
    return m3 && !jstr(*m3, "job").empty() && jstr(*m3, "kind").empty() ? m3 : nullptr;
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
  std::string select(const std::string &file, double x, double y, Val &found, bool scene = false, bool pieces = false) {
    const std::string path = room_.file_path(file);
    std::string bytes;
    if (path.empty() || !read_file(path, bytes, 20u << 20) || bytes.size() < 8) return "Bild fehlt oder ist größer als 20 MB.";
    http::ClientOptions opt = options("POST");
    opt.body = bytes;
    opt.headers.push_back({"Content-Type", "application/octet-stream"});
    opt.timeout_s = 600;
    const std::string at = x < 0 ? (pieces ? "?pieces=1" : scene ? "?scene=1" : "") : "?x=" + std::to_string(std::min(1.0, x)) + "&y=" + std::to_string(std::min(1.0, std::max(0.0, y)));
    const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/select" + at, opt);
    found = jparse(res.body);
    if (res.status != 200 || !found.isObj()) return res.error.empty() ? jstr(found, "error", "Der 3D-Dienst hat abgelehnt.") : "3D-Dienst nicht erreichbar.";
    return "";
  }

  void looked(const std::string &id, const Val &image, const std::string &quality) {
    Val found;
    const std::string why = select(jstr(image, "file"), -1, 0, found, false, true);
    Val still;
    if (!room_.get(id, still)) return;   // deleted meanwhile
    const Val *masks = why.empty() ? found.find("masks") : nullptr;
    std::string error;
    if (!masks || !masks->isArr() || masks->asArr().size() < 2) {
      // One thing (or nobody could look): the order as it was.
      if (start(image, quality, error, Val(), id).empty()) finish(id, "error", error);
      return;
    }
    const std::string group = random_hex(8);
    const std::string name = jstr(image, "name");
    const std::string base = name.substr(0, name.rfind('.') == std::string::npos ? name.size() : name.rfind('.'));
    size_t index = 0, begun = 0;
    const size_t count = std::min<size_t>(8, masks->asArr().size());
    for (auto &mask : masks->asArr()) {
      if (index >= count) break;
      const std::string cut = b64_decode(jstr(mask, "cut"));
      Val part = Val::object(), wrapped = Val::object();
      part["group"] = group;
      part["index"] = (double)index;
      part["count"] = (double)count;
      if (const Val *place = mask.find("place"); place && place->isObj())
        for (auto &kv : *place->obj) part[kv.first] = kv.second;
      if (const Val *walls = found.find("room")) part["room"] = *walls;
      wrapped["scene"] = part;
      const Val att = cut.size() < 64 ? Val() : room_.store_file(base + "-" + std::to_string(index + 1) + ".png", cut, "chris");
      if (att.isObj() && !start(att, quality, error, scene_part(wrapped), index == 0 ? id : "").empty()) ++begun;
      ++index;
    }
    if (!begun) {
      if (start(image, quality, error, Val(), id).empty()) finish(id, "error", error.empty() ? "Der 3D-Dienst hat den Auftrag abgelehnt." : error);
    }
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
      // An order whose photo was being looked at when the room went down: it is looked at again.
      if (const Val *waiting = m.find("model3d"); waiting && jbool(*waiting, "look", false) && jstr(*waiting, "job").empty() && jstr(m, "state") == "streaming") {
        Val image = Val::object();
        image["file"] = jstr(*waiting, "image");
        image["name"] = jstr(*waiting, "source");
        std::thread([this, id = jstr(m, "id"), image, quality = jstr(*waiting, "quality", "normal")] { looked(id, image, quality); }).detach();
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
                                std::to_string((int)jnum(st, "step"));
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
            if (jnum(st, "resumed") > 0) m3["resumed"] = true; // the worker took it up again after its own restart
          }, false);
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
