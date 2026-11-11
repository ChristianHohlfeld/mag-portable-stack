#!/usr/bin/env python3
"""Stand-in for the Idol worker (ops/idol/worker.py): same routes, a job is
done after a few polls. Records deletes and purges."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeIdol:
    def __init__(self, glb=b"glTF-fake"):
        self.jobs, self.deleted, self.purges, self.cancelled = {}, [], 0, []
        self.glb = glb
        self.hold = False  # True: jobs stay "running" until a test lets go
        self.fails = ""    # not empty: jobs end as an error with these words
        self.retried = []
        self.selected = []  # what was asked about the objects of a photo
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, value, raw=None):
                body = raw if raw is not None else json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/octet-stream" if raw is not None else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parts = self.path.strip("/").split("/")
                if parts == ["api", "idol", "health"]:
                    return self.reply(200, {"ok": True})
                job = fake.jobs.get(parts[3]) if len(parts) >= 4 else None
                if not job:
                    return self.reply(404, {"error": "Job nicht gefunden"})
                if len(parts) == 5:
                    return self.reply(200, None, raw=fake.glb)
                job["polls"] += 1
                if job["state"] not in ("cancelled", "error"):
                    job["state"] = "queued" if job["polls"] < 2 else "running" if job["polls"] < 4 or fake.hold else "error" if fake.fails else "done"
                step = min(50, job["polls"] * 15)
                out = {"id": parts[3], "state": job["state"], "stage": "3D-Form berechnen", "phase": 2, "phases": 5,
                       "step": step, "total_steps": 50, "progress": step / 50, "eta": 90, "started": 1000,
                       "finished": 1420, "quality": job["quality"], "resumed": job.get("resumed", 0)}
                if job["state"] == "error":
                    out["error"] = fake.fails or job.get("error", "Fehler")
                    job["error"] = out["error"]
                self.reply(200, out)

            def do_POST(self):
                if self.headers.get("X-Mag-Idol-UI") != "1":
                    return self.reply(403, {"error": "header"})
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                parts = self.path.split("?")[0].strip("/").split("/")
                if parts == ["api", "idol", "purge"]:
                    fake.purges += 1
                    fake.jobs.clear()
                    return self.reply(200, {"deleted": 0})
                if parts == ["api", "idol", "select"]:
                    fake.selected.append(self.path.partition("?")[2])
                    return self.reply(200, {"width": 4, "height": 3, "masks": [{"box": [0, 0, 2, 2], "share": 0.3, "png": "bWFzaw=="}]})
                if len(parts) == 5 and parts[4] == "cancel":
                    fake.cancelled.append(parts[3])
                    fake.jobs[parts[3]]["state"] = "cancelled"
                    return self.reply(200, {"cancelled": True})
                if len(parts) == 5 and parts[4] == "retry":
                    job = fake.jobs.get(parts[3])
                    if not job:
                        return self.reply(404, {"error": "Job nicht gefunden"})
                    fake.retried.append(parts[3])
                    if job["state"] in ("error", "cancelled"):
                        job.update(state="queued", polls=0, resumed=job.get("resumed", 0) + 1)
                    return self.reply(200, {"queued": True})
                if not body:
                    return self.reply(413, {"error": "empty"})
                job_id = "%032x" % (len(fake.jobs) + 1)
                fake.jobs[job_id] = {"state": "queued", "polls": 0, "quality": self.path.split("quality=")[-1], "size": len(body)}
                self.reply(202, {"id": job_id, "state": "queued"})

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
