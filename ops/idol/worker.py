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
from urllib.parse import urlsplit
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
RESULTS = {"splat.ply": "application/octet-stream", "mesh.glb": "model/gltf-binary",
           "mesh.stl": "model/stl"}
VALID_ID = re.compile(r"^[a-f0-9]{32}$")
STEP = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*50(?!\d)")
STEP_ALT = re.compile(r"\b(?:step|schritt)\s*(\d{1,2})\s*(?:of|/|von)\s*50\b", re.I)
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
        if jobs[job_id]["state"] in ("cancelled", "error", "done"):
            return False
        jobs[job_id].update(fields)
        save(jobs[job_id])
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

def run(job_id, command, log_path, timeout, progress=False):
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
                    if progress:
                        linebuf = (linebuf + data)[-8192:]
                        parts = re.split(b"[\r\n]", linebuf)
                        linebuf = parts[-1]
                        for part in parts[:-1]:
                            value = part.decode("utf-8", errors="replace")
                            match = STEP.search(value) or STEP_ALT.search(value)
                            if match:
                                step = int(match.group(1))
                                if 1 <= step <= 50:
                                    with lock:
                                        prev = int(jobs[job_id].get("step", 0))
                                    if step > prev:
                                        update(job_id, step=step, total_steps=50,
                                               progress=step / 50,
                                               stage=f"Idol · Schritt {step}/50")
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

def lena_state(job_id, state):
    with lock:
        item = jobs.get(job_id)
        if item:
            item["lena_state"] = state
            save(item)

def lena_lease(action):
    command = ["sudo", "-n", "/usr/local/lib/mag-idol/gpu-lease.sh", action]
    result = subprocess.run(command, capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError(f"Lena GPU-{action} fehlgeschlagen: " + (result.stderr or result.stdout).strip()[-240:])

def lena_health(job_id, timeout=360):
    import urllib.request
    last = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8191/health", timeout=3) as response:
                if response.status == 200:
                    lena_state(job_id, "bereit")
                    return
        except Exception as exc:
            last = str(exc)
        time.sleep(2)
    raise RuntimeError("Lena startete, aber :8191/health meldet nach "
                       + str(timeout) + " Sekunden kein HTTP 200: " + last[:160])

def execute():
    while True:
        job_id = pending.get()
        leased = False
        try:
            with lock:
                if jobs[job_id]["state"] == "cancelled":
                    continue
            if not update(job_id, state="running", stage="Lena wird für Foto zu 3D pausiert",
                          step=0, total_steps=50, progress=0.0,
                          lena_state="pausiert für Foto zu 3D"):
                continue
            # Mark lease before calling acquire. Even if acquire times out or
            # partly stops Lena, the finally path MUST request restoration.
            leased = True
            lena_lease("acquire")
            if job_id in abort:
                continue
            folder = ROOT / job_id
            out = folder / "outputs"
            out.mkdir(exist_ok=True)
            # No unreliable GPU-driver free-VRAM check: Lena uses Vulkan.
            # Lena systemd lease has stopped its Vulkan process; Idol uses full VRAM.
            command = ["taskset", "-c", "28-55", PYTHON,
                       str(Path(__file__).with_name("runner.py")),
                       "--image", str(folder / "input.png"),
                       "--out", str(out), "--mode", "real",
                       "--weights", WEIGHTS, "--steps", "50",
                       "--dinov3_dtype", "fp16", "--no_optimize"]
            run(job_id, command, folder / "idol.log", RUN_TIMEOUT, progress=True)
            if job_id in abort:
                continue
            source = out / "input.ply"
            if not source.is_file():
                raise RuntimeError("Idol hat keine PLY-Datei erzeugt")
            source.rename(folder / "splat.ply")
            update(job_id, stage="Mesh rekonstruieren", step=50, progress=1.0)
            mesh = ["taskset", "-c", "28-55", PYTHON,
                    str(Path(__file__).with_name("mesh.py")),
                    str(folder / "splat.ply"), str(folder)]
            run(job_id, mesh, folder / "mesh.log", 600)
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
            if leased:
                # This also runs on failure, timeout and user cancellation.
                # systemd ExecStopPost is an independent crash/kill safeguard.
                try:
                    lena_state(job_id, "wird neu gestartet")
                    lena_lease("restore")
                    lena_health(job_id)
                except Exception as exc:
                    lena_state(job_id, "Wiederanlauf fehlgeschlagen")
                    with lock:
                        item = jobs[job_id]
                        item["error"] = (str(item.get("error", "")) + "; "
                                         + str(exc))[-500:]
                        if item["state"] == "done":
                            item["stage"] = "Export fertig; Lena nicht bereit"
                        save(item)
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
        path = urlsplit(self.path).path
        if self.headers.get("X-Mag-Idol-UI") != "1":
            return self.reply(403, {"error": "MAG-Idol-Header fehlt"})
        parts = path.strip("/").split("/")
        if (len(parts) == 5 and parts[:3] == ["api", "idol", "jobs"] and
                VALID_ID.fullmatch(parts[3]) and parts[4] == "cancel"):
            outcome = cancel(parts[3])
            if outcome is None:
                return self.reply(404, {"error": "Job nicht gefunden"})
            return self.reply(200, {"cancelled": outcome})
        if path != "/api/idol/jobs":
            return self.reply(404, {"error": "Nicht gefunden"})
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
                    "created": int(time.time()), "step": 0, "total_steps": 50}
            jobs[identity] = item
            save(item)
            pending.put_nowait(identity)
        return self.reply(202, item)

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
