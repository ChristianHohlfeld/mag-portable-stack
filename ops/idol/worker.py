#!/usr/bin/env python3
"""Private, single-flight Idol MI50 worker; no server-side CUDA imports."""
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

ROOT = Path(os.environ.get("MAG_IDOL_JOB_ROOT", "/var/lib/mag-idol/jobs"))
PYTHON = os.environ.get("MAG_IDOL_PYTHON", "/home/chris/idol-mi50/venv/bin/python")
WEIGHTS = os.environ.get("MAG_IDOL_WEIGHTS", "/home/chris/idol-mi50/weights/idol")
DINO = os.environ.get("IDOL_DINOV3", "/home/chris/idol-mi50/weights/dinov3-timm")
VAE = os.environ.get("IDOL_FLUX2_VAE", "/home/chris/idol-mi50/weights/flux2vae")
PORT = int(os.environ.get("MAG_IDOL_PORT", "8793"))
RUN_TIMEOUT = max(3600, int(os.environ.get("MAG_IDOL_TIMEOUT_S", "4200")))
MAX_UPLOAD = 20 * 1024 * 1024
MAX_PENDING = 8
MESH_TIMEOUTS = {"fast": 300, "normal": 900, "high": 1800}
RESULTS = {"splat.ply": "application/octet-stream", "mesh.glb": "model/gltf-binary",
           "mesh.stl": "model/stl"}
VALID_ID = re.compile(r"^[a-f0-9]{32}$")
STEP = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*50(?!\d)")
STEP_ALT = re.compile(r"\b(?:step|schritt)\s*(\d{1,2})\s*(?:of|/|von)\s*50\b", re.I)
# What tqdm itself expects to remain: "[04:40<15:22, 24.27s/it]".
REMAINING = re.compile(r"<(\d+(?::\d+){1,2})[,\]]")
# The pipeline as it really runs, in this order. A job reports the phase it
# is in (1-based) so the chat can say "Schritt 2 von 5" without guessing:
# 1 the Idol process loads its weights, 2 it samples (50 steps, from tqdm),
# 3-5 are the sections of mesh.py, which announces them ("MAG-STEP ...").
PHASES = ("Modell laden", "3D-Form berechnen", "Mesh rekonstruieren", "Textur backen", "Dateien exportieren")
MESH_MARKS = {"MAG-STEP texture": 4, "MAG-STEP export": 5}
jobs = {}
lock = threading.Lock()
pending = queue.Queue(maxsize=MAX_PENDING)
processes = {}  # ID -> subprocess only while actually running
abort = set()

def save(job):
    folder = ROOT / job["id"]
    tmp = folder / "status.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / "status.json")

def update(job_id, **fields):
    with lock:
        item = jobs.get(job_id)  # None: discarded while it ran
        if item is None or item["state"] in ("cancelled", "error", "done"):
            return False
        item.update(fields)
        if (ROOT / job_id).is_dir():
            save(item)
        return True

def terminate(proc):
    try:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
    except (ProcessLookupError, OSError):
        pass

def cancel(job_id):
    with lock:
        item = jobs.get(job_id)
        if item is None:
            return None
        if item["state"] in ("done", "error", "cancelled"):
            return False
        abort.add(job_id)
        item["state"] = "cancelled"
        item["stage"] = "Abgebrochen"
        item["error"] = "Vom Benutzer abgebrochen"
        save(item)
        child = processes.get(job_id)
    if child:
        terminate(child)
    return True

def discard(job_id):
    """Stop the job if it runs and remove everything it left on disk."""
    cancel(job_id)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        with lock:
            if job_id not in processes:
                break
        time.sleep(0.1)
    with lock:
        jobs.pop(job_id, None)
    folder = ROOT / job_id
    if not folder.is_dir():
        return False
    import shutil
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()

def sampling_line(job_id, value):
    """One line of the Idol process: the sampler's step counter."""
    match = STEP.search(value) or STEP_ALT.search(value)
    if not match:
        return
    step = int(match.group(1))
    with lock:
        item = jobs.get(job_id) or {}
        known = int(item.get("step", 0)) if item.get("phase") == 2 else -1
    if not 0 <= step <= 50 or step <= known:
        return
    left = REMAINING.search(value)
    seconds = None
    if left:
        seconds = 0
        for part in left.group(1).split(":"):
            seconds = seconds * 60 + int(part)
    update(job_id, phase=2, stage=PHASES[1], step=step, total_steps=50,
           progress=step / 50, eta=seconds)

def mesh_line(job_id, value):
    """One line of mesh.py: it says when texture baking and export begin."""
    phase = MESH_MARKS.get(value.strip())
    if phase:
        update(job_id, phase=phase, stage=PHASES[phase - 1])

def run(job_id, command, log_path, timeout, on_line=None):
    env = os.environ.copy()
    env.update({
        "CUDA_VISIBLE_DEVICES": "0", "HIP_VISIBLE_DEVICES": "0",
        "PYTORCH_ROCM_ARCH": "gfx906", "ATTN_BACKEND": "sdpa",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "IDOL_DINOV3": DINO, "IDOL_FLUX2_VAE": VAE,
        "PYTHONUNBUFFERED": "1",
    })
    with log_path.open("wb") as logfile:
        proc = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=0,
                                start_new_session=True)
        with lock:
            processes[job_id] = proc
            already_cancelled = job_id in abort
        if already_cancelled:
            terminate(proc)
        start = time.monotonic()
        linebuf = b""
        try:
            # Read raw bytes, not readline: tqdm frequently emits CR without LF.
            while True:
                if time.monotonic() - start > timeout:
                    terminate(proc)
                    raise TimeoutError(f"Zeitlimit {timeout//60} Minuten überschritten")
                if job_id in abort:
                    terminate(proc)
                    raise InterruptedError("Job abgebrochen")
                import select
                ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                if ready:
                    data = os.read(proc.stdout.fileno(), 8192)
                    if not data:
                        break
                    logfile.write(data)
                    logfile.flush()
                    if on_line:
                        linebuf = (linebuf + data)[-8192:]
                        parts = re.split(b"[\r\n]", linebuf)
                        linebuf = parts[-1]
                        for part in parts[:-1]:
                            on_line(job_id, part.decode("utf-8", errors="replace"))
                elif proc.poll() is not None:
                    break
            code = proc.wait(timeout=10)
            if code:
                raise RuntimeError(f"Prozess Exit {code}; Details im privaten idol.log")
        finally:
            with lock:
                if processes.get(job_id) is proc:
                    del processes[job_id]
            if proc.poll() is None:
                terminate(proc)
            proc.stdout.close()

def execute():
    while True:
        job_id = pending.get()
        try:
            with lock:
                if jobs[job_id]["state"] == "cancelled":
                    continue
            # The MI50 belongs to Idol alone (both bots run CPU-only), so a
            # job never pauses or restarts a model.
            if not update(job_id, state="running", phase=1, phases=len(PHASES),
                          stage=PHASES[0], started=int(time.time()),
                          step=0, total_steps=50, progress=0.0, eta=None):
                continue
            folder = ROOT / job_id
            out = folder / "outputs"
            out.mkdir(exist_ok=True)
            command = ["taskset", "-c", "28-55", PYTHON,
                       str(Path(__file__).with_name("runner.py")),
                       "--image", str(folder / "input.png"),
                       "--out", str(out), "--mode", "real",
                       "--weights", WEIGHTS, "--steps", "50",
                       "--dinov3_dtype", "fp16", "--no_optimize"]
            run(job_id, command, folder / "idol.log", RUN_TIMEOUT, on_line=sampling_line)
            if job_id in abort:
                continue
            source = out / "input.ply"
            if not source.is_file():
                raise RuntimeError("Idol hat keine PLY-Datei erzeugt")
            source.rename(folder / "splat.ply")
            update(job_id, phase=3, stage=PHASES[2], step=50, progress=1.0, eta=None)
            with lock:
                quality = jobs[job_id].get("quality", "normal")
            if quality not in MESH_TIMEOUTS:
                raise ValueError("Ungültige gespeicherte Idol-Qualität")
            mesh = ["taskset", "-c", "28-55", PYTHON,
                    str(Path(__file__).with_name("mesh.py")),
                    str(folder / "splat.ply"), str(folder),
                    "--quality", quality]
            run(job_id, mesh, folder / "mesh.log", MESH_TIMEOUTS[quality], on_line=mesh_line)
            if job_id in abort:
                continue
            if not all((folder / name).is_file() for name in RESULTS):
                raise RuntimeError("Mesh-Export unvollständig (PLY/GLB/STL)")
            update(job_id, state="done", stage="Fertig", files=list(RESULTS),
                   finished=int(time.time()))
        except InterruptedError:
            pass
        except TimeoutError as exc:
            update(job_id, state="error", stage="Timeout", error=str(exc))
        except Exception as exc:
            update(job_id, state="error", stage="Fehler", error=str(exc)[:500])
        finally:
            with lock:
                abort.discard(job_id)
            pending.task_done()

class API(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        url = urlsplit(self.path)
        path = url.path
        if self.headers.get("X-Mag-Idol-UI") != "1":
            return self.reply(403, {"error": "MAG-Idol-Header fehlt"})
        parts = path.strip("/").split("/")
        if (len(parts) == 5 and parts[:3] == ["api", "idol", "jobs"] and
                VALID_ID.fullmatch(parts[3]) and parts[4] == "cancel"):
            outcome = cancel(parts[3])
            if outcome is None:
                return self.reply(404, {"error": "Job nicht gefunden"})
            return self.reply(200, {"cancelled": outcome})
        if path == "/api/idol/purge":
            # The room was emptied: no job has a message any more.
            with lock:
                known = list(jobs)
            stray = [p.name for p in ROOT.iterdir() if p.is_dir() and VALID_ID.fullmatch(p.name)]
            return self.reply(200, {"deleted": sum(discard(i) for i in set(known + stray))})
        if path != "/api/idol/jobs":
            return self.reply(404, {"error": "Nicht gefunden"})
        supplied = parse_qs(url.query, keep_blank_values=True)
        quality_values = supplied.get("quality", ["normal"])
        if len(quality_values) != 1 or quality_values[0] not in MESH_TIMEOUTS:
            return self.reply(400, {"error": "Qualität muss fast, normal oder high sein"})
        quality = quality_values[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 8 <= length <= MAX_UPLOAD:
            return self.reply(413, {"error": "Bild muss zwischen 8 Byte und 20 MB groß sein"})
        data = self.rfile.read(length)
        if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff") or
                (data.startswith(b"RIFF") and data[8:12] == b"WEBP")):
            return self.reply(415, {"error": "Nur PNG, JPEG oder WebP erlaubt"})
        with lock:
            if pending.full():
                return self.reply(429, {"error": "Idol-Warteschlange voll"})
            identity = uuid4().hex
            folder = ROOT / identity
            folder.mkdir(mode=0o700, parents=True)
            (folder / "input.png").write_bytes(data)
            item = {"id": identity, "state": "queued", "stage": "Warteschlange",
                    "quality": quality, "created": int(time.time()),
                    "step": 0, "total_steps": 50, "phase": 0, "phases": len(PHASES)}
            jobs[identity] = item
            save(item)
            pending.put_nowait(identity)
        return self.reply(202, item)

    def do_DELETE(self):
        # The room deletes a job together with its chat message.
        if self.headers.get("X-Mag-Idol-UI") != "1":
            return self.reply(403, {"error": "MAG-Idol-Header fehlt"})
        parts = urlsplit(self.path).path.strip("/").split("/")
        if len(parts) != 4 or parts[:3] != ["api", "idol", "jobs"] or not VALID_ID.fullmatch(parts[3]):
            return self.reply(404, {"error": "Nicht gefunden"})
        return self.reply(200, {"deleted": discard(parts[3])})

    def do_GET(self):
        parts = urlsplit(self.path).path.strip("/").split("/")
        if parts == ["api", "idol", "health"]:
            return self.reply(200, {"ok": True, "queued": pending.qsize()})
        if len(parts) < 4 or parts[:3] != ["api", "idol", "jobs"] or not VALID_ID.fullmatch(parts[3]):
            return self.reply(404, {"error": "Nicht gefunden"})
        with lock:
            item = dict(jobs.get(parts[3], {}))
        if not item:
            return self.reply(404, {"error": "Job nicht gefunden"})
        if len(parts) == 4:
            return self.reply(200, item)
        if len(parts) != 5 or parts[4] not in RESULTS or item["state"] != "done":
            return self.reply(404, {"error": "Ergebnis nicht vorhanden"})
        file = ROOT / parts[3] / parts[4]
        if not file.is_file():
            return self.reply(404, {"error": "Datei fehlt"})
        self.send_response(200)
        self.send_header("Content-Type", RESULTS[parts[4]])
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", f'attachment; filename="{parts[4]}"')
        self.send_header("Content-Length", str(file.stat().st_size))
        self.end_headers()
        try:
            with file.open("rb") as stream:
                import shutil
                shutil.copyfileobj(stream, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            pass

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    for path in ROOT.glob("*/status.json"):
        try:
            item = json.loads(path.read_text())
            if not VALID_ID.fullmatch(item["id"]):
                continue
            if item["state"] in ("running", "queued"):
                item["state"] = "error"
                item["error"] = "Dienst neu gestartet; bitte erneut senden"
                item["stage"] = "Unterbrochen"
                save(item)
            jobs[item["id"]] = item
        except (ValueError, OSError, KeyError):
            pass
    threading.Thread(target=execute, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), API).serve_forever()

if __name__ == "__main__":
    main()
