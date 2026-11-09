#!/usr/bin/env python3
"""Loopback-only Idol job HTTP service. nginx authenticates before proxying."""
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(os.environ.get("MAG_IDOL_JOB_ROOT", "/var/lib/mag-idol/jobs"))
PYTHON = os.environ.get("MAG_IDOL_PYTHON", "/home/chris/idol-mi50/venv/bin/python")
WEIGHTS = os.environ.get("MAG_IDOL_WEIGHTS", "/home/chris/idol-mi50/weights/idol")
MIN_FREE = int(os.environ.get("MAG_IDOL_MIN_FREE_MIB", "10000"))
MAX_UPLOAD = 20 * 1024 * 1024
MAX_PENDING = 8
PORT = int(os.environ.get("MAG_IDOL_PORT", "8793"))
jobs = {}
lock = threading.Lock()
pending = queue.Queue(maxsize=MAX_PENDING)
VALID_ID = re.compile(r"^[a-f0-9]{32}$")
EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
RESULTS = {"splat.ply": "application/octet-stream", "mesh.glb": "model/gltf-binary", "mesh.stl": "model/stl"}

def available_vram():
    # ROCm's rocm-smi reports memory for every GPU, not host RAM.
    tool = shutil.which("rocm-smi") or "/opt/rocm/bin/rocm-smi"
    result = subprocess.run([tool, "--showmeminfo", "vram", "--json"], text=True,
                            capture_output=True, timeout=10, check=True)
    data = json.loads(result.stdout)
    def collect(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    yield from collect(value)
                elif "used" in key.lower() and ("vram" in key.lower() or "memory" in key.lower()):
                    try: yield int(str(value).replace(",", "").split()[0])
                    except ValueError: pass
        elif isinstance(obj, list):
            for item in obj: yield from collect(item)
    # Different ROCm versions use different JSON shapes: fail closed if unknown.
    used = list(collect(data))
    total = []
    def totals(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (dict, list)): yield from totals(value)
                elif "total" in key.lower() and ("vram" in key.lower() or "memory" in key.lower()):
                    try: yield int(str(value).replace(",", "").split()[0])
                    except ValueError: pass
        elif isinstance(obj, list):
            for item in obj: yield from totals(item)
    total = list(totals(data))
    if not total or not used: raise RuntimeError("VRAM-Werte von rocm-smi nicht lesbar; Job aus Sicherheitsgründen blockiert")
    # Values are bytes in rocm-smi JSON output. Only MI50 is supported here.
    return (total[0] - used[0]) // 1048576

def save(job):
    directory = ROOT / job["id"]
    tmp = directory / "status.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, directory / "status.json")

def execute():
    while True:
        job_id = pending.get()
        with lock:
            item = jobs[job_id]
            item["state"] = "running"
            item["stage"] = "VRAM prüfen"
            save(item)
        folder = ROOT / job_id
        try:
            free = available_vram()
            if free < MIN_FREE:
                raise RuntimeError(f"MI50 VRAM belegt: {free} MiB frei, mindestens {MIN_FREE} MiB benötigt. Lena nutzt dieselbe GPU; bitte später erneut versuchen.")
            env = os.environ.copy()
            env["CUDA_VISIBLE_DEVICES"] = "0"
            env["HIP_VISIBLE_DEVICES"] = "0"
            env["PYTORCH_ROCM_ARCH"] = "gfx906"
            # No network or implicit download inside jobs.
            env["HF_HUB_OFFLINE"] = "1"
            env["TRANSFORMERS_OFFLINE"] = "1"
            out = folder / "outputs"
            out.mkdir()
            # idol-sdk upstream sample.py does NOT support --device; choose CUDA
            # through visible GPU and pipeline auto-selection. FP16 must be
            # implemented in the locally installed MI50-compatible idol-sdk.
            command = ["taskset", "-c", "28-55", PYTHON, "-m", "idol.sample",
                       "--image", str(folder / "input.png"), "--out", str(out),
                       "--mode", "real", "--weights", WEIGHTS, "--no_optimize",
                       "--dinov3_dtype", "fp32", "--num_gaussians", "131072"]
            with (folder / "idol.log").open("w") as log:
                result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=1800, check=False)
            if result.returncode:
                raise RuntimeError(f"Idol fehlgeschlagen (Exit {result.returncode}); siehe lokales idol.log. GPU-/FP16-Portierung prüfen.")
            source = out / "input.ply"
            if not source.is_file(): raise RuntimeError("Idol hat keine PLY-Datei erzeugt")
            shutil.move(source, folder / "splat.ply")
            with lock:
                item["stage"] = "Mesh rekonstruieren"
                save(item)
            result = subprocess.run(["taskset", "-c", "28-55", PYTHON,
                                     str(Path(__file__).with_name("mesh.py")),
                                     str(folder / "splat.ply"), str(folder)],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=600)
            if result.returncode:
                raise RuntimeError("Mesh-Konvertierung fehlgeschlagen: " + result.stdout[-350:])
            with lock:
                item["stage"] = "Fertig"
                item["state"] = "done"
                item["files"] = list(RESULTS)
                save(item)
        except Exception as exc:
            with lock:
                item["state"] = "error"
                item["stage"] = "Fehler"
                item["error"] = str(exc)[:600]
                save(item)
        finally:
            pending.task_done()

class API(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/idol/jobs":
            return self.reply(404, {"error": "Nicht gefunden"})
        try: length = int(self.headers.get("Content-Length", "0"))
        except ValueError: length = 0
        if not (8 <= length <= MAX_UPLOAD):
            return self.reply(413, {"error": "Bild muss zwischen 8 Byte und 20 MB groß sein"})
        # Raw image body; verify magic before placing into job folder.
        data = self.rfile.read(length)
        if not (data.startswith(b"\x89PNG\r\n\x1a\n") or
                data.startswith(b"\xff\xd8\xff") or
                (data.startswith(b"RIFF") and data[8:12] == b"WEBP")):
            return self.reply(415, {"error": "Nur PNG, JPEG oder WebP erlaubt"})
        with lock:
            if pending.full():
                return self.reply(429, {"error": "Idol-Warteschlange voll"})
            identity = uuid4().hex
            folder = ROOT / identity
            folder.mkdir(mode=0o700, parents=True)
            (folder / "input.png").write_bytes(data)
            item = {"id": identity, "state": "queued", "stage": "Warteschlange", "created": int(time.time())}
            jobs[identity] = item
            save(item)
            pending.put_nowait(identity)
        self.reply(202, item)

    def do_GET(self):
        parts = urlsplit(self.path).path.strip("/").split("/")
        if parts == ["api", "idol", "health"]:
            return self.reply(200, {"ok": True, "queued": pending.qsize()})
        if len(parts) < 4 or parts[:3] != ["api", "idol", "jobs"] or not VALID_ID.fullmatch(parts[3]):
            return self.reply(404, {"error": "Nicht gefunden"})
        with lock:
            item = dict(jobs.get(parts[3], {}))
        if not item: return self.reply(404, {"error": "Job nicht gefunden"})
        if len(parts) == 4: return self.reply(200, item)
        if len(parts) != 5 or parts[4] not in RESULTS or item["state"] != "done":
            return self.reply(404, {"error": "Ergebnis nicht vorhanden"})
        file = ROOT / parts[3] / parts[4]
        if not file.is_file(): return self.reply(404, {"error": "Datei fehlt"})
        self.send_response(200)
        self.send_header("Content-Type", RESULTS[parts[4]])
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", f'attachment; filename="{parts[4]}"')
        self.send_header("Content-Length", str(file.stat().st_size))
        self.end_headers()
        with file.open("rb") as stream: shutil.copyfileobj(stream, self.wfile)

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    # On restart interrupted work is marked failed, never silently replayed.
    for path in ROOT.glob("*/status.json"):
        try:
            item = json.loads(path.read_text())
            if not VALID_ID.fullmatch(item["id"]): continue
            if item["state"] in ("running", "queued"):
                item["state"] = "error"
                item["error"] = "Dienst wurde neu gestartet. Bitte Job erneut senden."
                save(item)
            jobs[item["id"]] = item
        except (ValueError, OSError, KeyError): pass
    threading.Thread(target=execute, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), API).serve_forever()

if __name__ == "__main__": main()
