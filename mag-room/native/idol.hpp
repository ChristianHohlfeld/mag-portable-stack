// Photo to 3D (MAG Idol on the MI50) as ordinary room messages.
// A job is one message from "idol": it shows the progress while the worker
// runs and carries the results (GLB, STL, PLY) as attachments afterwards, so
// a 3D result is stored, ordered, reloadable and deletable like any message.
// Deleting the message also deletes the job directory of the worker.
#pragma once
#include "http.hpp"
#include "room.hpp"

#include <thread>

namespace mag {

class Idol {
public:
  explicit Idol(Room &room) : room_(room) {}

  // Starts a job for an image that is already a room file. Returns the id of
  // the 3D message, or "" with `error`.
  std::string start(const Val &image, const std::string &quality, std::string &error) {
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
    m["model3d"] = m3;
    const std::string id = jstr(room_.add(m), "id");
    watch(id, jstr(job, "id"));
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
  std::string retry(const Val &message) {
    const Val *m3 = job_of(message);
    if (!m3) return "Das ist kein 3D-Auftrag.";
    if (jstr(message, "state") == "streaming") return "Der Auftrag läuft noch.";
    if (jstr(*m3, "state") == "done") return "Das Modell ist schon fertig.";
    const std::string job = jstr(*m3, "job");
    const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/jobs/" + job + "/retry", options("POST"));
    if (res.status == 404) return "Den 3D-Auftrag gibt es nicht mehr. Bitte das Foto noch einmal schicken.";
    if (res.status != 200) return res.error.empty() ? jstr(jparse(res.body), "error", "Der 3D-Dienst hat abgelehnt.") : "3D-Dienst nicht erreichbar.";
    watch(jstr(message, "id"), job, true);   // it finds the job under way and takes the message along
    return "";
  }

  // The objects of a photo (a room file): the one at a place (x across and
  // y down, 0..1), or with x < 0 every one by itself. `found` gets a mask per
  // object, as large as the photo. Returns "" or why not, in words.
  std::string select(const std::string &file, double x, double y, Val &found) {
    const std::string path = room_.file_path(file);
    std::string bytes;
    if (path.empty() || !read_file(path, bytes, 20u << 20) || bytes.size() < 8) return "Bild fehlt oder ist größer als 20 MB.";
    http::ClientOptions opt = options("POST");
    opt.body = bytes;
    opt.headers.push_back({"Content-Type", "application/octet-stream"});
    opt.timeout_s = 600;
    const std::string at = x < 0 ? "" : "?x=" + std::to_string(std::min(1.0, x)) + "&y=" + std::to_string(std::min(1.0, std::max(0.0, y)));
    const auto res = http::fetch(room_.cfg.idol_url + "/api/idol/select" + at, opt);
    found = jparse(res.body);
    if (res.status != 200 || !found.isObj()) return res.error.empty() ? jstr(found, "error", "Der 3D-Dienst hat abgelehnt.") : "3D-Dienst nicht erreichbar.";
    return "";
  }

  // The message is gone: remove what the worker keeps for it.
  void forget(const Val &removed) {
    const Val *m3 = job_of(removed);
    if (!m3) return;
    const std::string job = jstr(*m3, "job");
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
