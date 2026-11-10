#!/usr/bin/env python3
"""The scene worker's promises: a scene that cannot be built is refused in
words and leaves nothing behind; a built scene can be fetched at once, and
its film is rendered only when it is asked for; the film survives a restart
of the worker (the render keeps running and is followed again) and a reboot
(it goes on with the first frame that is missing). Blender and ffmpeg are
scripted stand-ins: this proves the worker's bookkeeping, not the pictures."""
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

WORKER = pathlib.Path(__file__).resolve().parents[1] / "worker.py"

BLENDER = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[sys.argv.index("--") + 1:]
with open(os.environ["FAKE_CALLS"], "a") as calls:
    calls.write(args[0] + "\n")
if args[0] == "parts":
    pathlib.Path(args[2]).write_text(json.dumps([{"part": 1, "kind": "Vierbeiner", "height": 0.2, "length": 0.5, "shape": "länglich"}]))
elif args[0] == "build":
    spec, out = json.loads(pathlib.Path(args[1]).read_text()), pathlib.Path(args[2])
    if any(actor.get("name") == "Niemand" for actor in spec["actors"]):
        print("MAG-ERROR Aktion für unbekannten Darsteller: Niemand.", flush=True)
        sys.exit(3)
    facts = {"title": spec["title"], "seconds": 0.5, "frames": 12, "fps": 24,
             "actors": [{"name": a["name"], "kind": "Vierbeiner", "notes": "Vierbeiner: 4 Beine"} for a in spec["actors"]]}
    (out / "scene.json").write_text(json.dumps(facts))
    (out / "scene.glb").write_bytes(b"glTF-scene")
    (out / "scene.blend").write_bytes(b"BLENDER")
    if spec["title"] in ("Stolpert", "Kaputt"):
        (out / spec["title"]).write_text("")
    print("MAG-BUILT " + json.dumps(facts), flush=True)
else:
    out = pathlib.Path(args[1])
    (out / "frames").mkdir(exist_ok=True)
    for i in range(12):
        frame = out / "frames" / ("f%04d.png" % i)
        if frame.exists():          # a render that goes on does not repeat what is there
            continue
        if (out / "Kaputt").exists() or (i == 6 and (out / "Stolpert").exists()):
            (out / "Stolpert").unlink(missing_ok=True)   # it stumbles once
            print("Error: OpenImageDenoise error: Resource temporarily unavailable", flush=True)
            sys.exit(3)
        time.sleep(float(os.environ.get("FAKE_DELAY", "0.1")))
        frame.write_bytes(b"png")
        print("MAG-FRAME %d/12" % (i + 1), flush=True)
    print("MAG-RENDERED 12", flush=True)
'''
FFMPEG = "#!/bin/sh\nfor last; do :; done\nprintf film > \"$last\"\n"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="scene-"))
    root, files, calls = tmp / "jobs", tmp / "files", tmp / "calls.log"
    files.mkdir()
    (files / "01ab_3d-fuchs.glb").write_bytes(b"glTF")
    for name, text in (("blender", BLENDER), ("ffmpeg", FFMPEG)):
        (tmp / name).write_text(text)
        (tmp / name).chmod(0o755)
    port = free_port()
    env = dict(os.environ, MAG_SCENE_ROOT=str(root), MAG_SCENE_FILES=str(files), MAG_SCENE_PORT=str(port), MAG_SCENE_BLENDER=str(tmp / "blender"),
               MAG_SCENE_FFMPEG=str(tmp / "ffmpeg"), MAG_SCENE_CPUS="", FAKE_CALLS=str(calls), FAKE_DELAY="0.15")
    worker = [None]

    def call(method, path, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, method=method, headers={"X-Mag-Scene-UI": "1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                payload = res.read()
                return res.status, payload if raw else json.loads(payload)
        except urllib.error.HTTPError as err:
            with err:
                return err.code, json.loads(err.read() or b"{}")

    def start():
        worker[0] = subprocess.Popen([sys.executable, "-I", str(WORKER)], env=env)
        end = time.time() + 10
        while time.time() < end:
            try:
                if call("GET", "/api/scene/health")[0] == 200:
                    return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("worker did not start")

    def stop():
        worker[0].terminate()   # what systemd does with KillMode=process: the worker only
        worker[0].wait(timeout=10)

    def status(job):
        return call("GET", "/api/scene/jobs/" + job)[1]

    def wait(job, test, what, seconds=40):
        end = time.time() + seconds
        while time.time() < end:
            s = status(job)
            if test(s):
                return s
            assert s.get("state") != "error", s
            time.sleep(0.05)
        raise AssertionError("timeout: %s; %s" % (what, status(job)))

    def alive(pid):
        try:
            return pathlib.Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    def scene(name="Fuchs", **more):
        return dict({"title": "Probe", "actors": [{"name": name, "file": "01ab_3d-fuchs.glb", "model": "3d-fuchs.glb", "part": 1}],
                     "actions": [{"actor": name, "do": "jump"}], "ground": "snow", "film": False}, **more)

    def filmed(**more):
        """A scene whose film was asked for."""
        job = call("POST", "/api/scene/jobs", scene(**more))[1]["id"]
        assert call("POST", "/api/scene/jobs/%s/film" % job) == (200, {"queued": True})
        return job

    try:
        start()
        assert call("GET", "/api/scene/health")[1]["ok"] is True
        # The figures of a model are found once per file.
        for _ in range(2):
            code, out = call("POST", "/api/scene/parts?file=01ab_3d-fuchs.glb")
            assert (code, out["parts"][0]["kind"]) == (200, "Vierbeiner"), out
        assert calls.read_text().split() == ["parts"]
        assert call("POST", "/api/scene/parts?file=../etc/passwd")[0] == 400
        assert call("POST", "/api/scene/parts?file=fehlt.glb") == (400, {"error": "Das Modell gibt es im Raum nicht mehr."})
        # A scene that cannot be built is refused in the builder's words and leaves nothing behind.
        code, out = call("POST", "/api/scene/jobs", scene("Niemand"))
        assert (code, out) == (400, {"error": "Aktion für unbekannten Darsteller: Niemand."}), out
        assert call("POST", "/api/scene/jobs", {"actors": []})[0] == 400
        assert not [p for p in root.iterdir() if p.is_dir()], "a refused scene must not leave a folder"
        # 1. A built scene is there at once and stays as it is: no film is
        #    rendered that nobody asked for. How it was described can be read
        #    again (for a new version of it), without the room's file paths.
        code, job = call("POST", "/api/scene/jobs", scene())
        assert code == 201 and (job["state"], job["frames"], job["actors"][0]["kind"], job["phases"]) == ("built", 12, "Vierbeiner", 3), job
        a = job["id"]
        assert call("GET", "/api/scene/jobs/%s/scene.glb" % a, raw=True) == (200, b"glTF-scene")
        assert call("GET", "/api/scene/jobs/%s/scene.blend" % a, raw=True) == (200, b"BLENDER")
        assert call("GET", "/api/scene/jobs/%s/scene.mp4" % a)[0] == 404
        assert call("GET", "/api/scene/jobs/%s/spec" % a) == (200, {"spec": {"title": "Probe", "actors": [{"name": "Fuchs", "model": "3d-fuchs.glb", "part": 1}],
                                                                     "actions": [{"actor": "Fuchs", "do": "jump"}], "ground": "snow"}})
        assert call("POST", "/api/scene/jobs/%s/cancel" % a) == (200, {"cancelled": False})    # nothing runs
        time.sleep(0.5)
        assert status(a)["state"] == "built" and calls.read_text().split() == ["parts", "build", "build"] and call("GET", "/api/scene/health")[1]["active"] == 0
        # The film is asked for. The worker restarts while it renders: the
        # render keeps running, the new worker follows it, no frame is
        # rendered twice.
        assert call("POST", "/api/scene/jobs/%s/film" % a) == (200, {"queued": True})
        assert call("POST", "/api/scene/jobs/%s/film" % a) == (200, {"queued": False, "error": "Der Film wird schon gerendert."})
        assert call("GET", "/api/scene/jobs/%s/scene.mp4" % a)[0] == 404        # not before it is finished
        s = wait(a, lambda s: s["state"] == "running" and s.get("step", 0) >= 3, "rendering")
        assert (s["phase"], s["stage"], s["total_steps"]) == (2, "Film rendern", 12), s
        pid = s["pid"]
        stop()
        assert alive(pid), "the render must outlive the worker"
        start()
        s = status(a)
        assert (s["state"], s["pid"], s["resumed"]) == ("running", pid, 1), s
        s = wait(a, lambda s: s["state"] == "done", "done after a worker restart")
        assert s["resumed"] == 1 and s["files"] == ["scene.glb", "scene.mp4", "scene.blend"], s
        assert call("GET", "/api/scene/jobs/%s/scene.mp4" % a, raw=True) == (200, b"film")
        assert (root / a / "render.log").read_text().count("MAG-FRAME 2/12") == 1
        assert not (root / a / "frames").exists()                                # the stills go once the film is written
        assert call("POST", "/api/scene/jobs/%s/film" % a) == (200, {"queued": False, "error": "Den Film gibt es schon."})
        # A built scene that waits for nothing survives a restart as it is.
        idle = call("POST", "/api/scene/jobs", scene())[1]["id"]
        stop()
        start()
        assert status(idle)["state"] == "built" and call("GET", "/api/scene/health")[1]["active"] == 0
        # A room from before films on request does not say `film`: it gets the film at once, and may ask for it again by the old name.
        code, job = call("POST", "/api/scene/jobs", {k: v for k, v in scene().items() if k != "film"})
        assert code == 201 and job["state"] == "queued", job
        old = job["id"]
        wait(old, lambda s: s["state"] == "running" and s.get("step", 0) >= 2, "rendering at once")
        assert call("POST", "/api/scene/jobs/%s/cancel" % old) == (200, {"cancelled": True})
        assert call("POST", "/api/scene/jobs/%s/retry" % old) == (200, {"queued": True})
        wait(old, lambda s: s["state"] == "done", "film by the old name")
        # 2. Worker and render both die (a reboot): the film goes on with the
        #    first frame that is missing.
        b = filmed()
        s = wait(b, lambda s: s["state"] == "running" and s.get("step", 0) >= 5, "rendering")
        stop()
        os.killpg(s["pid"], signal.SIGKILL)
        time.sleep(0.3)
        start()
        s = status(b)
        assert s["state"] in ("queued", "running") and s["resumed"] >= 1 and "error" not in s, s
        s = wait(b, lambda s: s["state"] == "done", "done after a lost render")
        log = (root / b / "render.log").read_text()
        assert log.count("MAG-FRAME 2/12") == 1 and log.count("MAG-FRAME 12/12") == 1, log   # early frames once, all of them in the end
        # 3. Cancelling ends the film, not the scene; deleting removes the folder.
        c = filmed()
        s = wait(c, lambda s: s["state"] == "running" and s.get("step", 0) >= 2, "rendering")
        assert call("POST", "/api/scene/jobs/%s/cancel" % c) == (200, {"cancelled": True})
        assert status(c)["state"] == "cancelled" and not alive(s["pid"])
        assert call("GET", "/api/scene/jobs/%s/scene.glb" % c, raw=True)[0] == 200
        # A film that was cancelled (or failed) can be rendered after all: what was rendered stays.
        before = (root / c / "render.log").read_text().count("MAG-FRAME 1/12")
        assert call("POST", "/api/scene/jobs/%s/film" % c) == (200, {"queued": True})
        s = wait(c, lambda s: s["state"] == "done", "film after all")
        assert "error" not in s and (root / c / "render.log").read_text().count("MAG-FRAME 1/12") == before
        c = filmed()
        wait(c, lambda s: s["state"] == "running" and s.get("step", 0) >= 2, "rendering")
        assert call("POST", "/api/scene/jobs/%s/cancel" % c) == (200, {"cancelled": True})
        assert call("DELETE", "/api/scene/jobs/" + c) == (200, {"deleted": True}) and not (root / c).exists()
        assert call("GET", "/api/scene/jobs/" + c)[0] == 404
        # 4. A render that fails halfway (the denoiser got no thread) is started
        #    again and goes on; one that gets nowhere is an error, in words.
        d = filmed(title="Stolpert")
        s = wait(d, lambda s: s["state"] == "done", "film after a render that stumbled")
        log = (root / d / "render.log").read_text()
        assert s["resumed"] == 1 and log.count("OpenImageDenoise error") == 1 and log.count("MAG-FRAME 3/12") == 1, (s, log)
        e = filmed(title="Kaputt")
        end = time.time() + 20
        while status(e)["state"] not in ("error", "done") and time.time() < end:
            time.sleep(0.05)
        assert status(e)["state"] == "error" and "Exit 3" in status(e)["error"], status(e)
        assert call("GET", "/api/scene/jobs/%s/scene.glb" % e, raw=True)[0] == 200              # the scene itself is there
        # What Blender said about a scene it could not build is kept.
        assert len(list((tmp / "failed").glob("*.log"))) == 1 and "Niemand" in next((tmp / "failed").glob("*.log")).read_text()
        assert call("POST", "/api/scene/purge")[1]["deleted"] == 7 and not [p for p in root.iterdir() if p.is_dir()]
        # Only the room may ask.
        req = urllib.request.Request("http://127.0.0.1:%d/api/scene/purge" % port, data=b"", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("a request without the header must be refused")
        except urllib.error.HTTPError as err:
            with err:
                assert err.code == 403
        print("scene worker ok")
    finally:
        if worker[0] and worker[0].poll() is None:
            try:
                call("POST", "/api/scene/purge")   # ends a render that is still under way
            except OSError:
                pass
            worker[0].kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
