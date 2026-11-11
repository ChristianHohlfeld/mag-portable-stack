#!/usr/bin/env python3
"""MAG scene worker: 3D models from the chat become animated scenes (Blender).

A scene is a job folder. Building it (scene.py build: what every figure is,
its skeleton, the motion) takes seconds and happens while the request waits,
so a bot that describes a scene wrongly is told at once what is wrong. The
built scene plays in the chat as it is. Its film is rendered only when it is
asked for, frame by frame on the CPU; that stage runs on its own (own
session, log file, exit code in a file), exactly like the stages of the
photo-to-3D worker: a restart of this worker (a deploy) finds it running and
follows it again, and after a reboot the render goes on with the first frame
that is missing.
CPU only: the MI50 belongs to photo-to-3D.
"""
import base64
import binascii
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

ROOT = Path(os.environ.get("MAG_SCENE_ROOT", "/var/lib/mag-scene/jobs"))
FILES = Path(os.environ.get("MAG_SCENE_FILES", "/var/lib/magbot/room/files"))  # the room's file store (read only)
BLENDER = os.environ.get("MAG_SCENE_BLENDER", "/opt/mag-scene/blender/blender")
FFMPEG = os.environ.get("MAG_SCENE_FFMPEG", "ffmpeg")
SCRIPT = str(Path(__file__).with_name("scene.py"))
PORT = int(os.environ.get("MAG_SCENE_PORT", "8795"))
CPUS = os.environ.get("MAG_SCENE_CPUS", "28-55")  # never the models' cores
BUILD_TIMEOUT, RENDER_TIMEOUT = 180, max(600, int(os.environ.get("MAG_SCENE_TIMEOUT_S", "5400")))
MAX_PENDING = 6
RESULTS = {"scene.glb": "model/gltf-binary", "scene.mp4": "video/mp4", "scene.blend": "application/octet-stream"}
PHASES = ("Skelette und Szene bauen", "Film rendern", "Video schreiben")
LOGS = {"build": "build.log", "render": "render.log", "encode": "encode.log"}
VALID_ID = re.compile(r"^[a-f0-9]{32}$")
STORED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
FRAME = re.compile(r"MAG-FRAME (\d+)/(\d+)")
jobs = {}
lock = threading.Lock()
building = threading.Lock()   # one Blender at a time builds; the render is queued
pending = queue.Queue()
abort = set()


class Lost(Exception):
    """A stage vanished without leaving its exit code (killed, reboot)."""


class Refused(Exception):
    """The scene cannot be built as described; the text says why."""


def save(job):
    folder = ROOT / job["id"]
    tmp = folder / "status.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / "status.json")


def update(job_id, **fields):
    with lock:
        item = jobs.get(job_id)  # None: discarded while it ran
        if item is None or item["state"] in ("built", "cancelled", "error", "done"):
            return False
        item.update(fields)
        if (ROOT / job_id).is_dir():
            save(item)
        return True


def proc_start(pid):
    """Start time of a live process; None when it is gone or only a zombie."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    fields = stat[stat.rindex(")") + 2:].split()
    return None if fields[0] == "Z" else int(fields[19])


def stage_alive(item):
    pid = item.get("pid")
    return bool(pid) and proc_start(pid) is not None and proc_start(pid) == item.get("pid_start")


def reap():
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
        if item["state"] in ("built", "done", "error", "cancelled"):
            return False
        abort.add(job_id)
        item["state"] = "cancelled"
        item["stage"] = "Abgebrochen"
        item["error"] = "Vom Benutzer abgebrochen"
        save(item)
        snapshot = dict(item)
    kill_stage(snapshot)
    return True


def film(job_id, voice=None):
    """Renders the film of a scene: when it is asked for, or again when it
    failed or was cancelled (what was rendered stays, the rest is rendered).
    `voice`: what its figures say, as a WAV the page made (the voices are
    made on the device, never here); it is the film's sound.
    True, or why not; None: no such job."""
    with lock:
        item = jobs.get(job_id)
        if item is None:
            return None
        if item["state"] in ("queued", "running"):
            return "Der Film wird schon gerendert."
        if item["state"] == "done":
            return "Den Film gibt es schon."
        if not (ROOT / job_id / "scene.blend").is_file():
            return "Die Szene ist nicht mehr vollständig."
        if pending.qsize() >= MAX_PENDING:
            return "Es warten schon zu viele Filme."
        abort.discard(job_id)
        if voice:
            (ROOT / job_id / "voice.wav").write_bytes(voice)
        item.update(state="queued", stage="Film wartet", pid=None, eta=None, started=int(time.time()))
        item.pop("error", None)
        item.pop("finished", None)
        save(item)
        pending.put_nowait(job_id)
    return True


def recipe(job_id):
    """The scene as it was described, for a new version of it: who plays and
    what happens (not where the files are). None: not known any more."""
    try:
        spec = json.loads((ROOT / job_id / "spec.json").read_text())
    except (OSError, ValueError):
        return None
    for actor in spec.get("actors") or []:
        actor.pop("file", None)
    return {key: value for key, value in spec.items() if value is not None}


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


def stage_env(folder, quality):
    env = os.environ.copy()
    home = folder / "home"  # Blender wants a home and a tmp of its own; both go with the job
    home.mkdir(exist_ok=True)
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home), "XDG_CACHE_HOME": str(home), "TMPDIR": str(home),
                "MAG_SCENE_QUALITY": quality, "PYTHONUNBUFFERED": "1"})
    return env


def blender(*args):
    command = [BLENDER, "-b", "--factory-startup", "--python-exit-code", "3", "-P", SCRIPT, "--", *args]
    return ["taskset", "-c", CPUS] + command if CPUS else command


def errors_of(text):
    lines = [line[len("MAG-ERROR "):].strip() for line in text.splitlines() if line.startswith("MAG-ERROR ")]
    return lines[-1] if lines else ""


def launch(job_id, key, command, quality):
    """Start one stage on its own: own session, output in its log, exit code
    in <stage>.exit. It keeps running when this worker does not."""
    folder = ROOT / job_id
    log_path, exit_path = folder / LOGS[key], folder / f"{key}.exit"
    exit_path.unlink(missing_ok=True)
    offset = log_path.stat().st_size if log_path.exists() else 0
    wrapper = ["/bin/sh", "-c", '"$@"; rc=$?; echo "$rc" > "$0.part"; mv "$0.part" "$0"', str(exit_path)] + command
    with log_path.open("ab") as log:
        proc = subprocess.Popen(wrapper, env=stage_env(folder, quality), stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
    update(job_id, stage_key=key, pid=proc.pid, pid_start=proc_start(proc.pid),
           stage_started=int(time.time()), log_offset=offset)


def follow(job_id, key, timeout, on_line):
    """Read a stage's log as it grows until its exit code is on disk."""
    folder = ROOT / job_id
    log_path, exit_path = folder / LOGS[key], folder / f"{key}.exit"
    with lock:
        item = dict(jobs[job_id])
    offset, linebuf, gone = int(item.get("log_offset") or 0), b"", None
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
            parts = re.split(b"[\r\n]", (linebuf + data)[-8192:])
            linebuf = parts[-1]
            for part in parts[:-1]:
                if part:
                    on_line(job_id, part.decode("utf-8", errors="replace"))
            update(job_id, log_offset=offset - len(linebuf))
            continue
        if code is not None:
            break
        if job_id in abort:
            kill_stage(item)
            raise InterruptedError("Job abgebrochen")
        if time.time() - float(item.get("stage_started") or time.time()) > timeout:
            kill_stage(item)
            raise TimeoutError(f"Zeitlimit {timeout // 60} Minuten überschritten")
        if stage_alive(item):
            gone = None
        else:
            gone = gone or time.monotonic()
            if time.monotonic() - gone > 3:  # no process and still no exit code
                raise Lost(key)
        time.sleep(0.5)
    if code:
        tail = ""
        try:
            tail = errors_of(log_path.read_text(errors="replace")[-20000:])
        except OSError:
            pass
        raise RuntimeError(tail or f"Prozess Exit {code}; Details im privaten {log_path.name}")


def run_stage(job_id, key, command, timeout, on_line, fresh, quality):
    """Run a stage to its end. One that is still running from before a restart
    of this worker (or finished meanwhile) is taken over; a lost one starts again."""
    for attempt in range(3):
        with lock:
            item = dict(jobs[job_id])
        known = item.get("stage_key") == key and bool(item.get("pid"))
        finished = known and attempt == 0 and read_exit(ROOT / job_id / f"{key}.exit") is not None
        if not (known and (stage_alive(item) or finished)):
            update(job_id, **fresh)
            launch(job_id, key, command, quality)
        try:
            return follow(job_id, key, timeout, on_line)
        except Lost:
            with lock:
                again = int(jobs[job_id].get("resumed", 0)) + 1 if job_id in jobs else 1
            update(job_id, pid=None, resumed=again)
    raise RuntimeError("Der Schritt wurde mehrfach unterbrochen")


def frame_line(job_id, line):
    found = FRAME.search(line)
    if not found:
        return
    done, total = int(found.group(1)), max(1, int(found.group(2)))
    with lock:
        item = jobs.get(job_id) or {}
        began, first = float(item.get("stage_started") or time.time()), int(item.get("first_frame") or 0)
    # Frames that were there already (a render that goes on) do not count for the pace.
    fresh = max(1, done - first)
    eta = int((time.time() - began) / fresh * (total - done)) if done < total else 0
    update(job_id, step=done, total_steps=total, progress=round(done / total, 3), eta=eta)


def process(job_id):
    folder = ROOT / job_id
    with lock:
        item = dict(jobs[job_id])
    quality, frames = item.get("quality", "normal"), int(item.get("frames") or 0)
    film = folder / "scene.mp4"
    stills = folder / "frames"

    def rendered():
        return len(list(stills.glob("f*.png"))) if stills.is_dir() else 0
    if not film.is_file():
        # A render that fails with frames to its name (the denoiser could not
        # get a thread, the machine was busy) is started again and goes on
        # where it stopped; one that gets nowhere is an error.
        while rendered() < frames or read_exit(folder / "render.exit") != 0:
            with lock:
                item = dict(jobs[job_id])
            have = rendered()
            if have and not stage_alive(item):
                # The frame a killed render was writing may be half a file: render it again.
                sorted(stills.glob("f*.png"))[-1].unlink(missing_ok=True)
                have -= 1
            update(job_id, first_frame=have)
            try:
                run_stage(job_id, "render", blender("render", str(folder)), RENDER_TIMEOUT, frame_line,
                          dict(phase=2, stage=PHASES[1], step=have, total_steps=frames, progress=round(have / max(1, frames), 3), eta=None), quality)
            except RuntimeError:
                if rendered() <= have + 1:
                    raise
                with lock:
                    again = int(jobs[job_id].get("resumed") or 0) + 1 if job_id in jobs else 1
                update(job_id, pid=None, resumed=again)
        encode = [FFMPEG, "-nostdin", "-v", "error", "-y", "-framerate", str(int(item.get("fps") or 24)), "-i", str(stills / "f%04d.png")]
        if (folder / "voice.wav").is_file():            # what the figures say: as long as the film, silent where nobody speaks
            encode += ["-i", str(folder / "voice.wav"), "-map", "0:v", "-map", "1:a", "-af", "apad", "-shortest", "-c:a", "aac", "-b:a", "128k"]
        encode += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium", "-movflags", "+faststart", str(folder / "scene.part.mp4")]
        if CPUS:
            encode = ["taskset", "-c", CPUS] + encode
        run_stage(job_id, "encode", encode, 600, lambda *_: None, dict(phase=3, stage=PHASES[2], eta=None), quality)
        os.replace(folder / "scene.part.mp4", film)
    shutil.rmtree(stills, ignore_errors=True)
    shutil.rmtree(folder / "home", ignore_errors=True)
    update(job_id, state="done", stage="Fertig", phase=len(PHASES), progress=1.0, files=[n for n in RESULTS if (folder / n).is_file()],
           finished=int(time.time()), pid=None)


def execute():
    while True:
        job_id = pending.get()
        try:
            with lock:
                item = jobs.get(job_id)
                state = item["state"] if item else "gone"
            if state not in ("queued", "running"):
                continue
            if not update(job_id, state="running", phases=len(PHASES)):
                continue
            process(job_id)
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


def room_file(stored):
    if not isinstance(stored, str) or not STORED.fullmatch(stored) or ".." in stored:
        raise Refused("Ungültiger Dateiname.")
    path = FILES / stored
    if not path.is_file():
        raise Refused("Das Modell gibt es im Raum nicht mehr.")
    return path


def run_blender(args, folder, log_name, timeout=BUILD_TIMEOUT):
    """A short Blender run that the request waits for."""
    with building:
        try:
            done = subprocess.run(blender(*args), env=stage_env(folder, "normal"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Refused("Blender hat zu lange gebraucht.")
        except OSError:
            raise Refused("Blender ist auf diesem Rechner nicht installiert.")
    text = done.stdout.decode("utf-8", errors="replace")
    (folder / log_name).write_text(text[-200000:], encoding="utf-8")
    if done.returncode != 0 or "MAG-ERROR " in text:
        raise Refused(errors_of(text) or "Blender konnte die Szene nicht bauen (Details im privaten %s)." % log_name)
    return text


def parts_of(stored):
    """The figures of a model, found once per file."""
    source = room_file(stored)
    cache = ROOT.parent / "parts"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / (stored + ".json")
    if not out.is_file() or out.stat().st_mtime < source.stat().st_mtime:
        scratch = cache / ("tmp-" + uuid4().hex)
        scratch.mkdir()
        try:
            run_blender(["parts", str(source), str(scratch / "parts.json")], scratch, "parts.log")
            os.replace(scratch / "parts.json", out)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    return json.loads(out.read_text())


def keep_log(folder):
    """What Blender said about a scene it could not build stays for a while
    (the newest twenty), so a failure can be understood afterwards."""
    try:
        kept = ROOT.parent / "failed"
        kept.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / LOGS["build"], kept / ("%d-%s.log" % (int(time.time()), folder.name[:6])))
        for old in sorted(kept.glob("*.log"))[:-20]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def create(spec):
    """Builds the scene now. Returns the job; its film is rendered on request
    (a room from before that, which does not say `film: false`, gets it at once)."""
    if not isinstance(spec, dict) or not isinstance(spec.get("actors"), list) or not spec["actors"]:
        raise Refused("Eine Szene braucht Darsteller (actors).")
    actors = []
    for entry in spec["actors"][:8]:
        if not isinstance(entry, dict):
            raise Refused("Ein Darsteller ist ein Objekt mit name und file.")
        actor = {k: entry[k] for k in ("name", "model", "part", "kind", "size", "at", "faces", "tall", "lift", "seen_from") if k in entry}
        actor["file"] = str(room_file(entry.get("file")))
        actors.append(actor)
    quality = spec.get("quality") if spec.get("quality") in ("fast", "normal") else "normal"
    clean = {"title": str(spec.get("title") or "Szene")[:80], "actors": actors,
             "actions": [a for a in (spec.get("actions") or []) if isinstance(a, dict)][:40],
             "ground": spec.get("ground"), "light": spec.get("light"), "seconds": spec.get("seconds"),
             "room": spec.get("room") if isinstance(spec.get("room"), dict) else None}
    at_once = spec.get("film") is not False
    with lock:
        if at_once and pending.qsize() >= MAX_PENDING:
            raise Refused("Es warten schon zu viele Szenen.")
    identity = uuid4().hex
    folder = ROOT / identity
    folder.mkdir(mode=0o700, parents=True)
    try:
        (folder / "spec.json").write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
        run_blender(["build", str(folder / "spec.json"), str(folder)], folder, LOGS["build"])
        facts = json.loads((folder / "scene.json").read_text())
    except Refused:
        keep_log(folder)
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except (OSError, ValueError):
        keep_log(folder)
        shutil.rmtree(folder, ignore_errors=True)
        raise Refused("Die Szene wurde nicht vollständig gebaut.")
    item = {"id": identity, "state": "built", "stage": "Bereit", "quality": quality, "created": int(time.time()),
            "title": facts.get("title"), "seconds": facts.get("seconds"), "frames": facts.get("frames"),
            "fps": facts.get("fps"), "actors": facts.get("actors"), "speech": facts.get("speech") or [], "phase": 1, "phases": len(PHASES), "step": 0,
            "total_steps": facts.get("frames"), "progress": 0.0, "files": ["scene.glb", "scene.blend"]}
    with lock:
        jobs[identity] = item
        save(item)
    if at_once:
        film(identity)
        with lock:
            item = dict(jobs[identity])
    return item


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

    def allowed(self):
        if self.headers.get("X-Mag-Scene-UI") == "1":
            return True
        self.reply(403, {"error": "MAG-Scene-Header fehlt"})
        return False

    def do_POST(self):
        url = urlsplit(self.path)
        parts = url.path.strip("/").split("/")
        if not self.allowed():
            return None
        if len(parts) == 5 and parts[:3] == ["api", "scene", "jobs"] and VALID_ID.fullmatch(parts[3]) and parts[4] in ("cancel", "film", "retry"):
            voice = None
            length = int(self.headers.get("Content-Length", "0") or 0)
            if parts[4] == "film" and 2 <= length <= 40_000_000:
                try:
                    told = json.loads(self.rfile.read(length))
                    voice = base64.b64decode(str(told.get("voice") or ""), validate=True) if isinstance(told, dict) else None
                except (ValueError, binascii.Error):
                    voice = None
                if voice and not (voice[:4] == b"RIFF" and voice[8:12] == b"WAVE"):
                    voice = None                      # only a WAV is the film's sound
            outcome = cancel(parts[3]) if parts[4] == "cancel" else film(parts[3], voice) if parts[4] == "film" else film(parts[3])
            if outcome is None:
                return self.reply(404, {"error": "Job nicht gefunden"})
            if parts[4] == "cancel":
                return self.reply(200, {"cancelled": outcome})
            return self.reply(200, {"queued": True} if outcome is True else {"queued": False, "error": outcome})
        if url.path == "/api/scene/purge":
            with lock:
                known = list(jobs)
            stray = [p.name for p in ROOT.iterdir() if p.is_dir() and VALID_ID.fullmatch(p.name)]
            return self.reply(200, {"deleted": sum(discard(i) for i in set(known + stray))})
        try:
            if url.path == "/api/scene/parts":
                return self.reply(200, {"parts": parts_of(parse_qs(url.query).get("file", [""])[0])})
            if url.path == "/api/scene/jobs":
                length = int(self.headers.get("Content-Length", "0") or 0)
                if not 2 <= length <= 200000:
                    return self.reply(413, {"error": "Szenenbeschreibung fehlt oder ist zu groß"})
                return self.reply(201, create(json.loads(self.rfile.read(length))))
        except Refused as why:
            return self.reply(400, {"error": str(why)})
        except ValueError:
            return self.reply(400, {"error": "JSON erwartet"})
        return self.reply(404, {"error": "Nicht gefunden"})

    def do_DELETE(self):
        parts = urlsplit(self.path).path.strip("/").split("/")
        if not self.allowed():
            return None
        if len(parts) != 4 or parts[:3] != ["api", "scene", "jobs"] or not VALID_ID.fullmatch(parts[3]):
            return self.reply(404, {"error": "Nicht gefunden"})
        return self.reply(200, {"deleted": discard(parts[3])})

    def do_GET(self):
        parts = urlsplit(self.path).path.strip("/").split("/")
        if parts == ["api", "scene", "health"]:
            with lock:
                active = sum(1 for item in jobs.values() if item["state"] in ("queued", "running"))
            return self.reply(200, {"ok": os.access(BLENDER, os.X_OK), "queued": pending.qsize(), "active": active, "resumable": True})
        if len(parts) < 4 or parts[:3] != ["api", "scene", "jobs"] or not VALID_ID.fullmatch(parts[3]):
            return self.reply(404, {"error": "Nicht gefunden"})
        with lock:
            item = dict(jobs.get(parts[3], {}))
        if not item:
            return self.reply(404, {"error": "Job nicht gefunden"})
        if len(parts) == 4:
            return self.reply(200, item)
        if len(parts) == 5 and parts[4] == "spec":
            spec = recipe(parts[3])
            return self.reply(200, {"spec": spec}) if spec else self.reply(404, {"error": "Die Beschreibung der Szene gibt es nicht mehr"})
        file = ROOT / parts[3] / parts[4] if len(parts) == 5 and parts[4] in RESULTS else None
        if file is None or not file.is_file() or (parts[4] == "scene.mp4" and item["state"] != "done"):
            return self.reply(404, {"error": "Ergebnis nicht vorhanden"})
        self.send_response(200)
        self.send_header("Content-Type", RESULTS[parts[4]])
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(file.stat().st_size))
        self.end_headers()
        try:
            with file.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            pass
        return None


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    resume = []
    for path in ROOT.glob("*/status.json"):
        try:
            item = json.loads(path.read_text())
            if not VALID_ID.fullmatch(item["id"]):
                continue
            if item["state"] in ("running", "queued"):
                if item["state"] == "running":
                    item["resumed"] = int(item.get("resumed", 0)) + 1
                item["state"] = "running" if stage_alive(item) else "queued"
                save(item)
                resume.append(item)
            jobs[item["id"]] = item
        except (ValueError, OSError, KeyError):
            continue
    for item in sorted(resume, key=lambda job: (not stage_alive(job), job.get("created", 0))):
        pending.put_nowait(item["id"])
    threading.Thread(target=execute, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), API)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
