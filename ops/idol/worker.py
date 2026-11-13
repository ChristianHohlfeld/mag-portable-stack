#!/usr/bin/env python3
"""Private, single-flight Idol MI50 worker; no server-side CUDA imports.

A job survives everything short of being deleted. It is a folder: status.json
says where the job is, and every stage of the pipeline leaves its result
there (the whole shape -> shape.ply, meshing -> mesh.glb and mesh.stl; Idol's
sampling -> splat.ply only where the shape alone does not do). A stage runs
in its own session, writes its output to a log file and its exit code to a
file, so it does not need this process: when the worker restarts (a deploy,
a crash) it finds the stage still running and follows it again, and after a
reboot it starts the job again at the first stage whose result is missing.
Nothing is ever written off as "interrupted", and nothing has a time limit:
a stage is ended only when it stands still (it neither writes nor uses the
processor for ten minutes), and then it is started again. A job that failed or was
cancelled is taken up again when it is asked for (retry), at the first stage
whose result is missing: sampling that is done is not done again.
"""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4
# The Idol environment of this host's owner (ops/host.env; ~/idol-mi50).
IDOL_BASE = os.environ.get("MAG_IDOL_BASE", os.path.expanduser("~/idol-mi50"))

ROOT = Path(os.environ.get("MAG_IDOL_JOB_ROOT", "/var/lib/mag-idol/jobs"))
PYTHON = os.environ.get("MAG_IDOL_PYTHON", os.path.join(IDOL_BASE, "venv/bin/python"))
WEIGHTS = os.environ.get("MAG_IDOL_WEIGHTS", os.path.join(IDOL_BASE, "weights/idol"))
DINO = os.environ.get("IDOL_DINOV3", os.path.join(IDOL_BASE, "weights/dinov3-timm"))
VAE = os.environ.get("IDOL_FLUX2_VAE", os.path.join(IDOL_BASE, "weights/flux2vae"))
PORT = int(os.environ.get("MAG_IDOL_PORT", "8793"))
CPUS = os.environ.get("MAG_IDOL_CPUS", "28-55")  # never the models' cores
# A stage has no time limit: sampling on the MI50 at its 73 W takes as long
# as it takes. A stage is only ended when it has come to a standstill: for
# this long it has neither written a line nor used the processor.
STALL = max(1, int(os.environ.get("MAG_IDOL_STALL_S", "600")))
MAX_UPLOAD = 20 * 1024 * 1024
MAX_VIDEO_UPLOAD = 500 * 1024 * 1024   # a video that is continued
MAX_PENDING = 32   # a whole room of objects, pictures and videos wait in one line
QUALITIES = ("fast", "normal", "high")
RESULTS = {"splat.ply": "application/octet-stream", "mesh.glb": "model/gltf-binary",
           "mesh.stl": "model/stl"}
# A picture from words (imagegen.py, FLUX.2 klein on the MI50) is a job of the
# same queue: the card does one thing at a time.
IMAGE_RESULTS = {"image.png": "image/png"}
IMAGE_STEP = re.compile(r"MAG-IMAGE (\d+)/(\d+)")
# A video with its sound (h3gen.py, HyperFlow H3 Flash, a few-step MiniMax H3,
# by stable-diffusion.cpp natively on HIP for gfx906), the same queue again. Licensed to Chris
# personally: its weights and program are fetched and built on this host by
# hand (ops/video/h3), and until they are there the room is told so. (Wan 2.2
# made the videos before; its weights stay on disk, unused.)
VIDEO_RESULTS = {"video.mp4": "video/mp4"}
VIDEO_STEP = re.compile(r"MAG-VIDEO (\d+)/(\d+)")
VIDEO_STAGE = re.compile(r"MAG-VIDEO-STAGE (.+)")
H3_WEIGHTS = Path(os.environ.get("MAG_H3_WEIGHTS", os.path.join(IDOL_BASE, "weights/hyperflow-h3")))
H3_CLI = Path(os.environ.get("MAG_H3_CLI", os.path.join(IDOL_BASE, "h3/sd-cli-hip")))
H3_ROCM = Path(os.path.join(IDOL_BASE, "rocm-6.3.4/current"))
H3_STEPS = 4
H3D_URL = os.environ.get("MAG_H3D_URL", "http://127.0.0.1:8796")   # mag-h3d.service


def h3_ready():
    return (H3_WEIGHTS / ".complete").is_file() and H3_CLI.is_file() and H3_ROCM.is_dir()


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
LOGS = {"sample": "idol.log", "shape": "shape.log", "paint": "paint.log", "mesh": "mesh.log", "image": "image.log", "video": "video.log"}
# The whole shape (shape.py, TripoSG on the MI50) in an environment of its own;
# where it is not installed, the mesh is made from the splat alone.
SHAPE_PYTHON = os.environ.get("MAG_TRIPOSG_PYTHON", os.path.join(IDOL_BASE, "triposg/venv/bin/python"))
SHAPE_WEIGHTS = os.environ.get("MAG_TRIPOSG_WEIGHTS", os.path.join(IDOL_BASE, "weights/triposg"))
# Its colours from every side (paint.py, MV-Adapter on SDXL, in TripoSG's
# environment), where ops/idol/paint/fetch.sh fetched them all.
PAINT_SOURCE = os.environ.get("MAG_MVADAPTER_SOURCE", os.path.join(IDOL_BASE, "mvadapter/src"))
PAINT_WEIGHTS = os.environ.get("MAG_PAINT_WEIGHTS", os.path.join(IDOL_BASE, "weights/paint"))
PAINT_STEP = re.compile(r"MAG-PAINT (\d+)/(\d+)")
# The MI50 does one thing at a time. Jobs of this queue already run one after
# the other; a stage that uses the card also holds this lock for as long as it
# runs (it is the stage's process that holds it, so it holds across a restart
# of the worker), and anything else that wants the card takes it too. A stage
# that finds the card taken waits and says so ("MAG-WAIT"), which counts as
# getting on: waiting is no standstill.
GPU_LOCK = os.environ.get("MAG_GPU_LOCK", str(ROOT.parent / "gpu.lock"))
GPU_STAGES = ("sample", "shape", "paint", "image", "video")
GPU_WAIT = "MAG-WAIT"
jobs = {}
lock = threading.Lock()
class Line:
    """The MI50's one line of jobs (3D, pictures, videos). A job's place is
    its "order", kept in its status.json, so the line is the same after a
    restart or a reboot; Chris moves a job forwards or backwards in it from
    the chat (move). The next job is always the queued one first in line."""

    def __init__(self):
        self.ids, self.cv = [], threading.Condition()

    def qsize(self):
        with self.cv:
            return len(self.ids)

    def put_nowait(self, job_id):
        with self.cv:
            if job_id not in self.ids:
                self.ids.append(job_id)
            self.cv.notify()

    def get(self):
        with self.cv:
            while not self.ids:
                self.cv.wait()
            first = min(self.ids, key=lambda i: (float(jobs.get(i, {}).get("order", 0) or 0), i))
            self.ids.remove(first)
            return first

    def task_done(self):
        pass


def place_of(item):
    return float(item.get("order") or item.get("created") or 0)


def move(job_id, to):
    """A queued job one place forwards ("up") or backwards ("down") in the
    line. True, False (it is not waiting, or already there), None: no job."""
    with lock:
        item = jobs.get(job_id)
        if item is None:
            return None
        if item.get("state") != "queued":
            return False
        line = sorted((other for other in jobs.values() if other.get("state") == "queued"), key=lambda o: (place_of(o), o["id"]))
        at = next(i for i, other in enumerate(line) if other["id"] == job_id)
        there = at - 1 if to == "up" else at + 1
        if not 0 <= there < len(line):
            return False
        other = line[there]
        mine, theirs = place_of(item), place_of(other)
        if mine == theirs:
            theirs += -1e-3 if to == "up" else 1e-3
        item["order"], other["order"] = theirs, mine
        save(item)
        save(other)
    return True


pending = Line()
selecting = threading.Lock()   # one photo is looked at at a time
abort = set()


class Lost(Exception):
    """A stage vanished without leaving its exit code (killed, reboot)."""


class Stalled(Exception):
    """A stage came to a standstill and was ended."""

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

def proc_start(pid):
    """Start time of a live process (clock ticks since boot); None when it
    is gone or only a zombie. With the pid it identifies one process."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    fields = stat[stat.rindex(")") + 2:].split()
    return None if fields[0] == "Z" else int(fields[19])

def adopt(item):
    """The stage of a job that is running now, found from the stages' own pid
    files (a stage writes its pid when it starts): status.json may not say so
    when the worker was restarted right as it started the stage. The one
    that started first wins (it holds the card; a later one waits for it)."""
    folder = ROOT / item["id"]
    found = []
    for key in LOGS:
        if (folder / f"{key}.exit").exists():
            continue
        try:
            pid = int((folder / f"{key}.exit.pid").read_text().strip())
        except (OSError, ValueError):
            continue
        began = proc_start(pid)
        if began is not None:
            found.append((began, key, pid))
    if found:
        began, key, pid = min(found)
        if (item.get("stage_key"), item.get("pid"), item.get("pid_start")) != (key, pid, began):
            print(f"stage {key} of {item['id'][:8]} found running (pid {pid})", flush=True)
            offset = item.get("log_offset") if item.get("stage_key") == key else 0
            item.update(stage_key=key, pid=pid, pid_start=began, log_offset=offset or 0)


def stage_alive(item):
    pid = item.get("pid")
    return bool(pid) and proc_start(pid) is not None and proc_start(pid) == item.get("pid_start")

def worked(pid):
    """How much processor time everything in a stage's session has used so
    far (clock ticks). A stage that is alive but hangs uses none."""
    total = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            stat = Path("/proc", entry, "stat").read_text()
            fields = stat[stat.rindex(")") + 2:].split()
            if int(fields[3]) == pid:
                total += int(fields[11]) + int(fields[12])
        except (OSError, ValueError, IndexError):
            continue
    return total

def reap():
    """Collect finished stages this process started itself."""
    try:
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    except ChildProcessError:
        pass

def read_exit(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None

def kill_stage(item):
    """End the running stage of a job (its whole process group)."""
    pid = item.get("pid")
    for sig, patience in ((signal.SIGTERM, 4), (signal.SIGKILL, 6)):
        if not stage_alive(item):
            return
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        end = time.monotonic() + patience
        while time.monotonic() < end and stage_alive(item):
            reap()
            time.sleep(0.1)

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
        snapshot = dict(item)
    kill_stage(snapshot)
    return True

def again(job_id, remesh=False):
    """Take a failed or cancelled job up again. What its finished stages
    left is still there (after sampling, the splat), so only what did not
    get done is done. With `remesh` a finished job's model is made anew
    from its splat (the mesh stage alone, on the CPU: nothing is sampled
    again). Returns True, or why not in words; None: no such job."""
    with lock:
        item = jobs.get(job_id)
        if item is None:
            return None
        if item["state"] == "done" and remesh and any((ROOT / job_id / name).is_file() for name in ("splat.ply", "shape.ply")):
            for name in ("mesh.glb", "mesh.stl", "mesh.exit", "mesh.json", "alone.failed"):
                (ROOT / job_id / name).unlink(missing_ok=True)
            item.pop("finished", None)
            item.pop("seen_from", None)
        elif item["state"] in ("queued", "running", "done"):
            return True                                   # under way, or done: there is nothing to take up
        if not ((ROOT / job_id / "input.png").is_file() or (ROOT / job_id / "prompt.json").is_file()):
            return "Das Foto dieses Auftrags gibt es nicht mehr."
        if pending.qsize() >= MAX_PENDING:
            return "Idol-Warteschlange voll"
        abort.discard(job_id)
        # A whole shape that could not be made before is tried once more: without
        # it the model is only the splat's relief.
        if not (ROOT / job_id / "shape.ply").is_file() and read_exit(ROOT / job_id / "shape.exit") not in (None, 0):
            (ROOT / job_id / "shape.exit").unlink(missing_ok=True)
        if not (ROOT / job_id / "outputs" / "views.png").is_file() and read_exit(ROOT / job_id / "paint.exit") not in (None, 0):
            (ROOT / job_id / "paint.exit").unlink(missing_ok=True)
        item.pop("error", None)
        item.pop("started", None)                         # its time counts from now
        item["order"] = time.time()                       # at the end of the line
        item.update(state="queued", stage="Wird fortgesetzt", pid=None, stage_key=None, step=0, progress=0.0,
                    eta=None, resumed=int(item.get("resumed", 0)) + 1)
        save(item)
        pending.put_nowait(job_id)
    return True

def discard(job_id):
    """Stop the job if it runs and remove everything it left on disk."""
    cancel(job_id)
    with lock:
        item = dict(jobs.get(job_id) or {})
    kill_stage(item)
    with lock:
        jobs.pop(job_id, None)
    folder = ROOT / job_id
    if not VALID_ID.fullmatch(job_id) or not folder.is_dir():
        return False
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

def paint_line(job_id, value):
    """One line of paint.py: the step MV-Adapter is at."""
    found = PAINT_STEP.search(value)
    if found:
        k, n = int(found.group(1)), max(1, int(found.group(2)))
        update(job_id, phase=3, stage="Farben rundum malen", step=k, total_steps=n, progress=k / n)

def mesh_line(job_id, value):
    """One line of mesh.py: it says when texture baking and export begin."""
    phase = MESH_MARKS.get(value.strip())
    if phase:
        update(job_id, phase=phase, stage=PHASES[phase - 1])

def stage_env(folder):
    env = os.environ.copy()
    tmp = folder / "tmp"  # not the service's private /tmp: that goes when the worker restarts
    tmp.mkdir(exist_ok=True)
    env.update({
        "CUDA_VISIBLE_DEVICES": "0", "HIP_VISIBLE_DEVICES": "0",
        "PYTORCH_ROCM_ARCH": "gfx906", "ATTN_BACKEND": "sdpa",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "IDOL_DINOV3": DINO, "IDOL_FLUX2_VAE": VAE,
        "PYTHONUNBUFFERED": "1", "TMPDIR": str(tmp),
    })
    return env

def launch(job_id, key, command):
    """Start one stage on its own: own session, output in its log, exit code
    in <stage>.exit. It keeps running when this worker does not."""
    folder = ROOT / job_id
    log_path, exit_path = folder / LOGS[key], folder / f"{key}.exit"
    exit_path.unlink(missing_ok=True)
    offset = log_path.stat().st_size if log_path.exists() else 0
    if CPUS:
        command = ["taskset", "-c", CPUS] + command
    if key in GPU_STAGES and GPU_LOCK:
        # A lock file that cannot be opened never costs a job: the stage runs
        # without it (this queue still runs one job at a time) and says so.
        # A stage that is not a video, once the card is its own, has h3d let go
        # of H3 (its buffers on the card); the next video loads it again.
        release = "" if key == "video" else (
            'curl -s -m 120 -X POST ' + H3D_URL + '/sdcpp/v1/models/unload >/dev/null 2>&1 || :; ')
        command = ["/bin/sh", "-c", 'if (: >>"$0") 2>/dev/null; then exec 9>>"$0"; w=; '
                   'until flock -n 9; do w=1; echo "' + GPU_WAIT + ' Die Grafikkarte ist belegt"; sleep 20; done; '
                   '[ -z "$w" ] || echo "' + GPU_WAIT + '-END"; else echo "MAG-NOTE no GPU lock: $0 cannot be opened"; fi; '
                   + release + 'exec "$@"',
                   GPU_LOCK] + command
    # The stage writes its own pid next to where its exit code will be: a
    # worker that starts while this one is still saying so finds it anyway.
    wrapper = ["/bin/sh", "-c", 'echo $$ > "$0.pid"; "$@"; rc=$?; echo "$rc" > "$0.part"; mv "$0.part" "$0"', str(exit_path)] + command
    with log_path.open("ab") as log:
        proc = subprocess.Popen(wrapper, env=stage_env(folder), stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
    update(job_id, stage_key=key, pid=proc.pid, pid_start=proc_start(proc.pid),
           stage_started=int(time.time()), log_offset=offset)

def follow(job_id, key, on_line):
    """Read a stage's log as it grows until its exit code is on disk. Works
    the same for a stage this process started and for one it found running.
    However long it takes: only a stage that stands still is ended."""
    folder = ROOT / job_id
    log_path, exit_path = folder / LOGS[key], folder / f"{key}.exit"
    with lock:
        item = dict(jobs[job_id])
    offset, linebuf, gone = int(item.get("log_offset") or 0), b"", None
    moved, looked, used = time.monotonic(), 0.0, -1      # when it last got on, when its processor time was last read, and what it was
    while True:
        reap()
        code = read_exit(exit_path)
        data = b""
        try:
            with log_path.open("rb") as log:
                log.seek(offset)
                data = log.read(65536)
        except OSError:
            pass
        if data:
            offset += len(data)
            # Split on CR as well: tqdm redraws its line without a newline, so
            # the line it is showing right now is the unfinished last part.
            # That part is read too (the step counter in it is current), and
            # the saved offset stays in front of it for a worker that follows
            # this stage after a restart.
            parts = re.split(b"[\r\n]", (linebuf + data)[-8192:])
            linebuf = parts[-1]
            for part in parts:
                if part.startswith((GPU_WAIT + "-END").encode()):
                    update(job_id, stage=item.get("stage"))            # the card is its own now
                elif part.startswith(GPU_WAIT.encode()):
                    update(job_id, stage="Wartet auf die Grafikkarte")
                elif part:
                    on_line(job_id, part.decode("utf-8", errors="replace"))
            update(job_id, log_offset=offset - len(linebuf))
            moved = time.monotonic()
            continue
        if code is not None:
            break
        if job_id in abort:
            kill_stage(item)
            raise InterruptedError("Job abgebrochen")
        if stage_alive(item):
            gone = None
            now = time.monotonic()
            if now - looked >= min(5.0, STALL / 4):       # it writes nothing: is it working?
                looked, before, used = now, used, worked(item["pid"])
                if used != before:
                    moved = now
            if now - moved > STALL:
                kill_stage(item)
                raise Stalled(key)
        else:
            gone = gone or time.monotonic()
            if time.monotonic() - gone > 3:  # no process and still no exit code
                raise Lost(key)
        time.sleep(0.5)
    if code:
        raise RuntimeError(why_failed(log_path, code))


def why_failed(log_path, code):
    """What a failed stage says about itself, in words for the chat: its own
    MAG-ERROR line, a cause this worker knows, or at least which step it was."""
    try:
        with log_path.open("rb") as log:
            log.seek(max(0, log_path.stat().st_size - 20000))
            tail = log.read().decode("utf-8", errors="replace")
    except OSError:
        tail = ""
    said = [line[len("MAG-ERROR "):].strip() for line in tail.splitlines() if line.startswith("MAG-ERROR ")]
    if said:
        return said[-1]
    if "image file is truncated" in tail or "cannot identify image file" in tail:
        return "Das Bild ist unvollständig oder beschädigt angekommen. Bitte noch einmal hochladen."
    if "out of memory" in tail.lower():
        return "Der Grafikkarte ist der Speicher ausgegangen. Bitte mit einer niedrigeren Qualität noch einmal versuchen."
    return f"Die Berechnung ist abgebrochen (Prozess Exit {code}); Details im privaten {log_path.name}"

def run_stage(job_id, key, command, on_line, fresh):
    """Run a stage to its end. A stage that is still running from before a
    restart of this worker (or finished meanwhile) is taken over as it is; one
    that was lost, or that came to a standstill and was ended, is started
    again."""
    for attempt in range(3):
        with lock:
            item = dict(jobs[job_id])
        known = item.get("stage_key") == key and bool(item.get("pid"))
        finished = known and attempt == 0 and read_exit(ROOT / job_id / f"{key}.exit") is not None
        if not (known and (stage_alive(item) or finished)):
            update(job_id, **fresh)
            launch(job_id, key, command)
        try:
            return follow(job_id, key, on_line)
        except (Lost, Stalled) as why:
            stalled = isinstance(why, Stalled)
            with lock:
                again = int(jobs[job_id].get("resumed", 0)) + 1 if job_id in jobs else 1
            update(job_id, pid=None, resumed=again)
    if stalled:
        raise TimeoutError(f"Kein Fortschritt mehr: der Schritt stand dreimal länger als {max(1, STALL // 60)} Minuten still")
    raise RuntimeError("Der Schritt wurde mehrfach unterbrochen")

def image_line(job_id, value):
    """One line of imagegen.py: the step the model is at."""
    found = IMAGE_STEP.search(value)
    if found:
        k, n = int(found.group(1)), max(1, int(found.group(2)))
        update(job_id, phase=2, stage="Bild malen", step=k, total_steps=n, progress=k / n)


def video_line(job_id, value):
    """One line of h3gen.py: which part it is in, how far it got."""
    found = VIDEO_STEP.search(value)
    if found:
        k, n = int(found.group(1)), max(1, int(found.group(2)))
        update(job_id, phase=2, stage="Video rechnen", step=k, total_steps=n, progress=k / n)
        return
    found = VIDEO_STAGE.search(value)
    if found:
        update(job_id, stage=found.group(1).strip()[:60])


def process_video(job_id):
    folder = ROOT / job_id
    if not (folder / "video.mp4").is_file():
        command = [PYTHON, str(Path(__file__).with_name("h3gen.py")), str(folder)]
        run_stage(job_id, "video", command, video_line, dict(phase=1, phases=2, stage="Modell laden", step=0,
                                                              total_steps=H3_STEPS, progress=0.0, eta=None))
        if not (folder / "video.mp4").is_file():
            raise RuntimeError("Das Video wurde nicht fertig")
    update(job_id, state="done", stage="Fertig", phase=2, files=list(VIDEO_RESULTS), finished=int(time.time()), pid=None)


def process_image(job_id):
    folder = ROOT / job_id
    if not (folder / "image.png").is_file():
        command = [PYTHON, str(Path(__file__).with_name("imagegen.py")), str(folder)]
        run_stage(job_id, "image", command, image_line, dict(phase=1, phases=2, stage="Modell laden", step=0, total_steps=4, progress=0.0, eta=None))
        if not (folder / "image.png").is_file():
            raise RuntimeError("Das Bild wurde nicht fertig")
    update(job_id, state="done", stage="Fertig", phase=2, files=list(IMAGE_RESULTS), finished=int(time.time()), pid=None)


def process(job_id):
    folder = ROOT / job_id
    out = folder / "outputs"
    out.mkdir(exist_ok=True)
    with lock:
        quality = jobs[job_id].get("quality", "normal")
        kind = jobs[job_id].get("kind", "model")
    if kind == "image":
        return process_image(job_id)
    if kind == "video":
        return process_video(job_id)
    if quality not in QUALITIES:
        raise ValueError("Ungültige gespeicherte Idol-Qualität")
    here = Path(__file__)
    splat = folder / "splat.ply"
    shape, tried = folder / "shape.ply", folder / "shape.exit"
    with lock:
        sampling = jobs[job_id].get("stage_key") == "sample"   # a worker before this one began with the splat

    def sample():
        """The Idol process loads its weights and samples. Its result is
        splat.ply; a job that already has it does not sample again."""
        if splat.is_file():
            return
        command = [PYTHON, str(here.with_name("runner.py")),
                   "--image", str(folder / "input.png"),
                   "--out", str(out), "--mode", "real",
                   "--weights", WEIGHTS, "--steps", "50",
                   "--dinov3_dtype", "fp16", "--no_optimize"]
        run_stage(job_id, "sample", command, sampling_line,
                  dict(phase=1, stage=PHASES[0], step=0, total_steps=50, progress=0.0, eta=None))
        source = out / "input.ply"
        if not source.is_file():
            raise RuntimeError("Idol hat keine PLY-Datei erzeugt")
        source.rename(splat)

    def meshed():
        return read_exit(folder / "mesh.exit") == 0 and all((folder / name).is_file() for name in ("mesh.glb", "mesh.stl"))

    if sampling:
        sample()
    # The whole shape first (TripoSG, a few minutes on the MI50), where it is
    # installed. Its result is shape.ply; it never costs a job (not installed,
    # or it failed: the splat is sampled and the mesh made from it as before).
    weights = Path(SHAPE_WEIGHTS) / "transformer" / "diffusion_pytorch_model.safetensors"
    if not shape.is_file() and not tried.is_file() and Path(SHAPE_PYTHON).is_file() and weights.is_file():
        command = [SHAPE_PYTHON, str(here.with_name("shape.py")), str(folder)]
        try:
            run_stage(job_id, "shape", command, sampling_line,
                      dict(phase=2, stage=PHASES[1], step=0, total_steps=50, progress=0.0, eta=None))
        except InterruptedError:
            raise                                         # cancelled: nothing more is done
        except Exception as exc:
            print(f"shape of {job_id[:8]} not made: {type(exc).__name__}", flush=True)
    # The shape painted from six sides (MV-Adapter, the MI50 again, about ten
    # minutes): the colours of every side, not the photo's carried around.
    # It never costs a job either: without its views the model is coloured
    # as before.
    views = folder / "outputs" / "views.png"
    if (shape.is_file() and not views.is_file() and not (folder / "paint.exit").is_file() and not meshed()
            and Path(PAINT_SOURCE).is_dir() and (Path(PAINT_WEIGHTS) / ".complete").is_file()):
        command = [SHAPE_PYTHON, str(here.with_name("paint.py")), str(folder)]
        try:
            run_stage(job_id, "paint", command, paint_line,
                      dict(phase=3, stage="Farben rundum malen", step=0, total_steps=50, progress=0.0, eta=None))
        except InterruptedError:
            raise
        except Exception as exc:
            print(f"views of {job_id[:8]} not painted: {type(exc).__name__}", flush=True)
    # With the shape, the model is made from it and the photo alone: the splat
    # would only say where it stands, and sampling it takes a quarter of an
    # hour. Where that does not do (alone.failed), the splat is sampled.
    alone_failed = folder / "alone.failed"
    if shape.is_file() and not splat.is_file() and not meshed() and not alone_failed.is_file():
        command = [PYTHON, str(here.with_name("mesh.py")), str(splat), str(folder), "--quality", quality, "--alone"]
        try:
            run_stage(job_id, "mesh", command, mesh_line,
                      dict(phase=3, stage=PHASES[2], step=50, progress=1.0, eta=None))
        except InterruptedError:
            raise
        except Exception as exc:
            print(f"model of {job_id[:8]} not made from its shape alone: {type(exc).__name__}", flush=True)
        if not meshed():
            alone_failed.touch()
    # Mesh, texture, export from the splat (and the shape, where there is one).
    # Done only when mesh.py itself said so.
    if not meshed():
        sample()
        command = [PYTHON, str(here.with_name("mesh.py")), str(splat), str(folder), "--quality", quality]
        run_stage(job_id, "mesh", command, mesh_line,
                  dict(phase=3, stage=PHASES[2], step=50, progress=1.0, eta=None))
        if not meshed():
            raise RuntimeError("Mesh-Export unvollständig (GLB/STL)")
    shutil.rmtree(folder / "tmp", ignore_errors=True)
    seen = None
    try:
        seen = json.loads((folder / "mesh.json").read_text()).get("seen_from")
    except (OSError, ValueError, AttributeError):
        pass
    update(job_id, state="done", stage="Fertig", phase=len(PHASES), files=[name for name in RESULTS if (folder / name).is_file()],
           finished=int(time.time()), pid=None, **({"seen_from": seen} if isinstance(seen, list) and len(seen) == 3 else {}))

def execute():
    while True:
        job_id = pending.get()
        try:
            with lock:
                item = jobs.get(job_id)
                state = item["state"] if item else "gone"
                begun = bool(item and item.get("started"))
            if state not in ("queued", "running"):
                continue
            # The MI50 belongs to Idol alone (both bots run CPU-only), so a
            # job never pauses or restarts a model.
            image = bool(item) and item.get("kind") in ("image", "video")
            fields = dict(state="running", phases=2 if image else len(PHASES))
            if not begun and image:
                fields.update(started=int(time.time()), phase=1, stage="Modell laden", step=0, total_steps=4, progress=0.0, eta=None)
            elif not begun:
                fields.update(started=int(time.time()), phase=1, stage=PHASES[0],
                              step=0, total_steps=50, progress=0.0, eta=None)
            if not update(job_id, **fields):
                continue
            process(job_id)
        except InterruptedError:
            pass
        except TimeoutError as exc:
            update(job_id, state="error", stage="Stillstand", error=str(exc))
        except Exception as exc:
            update(job_id, state="error", stage="Fehler", error=str(exc)[:500])
        finally:
            with lock:
                abort.discard(job_id)
            pending.task_done()

def is_picture(data):
    return (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff") or
            (data.startswith(b"RIFF") and data[8:12] == b"WEBP"))


def select(data, query):
    """The objects of a photo (objects.py: SAM 2 on the CPU, never the MI50):
    the one at a place, or all of them. Returns (status, answer); every mask
    is a PNG as large as the photo, white where the object is."""
    here = Path(__file__).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="select-", dir=ROOT))
    try:
        (tmp / "photo").write_bytes(data)
        command = [PYTHON, str(here.with_name("objects.py")), str(tmp / "photo"), str(tmp / "out")]
        if "x" in query and "y" in query:
            try:
                x, y = float(query["x"][0]), float(query["y"][0])
            except ValueError:
                return 400, {"error": "x und y müssen Zahlen sein"}
            if not (0 <= x <= 1 and 0 <= y <= 1):
                return 400, {"error": "x und y liegen zwischen 0 und 1"}
            command += ["--point", str(x), str(y)]
        else:
            command += ["--pieces" if "pieces" in query else "--scene" if "scene" in query else "--all"]
        if CPUS:
            command = ["taskset", "-c", CPUS] + command
        # Few threads: the unit counts tasks, and a stage of a job may be running beside it.
        env = dict(os.environ, CUDA_VISIBLE_DEVICES="", HIP_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TMPDIR=str(tmp),
                   OMP_NUM_THREADS="12", MKL_NUM_THREADS="12", OPENBLAS_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
        with selecting:
            done = subprocess.run(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        listing = tmp / "out" / "masks.json"
        if done.returncode != 0 or not listing.is_file():
            return 500, {"error": "Die Objekte im Foto ließen sich nicht finden."}
        found = json.loads(listing.read_text())
        for mask in found["masks"]:
            mask["png"] = base64.b64encode((tmp / "out" / mask.pop("file")).read_bytes()).decode()
            if "cut" in mask:                        # the piece itself, cut out of the photo
                mask["cut"] = base64.b64encode((tmp / "out" / mask["cut"]).read_bytes()).decode()
        return 200, found
    except subprocess.TimeoutExpired:
        return 500, {"error": "Die Objekte im Foto ließen sich nicht finden."}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
                VALID_ID.fullmatch(parts[3]) and parts[4] == "move"):
            to = parse_qs(url.query).get("to", [""])[0]
            if to not in ("up", "down"):
                return self.reply(400, {"error": "up oder down"})
            moved = move(parts[3], to)
            if moved is None:
                return self.reply(404, {"error": "Job nicht gefunden"})
            return self.reply(200, {"moved": moved})
        if (len(parts) == 5 and parts[:3] == ["api", "idol", "jobs"] and
                VALID_ID.fullmatch(parts[3]) and parts[4] in ("cancel", "retry")):
            outcome = cancel(parts[3]) if parts[4] == "cancel" else again(parts[3], "remesh" in parse_qs(url.query))
            if outcome is None:
                return self.reply(404, {"error": "Job nicht gefunden"})
            if parts[4] == "cancel":
                return self.reply(200, {"cancelled": outcome})
            return self.reply(200, {"queued": True}) if outcome is True else self.reply(409, {"error": outcome})
        if path == "/api/idol/purge":
            # The room was emptied: no job has a message any more.
            with lock:
                known = list(jobs)
            stray = [p.name for p in ROOT.iterdir() if p.is_dir() and VALID_ID.fullmatch(p.name)]
            return self.reply(200, {"deleted": sum(discard(i) for i in set(known + stray))})
        if path == "/api/idol/select":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 8 <= length <= MAX_UPLOAD:
                return self.reply(413, {"error": "Bild muss zwischen 8 Byte und 20 MB groß sein"})
            data = self.rfile.read(length)
            if not is_picture(data):
                return self.reply(415, {"error": "Nur PNG, JPEG oder WebP erlaubt"})
            return self.reply(*select(data, parse_qs(url.query)))
        if path == "/api/idol/images":
            # A picture from words: {"prompt", "width", "height", "seed"}.
            try:
                length = int(self.headers.get("Content-Length", "0"))
                asked = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
                prompt = str(asked.get("prompt", "")).strip()
                width, height = int(asked.get("width", 1024)), int(asked.get("height", 1024))
                seed = int(asked.get("seed", 0))
            except (ValueError, TypeError, AttributeError):
                return self.reply(400, {"error": "Ungültiger Bildauftrag"})
            if not 1 <= len(prompt) <= 2000:
                return self.reply(400, {"error": "Die Beschreibung fehlt oder ist zu lang"})
            with lock:
                if pending.qsize() >= MAX_PENDING:
                    return self.reply(429, {"error": "Idol-Warteschlange voll"})
                identity = uuid4().hex
                folder = ROOT / identity
                folder.mkdir(mode=0o700, parents=True)
                (folder / "prompt.json").write_text(json.dumps({"prompt": prompt, "width": width, "height": height, "seed": seed}))
                item = {"id": identity, "kind": "image", "state": "queued", "stage": "Warteschlange", "quality": "normal",
                        "created": int(time.time()), "order": time.time(), "step": 0, "total_steps": 4, "phase": 0, "phases": 2}
                jobs[identity] = item
                save(item)
                pending.put_nowait(identity)
            return self.reply(202, item)
        if path == "/api/idol/videos":
            # A video with its sound (HyperFlow H3 Flash): ?prompt= what happens and is heard,
            # 5 or 10 ?seconds; the body a picture as its first frame or (?use=ref) as who
            # or what is in it, or nothing: words alone. ?continue=1: the body is the
            # video so far (mp4); its last frame begins the new part, and the result is
            # the whole video, joined without a seam.
            asked = parse_qs(url.query)
            if not h3_ready():
                return self.reply(503, {"error": "Das Videomodell (H3) ist noch nicht ganz eingerichtet: Download oder Build läuft."})
            prompt = asked.get("prompt", [""])[0].strip()[:1500]
            try:
                seed = int(asked.get("seed", ["0"])[0])
                seconds = 5 if int(asked.get("seconds", ["5"])[0]) < 10 else 10
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply(400, {"error": "Ungültiger Videoauftrag"})
            if not prompt:
                return self.reply(400, {"error": "Was soll im Video geschehen?"})
            data = b""
            going_on = asked.get("continue", ["0"])[0] == "1"
            if going_on:
                if not 8 <= length <= MAX_VIDEO_UPLOAD:
                    return self.reply(413, {"error": "Das Video zum Fortsetzen fehlt oder ist größer als 500 MB"})
                data = self.rfile.read(length)
                if data[4:8] != b"ftyp":
                    return self.reply(415, {"error": "Fortgesetzt wird nur ein MP4-Video"})
            elif length:
                if not 8 <= length <= MAX_UPLOAD:
                    return self.reply(413, {"error": "Bild muss zwischen 8 Byte und 20 MB groß sein"})
                data = self.rfile.read(length)
                if not is_picture(data):
                    return self.reply(415, {"error": "Nur PNG, JPEG oder WebP erlaubt"})
            told = {"prompt": prompt, "seed": seed, "model": "h3", "seconds": seconds,
                    "use": "ref" if asked.get("use", ["first"])[0] == "ref" else "first"}
            with lock:
                if pending.qsize() >= MAX_PENDING:
                    return self.reply(429, {"error": "Idol-Warteschlange voll"})
                identity = uuid4().hex
                folder = ROOT / identity
                folder.mkdir(mode=0o700, parents=True)
                if data:
                    (folder / ("prev.mp4" if going_on else "input.png")).write_bytes(data)
                if going_on:
                    told.update(use="first", continues=True)
                (folder / "prompt.json").write_text(json.dumps(told))
                item = {"id": identity, "kind": "video", "state": "queued", "stage": "Warteschlange", "quality": "normal",
                        "created": int(time.time()), "order": time.time(), "step": 0, "total_steps": H3_STEPS,
                        "phase": 0, "phases": 2, "model": "h3"}
                jobs[identity] = item
                save(item)
                pending.put_nowait(identity)
            return self.reply(202, item)
        if path != "/api/idol/jobs":
            return self.reply(404, {"error": "Nicht gefunden"})
        supplied = parse_qs(url.query, keep_blank_values=True)
        quality_values = supplied.get("quality", ["normal"])
        if len(quality_values) != 1 or quality_values[0] not in QUALITIES:
            return self.reply(400, {"error": "Qualität muss fast, normal oder high sein"})
        quality = quality_values[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 8 <= length <= MAX_UPLOAD:
            return self.reply(413, {"error": "Bild muss zwischen 8 Byte und 20 MB groß sein"})
        data = self.rfile.read(length)
        if not is_picture(data):
            return self.reply(415, {"error": "Nur PNG, JPEG oder WebP erlaubt"})
        with lock:
            if pending.qsize() >= MAX_PENDING:
                return self.reply(429, {"error": "Idol-Warteschlange voll"})
            identity = uuid4().hex
            folder = ROOT / identity
            folder.mkdir(mode=0o700, parents=True)
            (folder / "input.png").write_bytes(data)
            item = {"id": identity, "state": "queued", "stage": "Warteschlange",
                    "quality": quality, "created": int(time.time()), "order": time.time(),
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
            with lock:
                active = sum(1 for item in jobs.values() if item["state"] in ("queued", "running"))
            # "resumable": this worker's jobs survive its restart.
            return self.reply(200, {"ok": True, "queued": pending.qsize(), "active": active, "resumable": True,
                                    "video": h3_ready(), "continue": True})   # "continue": ?continue=1 is understood
        if len(parts) < 4 or parts[:3] != ["api", "idol", "jobs"] or not VALID_ID.fullmatch(parts[3]):
            return self.reply(404, {"error": "Nicht gefunden"})
        with lock:
            item = dict(jobs.get(parts[3], {}))
        if not item:
            return self.reply(404, {"error": "Job nicht gefunden"})
        if len(parts) == 4:
            if item.get("state") == "queued":
                # Its place in the one line: how many jobs go before it on the card.
                with lock:
                    mine = (place_of(item), parts[3])
                    item["ahead"] = sum(1 for key, other in jobs.items() if other.get("state") == "running" or (
                        other.get("state") == "queued" and (place_of(other), key) < mine))
            return self.reply(200, item)
        results = {"image": IMAGE_RESULTS, "video": VIDEO_RESULTS}.get(item.get("kind"), RESULTS)
        if len(parts) != 5 or parts[4] not in results or item["state"] != "done":
            return self.reply(404, {"error": "Ergebnis nicht vorhanden"})
        file = ROOT / parts[3] / parts[4]
        if not file.is_file():
            return self.reply(404, {"error": "Datei fehlt"})
        self.send_response(200)
        self.send_header("Content-Type", results[parts[4]])
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", f'attachment; filename="{parts[4]}"')
        self.send_header("Content-Length", str(file.stat().st_size))
        self.end_headers()
        try:
            with file.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            pass

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    resume = []
    for path in ROOT.glob("*/status.json"):
        try:
            item = json.loads(path.read_text())
            if not VALID_ID.fullmatch(item["id"]):
                continue
            # Written off by an earlier version of this worker, when it was
            # restarted or at the time limit a stage had then: such a job is
            # taken up again like any unfinished one.
            interrupted = item["state"] == "error" and item.get("stage") in ("Unterbrochen", "Timeout")
            if item["state"] in ("running", "queued") or interrupted:
                adopt(item)
                if item["state"] == "running" or interrupted:
                    item["resumed"] = int(item.get("resumed", 0)) + 1
                item.pop("error", None)
                item["state"] = "running" if stage_alive(item) else "queued"
                if not stage_alive(item) and item.get("started"):
                    item["stage"] = "Wird fortgesetzt"
                save(item)
                resume.append(item)
            jobs[item["id"]] = item
        except (ValueError, OSError, KeyError):
            continue
    # Jobs whose stage is still running come first, the one that started its
    # stage first at the head (it holds the card, a later one waits for it and
    # would hide its progress), then the line as it was (each job keeps its
    # place in its status.json).
    alive = sorted((job for job in resume if stage_alive(job)), key=lambda job: job.get("pid_start") or 0)
    head = min([place_of(job) for job in resume] + [time.time()])
    for n, item in enumerate(alive):
        item["order"] = head - len(alive) + n
        save(item)
    for item in sorted(resume, key=place_of):
        pending.put_nowait(item["id"])
    threading.Thread(target=execute, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), API)
    server.daemon_threads = True
    server.serve_forever()

if __name__ == "__main__":
    main()
