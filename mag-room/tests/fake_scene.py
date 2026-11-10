#!/usr/bin/env python3
"""Stand-in for the scene worker (ops/scene/worker.py): same routes. A scene
is built at once or refused in the worker's words; its film is rendered when
it is asked for and done after a few polls. Records what was asked for,
films, deletes and purges."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PARTS = [{"part": 1, "kind": "Zweibeiner", "height": 0.377, "length": 0.284, "width": 0.218, "shape": "aufrecht (höher als lang)", "side": "links"},
         {"part": 2, "kind": "Vierbeiner", "height": 0.184, "length": 0.498, "width": 0.087, "shape": "länglich und niedrig (länger als hoch)", "side": "rechts"}]
KINDS = {1: ("Zweibeiner", "Zweibeiner: 2 Beine, Rumpf, Kopf"), 2: ("Vierbeiner", "Vierbeiner: 4 Beine, Kopf und Schwanz")}


class FakeScene:
    def __init__(self):
        self.jobs, self.specs, self.asked, self.deleted, self.purges, self.cancelled, self.films, self.fetched = {}, [], [], [], 0, [], [], []
        self.hold = False  # True: films stay "running" until a test lets go
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, value, raw=None):
                body = raw if raw is not None else json.dumps(value, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/octet-stream" if raw is not None else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parts = self.path.strip("/").split("/")
                if parts == ["api", "scene", "health"]:
                    return self.reply(200, {"ok": True})
                job = fake.jobs.get(parts[3]) if len(parts) >= 4 else None
                if not job:
                    return self.reply(404, {"error": "Job nicht gefunden"})
                if len(parts) == 5 and parts[4] == "spec":
                    spec = {k: v for k, v in job["spec"].items() if k != "film"}
                    spec["actors"] = [{k: v for k, v in actor.items() if k != "file"} for actor in spec["actors"]]
                    return self.reply(200, {"spec": spec})
                if len(parts) == 5:
                    if parts[4] == "scene.mp4" and job["state"] != "done":
                        return self.reply(404, {"error": "Ergebnis nicht vorhanden"})
                    fake.fetched.append(parts[4])
                    return self.reply(200, None, raw={"scene.glb": b"glTF-scene", "scene.mp4": b"film", "scene.blend": b"BLENDER"}[parts[4]])
                if job["state"] == "built":
                    return self.reply(200, {"id": parts[3], "state": "built", "stage": "Bereit", "phase": 1, "phases": 3})
                job["polls"] += 1
                if job["state"] != "cancelled":
                    job["state"] = "queued" if job["polls"] < 2 else "running" if job["polls"] < 4 or fake.hold else "done"
                step = min(109, job["polls"] * 30)
                self.reply(200, {"id": parts[3], "state": job["state"], "stage": "Film rendern", "phase": 2, "phases": 3, "step": step,
                                 "total_steps": 109, "progress": step / 109, "eta": 40, "started": 1000, "finished": 1090})

            def do_POST(self):
                if self.headers.get("X-Mag-Scene-UI") != "1":
                    return self.reply(403, {"error": "header"})
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                url = urlsplit(self.path)
                parts = url.path.strip("/").split("/")
                if parts == ["api", "scene", "purge"]:
                    fake.purges += 1
                    fake.jobs.clear()
                    return self.reply(200, {"deleted": 0})
                if parts == ["api", "scene", "parts"]:
                    fake.asked.append(parse_qs(url.query).get("file", [""])[0])
                    return self.reply(200, {"parts": PARTS})
                if len(parts) == 5 and parts[4] == "cancel":
                    fake.cancelled.append(parts[3])
                    fake.jobs[parts[3]]["state"] = "cancelled"
                    return self.reply(200, {"cancelled": True})
                if len(parts) == 5 and parts[4] == "film":
                    job = fake.jobs.get(parts[3])
                    if not job or job["state"] not in ("built", "cancelled", "error"):
                        return self.reply(200, {"queued": False, "error": "Den Film gibt es schon."})
                    fake.films.append(parts[3])
                    job.update(state="queued", polls=0)
                    return self.reply(200, {"queued": True})
                spec = json.loads(body)
                fake.specs.append(spec)
                names = [actor.get("name") for actor in spec.get("actors", [])]
                for actor in spec.get("actors", []):
                    if not actor.get("file"):
                        return self.reply(400, {"error": "Ungültiger Dateiname."})
                    if "part" not in actor:
                        return self.reply(400, {"error": "Das Modell %s zeigt 2 Figuren. Gib bei jedem Darsteller part an." % actor.get("model")})
                for action in spec.get("actions", []):
                    if action.get("actor") not in names:
                        return self.reply(400, {"error": "Aktion für unbekannten Darsteller: %s." % action.get("actor")})
                job_id = "%032x" % (len(fake.jobs) + 0xabc001)
                fake.jobs[job_id] = {"state": "built", "polls": 0, "spec": spec}
                self.reply(201, {"id": job_id, "state": "built", "stage": "Bereit", "title": spec.get("title") or "Szene", "seconds": 4.5,
                                 "frames": 109, "fps": 24, "phase": 1, "phases": 3,
                                 "actors": [{"name": a.get("name"), "kind": KINDS.get(a.get("part"), ("Gegenstand", ""))[0],
                                             "notes": KINDS.get(a.get("part"), ("Gegenstand", ""))[1]} for a in spec.get("actors", [])]})

            def do_DELETE(self):
                job_id = self.path.strip("/").split("/")[3]
                fake.deleted.append(job_id)
                fake.jobs.pop(job_id, None)
                self.reply(200, {"deleted": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
