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
                if job["state"] != "cancelled":
                    job["state"] = "queued" if job["polls"] < 2 else "running" if job["polls"] < 4 else "done"
                step = min(50, job["polls"] * 15)
                self.reply(200, {"id": parts[3], "state": job["state"], "stage": "Idol · Schritt %d/50" % step, "step": step,
                                 "total_steps": 50, "progress": step / 50, "quality": job["quality"]})

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
                if len(parts) == 5 and parts[4] == "cancel":
                    fake.cancelled.append(parts[3])
                    fake.jobs[parts[3]]["state"] = "cancelled"
                    return self.reply(200, {"cancelled": True})
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
