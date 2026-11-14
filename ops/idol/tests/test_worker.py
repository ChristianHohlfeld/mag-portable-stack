#!/usr/bin/env python3
"""The Idol worker's promise: a job survives a restart of the worker (its
stage keeps running and is followed again), a reboot (the lost stage starts
again, a finished one is not repeated) and an earlier worker's "Unterbrochen".
The stages are scripted stand-ins; this proves the worker's bookkeeping, not
the GPU pipeline."""
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
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64

STAGE = r'''#!/usr/bin/env python3
import os, pathlib, sys, time
script, args = pathlib.Path(sys.argv[1]).name, sys.argv[2:]
delay = float(os.environ.get("FAKE_DELAY", "0.1"))
if script == "runner.py":
    if b"CUT" in pathlib.Path(args[args.index("--image") + 1]).read_bytes():
        print("Traceback (most recent call last):\nOSError: something long and technical", flush=True)
        print("MAG-ERROR Das Foto ist unvollständig angekommen: unten fehlen etwa 40 % des Bildes.", flush=True)
        sys.exit(2)
    out = pathlib.Path(args[args.index("--out") + 1])
    sys.stderr.write("Sampling:   0%|          | 0/50 [00:00<?, ?it/s]")
    sys.stderr.flush()
    for i in range(1, 51):
        time.sleep(delay)
        sys.stderr.write("\rSampling: %3d%%|#| %d/50 [00:01<00:%02d,  1.00s/it]" % (i * 2, i, 50 - i))
        sys.stderr.flush()
    (out / "input.ply").write_bytes(b"ply")
elif script == "objects.py":
    import json
    out = pathlib.Path(args[1])
    out.mkdir()
    (out / "mask-0.png").write_bytes(b"\x89PNG mask " + " ".join(args[2:]).encode())
    (out / "masks.json").write_text(json.dumps({"width": 4, "height": 3, "masks": [{"file": "mask-0.png", "box": [0, 0, 2, 2], "share": 0.3}]}))
elif script == "imagegen.py":
    import json
    folder = pathlib.Path(args[0])
    asked = json.loads((folder / "prompt.json").read_text())
    for i in range(1, 5):
        time.sleep(delay)
        print("MAG-IMAGE %d/4" % i, flush=True)
    (folder / "image.png").write_bytes(b"\x89PNG " + asked["prompt"].encode())
else:
    folder = pathlib.Path(args[1])
    if (folder.parent.parent / "mesh-fails").exists():
        print("MAG-ERROR Das Netz ließ sich nicht bauen.", flush=True)
        sys.exit(3)
    if (folder.parent.parent / "mesh-hangs").exists():
        time.sleep(600)                       # alive, and neither writing nor working
    time.sleep(delay * 8)
    print("MAG-STEP texture", flush=True)
    time.sleep(delay * 8)
    print("MAG-STEP export", flush=True)
    time.sleep(delay * 4)
    (folder / "mesh.glb").write_bytes(b"glTF")
    (folder / "mesh.stl").write_bytes(b"solid")
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="idol-"))
    root, stage = tmp / "jobs", tmp / "stage.py"
    stage.write_text(STAGE)
    stage.chmod(0o755)
    port = free_port()
    env = dict(os.environ, MAG_IDOL_JOB_ROOT=str(root), MAG_IDOL_PORT=str(port), MAG_IDOL_PYTHON=str(stage),
               MAG_IDOL_CPUS="", FAKE_DELAY="0.1", MAG_IDOL_STALL_S="2",
               MAG_TRIPOSG_PYTHON=str(tmp / "no-shape-python"),      # the whole shape stage is not installed here
               MAG_GPU_TEMP_FILE=str(tmp / "temp"),                 # the card, as hot as the test says
               MAG_GPU_HOLD_S="1", MAG_H3D_URL="http://127.0.0.1:9")                     # not the host's own h3d
    worker = [None]

    def call(method, path, body=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=body, method=method,
                                     headers={"X-Mag-Idol-UI": "1"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            with err:
                return err.code, json.loads(err.read() or b"{}")

    def start():
        worker[0] = subprocess.Popen([sys.executable, "-I", str(WORKER)], env=env)
        end = time.time() + 10
        while time.time() < end:
            try:
                if call("GET", "/api/idol/health")[0] == 200:
                    return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("worker did not start")

    def stop():
        worker[0].terminate()   # what systemd does with KillMode=process: the worker only
        worker[0].wait(timeout=10)

    def status(job):
        return call("GET", "/api/idol/jobs/" + job)[1]

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

    def order():
        code, job = call("POST", "/api/idol/jobs?quality=fast", PNG)
        assert code == 202, job
        return job["id"]

    try:
        start()
        # A picture from words is a job of the same queue, with its own result.
        code, image = call("POST", "/api/idol/images", json.dumps({"prompt": "ein Fuchs im Schnee", "width": 768, "height": 512}).encode())
        assert code == 202 and image["kind"] == "image", image
        s = wait(image["id"], lambda s: s["state"] == "done", "picture made")
        assert (s["files"], s["phases"]) == (["image.png"], 2), s
        with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/api/idol/jobs/%s/image.png" % (port, image["id"]), headers={"X-Mag-Idol-UI": "1"}), timeout=5) as res:
            assert res.headers["Content-Type"] == "image/png" and res.read() == b"\x89PNG " + "ein Fuchs im Schnee".encode()
        assert call("GET", "/api/idol/jobs/%s/mesh.glb" % image["id"])[0] == 404
        assert call("POST", "/api/idol/images", b'{"prompt": ""}')[0] == 400
        # The objects of a photo: the one at a place, or all; nothing of it stays behind.
        code, found = call("POST", "/api/idol/select?x=0.25&y=0.5", PNG)
        assert code == 200 and found["width"] == 4 and len(found["masks"]) == 1, found
        import base64
        assert base64.b64decode(found["masks"][0]["png"]) == b"\x89PNG mask --point 0.25 0.5" and "file" not in found["masks"][0], found
        assert base64.b64decode(call("POST", "/api/idol/select", PNG)[1]["masks"][0]["png"]).endswith(b"--all")
        assert base64.b64decode(call("POST", "/api/idol/select?scene=1", PNG)[1]["masks"][0]["png"]).endswith(b"--scene")
        assert base64.b64decode(call("POST", "/api/idol/select?pieces=1", PNG)[1]["masks"][0]["png"]).endswith(b"--pieces")
        assert call("POST", "/api/idol/select?x=2&y=0", PNG)[0] == 400 and call("POST", "/api/idol/select", b"not a picture")[0] == 415
        assert [p for p in root.iterdir() if p.name != image["id"]] == [], list(root.iterdir())
        assert call("GET", "/api/idol/health")[1]["resumable"] is True
        # 0. The card gets too hot while a job samples: its stage is held (no
        #    standstill, no error, nothing parked) and goes on once it is cool.
        (tmp / "temp").write_text("95")
        h = order()
        s = wait(h, lambda s: s.get("cooling") is True, "held for the heat")
        assert s["state"] == "running" and s["stage"] == "Grafikkarte kühlt ab", s
        time.sleep(4)                                  # longer than the standstill limit of 2 s
        s = status(h)
        assert s["state"] == "running" and s["cooling"] is True and s.get("resumed", 0) == 0, s
        (tmp / "temp").write_text("70")
        s = wait(h, lambda s: s["state"] == "done", "done after cooling down")
        assert s["cooling"] is False and (root / h / "idol.log").read_bytes().count(b" 1/50 ") == 1, s
        # 1. The worker restarts while a job samples: the stage keeps running,
        #    the new worker follows it, nothing starts twice.
        a = order()
        s = wait(a, lambda s: s["state"] == "running" and s.get("phase") == 2 and s["step"] >= 5, "sampling")
        assert (s["phases"], s["stage"]) == (5, "3D-Form berechnen"), s
        pid = s["pid"]
        stop()
        assert alive(pid), "the stage must outlive the worker"
        start()
        s = status(a)
        assert (s["state"], s["pid"], s["resumed"]) == ("running", pid, 1), s
        s = wait(a, lambda s: s["state"] == "done", "done after a worker restart")
        assert s["resumed"] == 1 and (root / a / "mesh.glb").read_bytes() == b"glTF"
        assert (root / a / "idol.log").read_bytes().count(b" 1/50 ") == 1   # sampled once
        # 2. Worker and stage both die mid-sampling (a reboot): the lost stage
        #    starts again by itself.
        b = order()
        s = wait(b, lambda s: s["state"] == "running" and s.get("phase") == 2 and s["step"] >= 5, "sampling")
        stop()
        os.killpg(s["pid"], signal.SIGKILL)
        time.sleep(0.3)
        start()
        s = status(b)
        assert s["state"] in ("queued", "running") and s["resumed"] >= 1 and "error" not in s, s
        s = wait(b, lambda s: s["state"] == "done", "done after a lost stage")
        assert (root / b / "idol.log").read_bytes().count(b" 1/50 ") == 2   # sampling had to start again
        # 3. The same during meshing: the finished sampling is not repeated.
        c = order()
        # (the job is in phase 3 a moment before the mesh stage has its process: wait for that)
        s = wait(c, lambda s: s["state"] == "running" and s.get("phase", 0) >= 3 and s.get("stage_key") == "mesh", "meshing")
        stop()
        os.killpg(s["pid"], signal.SIGKILL)
        time.sleep(0.3)
        start()
        s = wait(c, lambda s: s["state"] == "done", "done after a lost mesh stage")
        assert (root / c / "idol.log").read_bytes().count(b" 1/50 ") == 1, "sampling must not run again"
        assert s["phase"] == 5 and s["resumed"] >= 1, s
        # 3b. A job whose mesh stage failed is taken up again when it is asked for:
        #     what sampling left is still there, and only meshing is done again.
        (tmp / "mesh-fails").write_text("")
        f = order()
        s = wait(f, lambda s: s["state"] == "error", "a failed mesh stage")
        assert s["error"] == "Das Netz ließ sich nicht bauen." and (root / f / "splat.ply").is_file(), s
        assert call("POST", "/api/idol/jobs/%s/retry" % f) == (200, {"queued": True})     # (it fails the same way while the cause is there)
        s = wait(f, lambda s: s["state"] == "error" and s["resumed"] == 1, "failed again")
        (tmp / "mesh-fails").unlink()
        assert call("POST", "/api/idol/jobs/%s/retry" % f) == (200, {"queued": True})
        s = wait(f, lambda s: s["state"] == "done", "done after it was taken up again")
        assert "error" not in s and s["resumed"] == 2 and (root / f / "mesh.glb").read_bytes() == b"glTF", s
        assert (root / f / "idol.log").read_bytes().count(b" 1/50 ") == 1, "sampling must not run again"
        assert call("POST", "/api/idol/jobs/%s/retry" % f) == (200, {"queued": True}) and status(f)["state"] == "done"   # nothing to take up
        # A finished model is made anew from its splat when that is asked for: the mesh stage alone.
        splat = (root / f / "splat.ply").stat().st_mtime_ns
        (root / f / "mesh.glb").write_bytes(b"old")
        assert call("POST", "/api/idol/jobs/%s/retry?remesh=1" % f) == (200, {"queued": True})
        s = wait(f, lambda s: s["state"] == "done" and s.get("finished"), "made anew")
        assert (root / f / "mesh.glb").read_bytes() == b"glTF" and (root / f / "splat.ply").stat().st_mtime_ns == splat, "sampled once"
        assert call("POST", "/api/idol/jobs/%s/retry" % ("0" * 32))[0] == 404
        # A job that was cancelled while it sampled starts again, from the beginning.
        g = order()
        wait(g, lambda s: s["state"] == "running" and s.get("phase") == 2 and s["step"] >= 3, "sampling")
        assert call("POST", "/api/idol/jobs/%s/cancel" % g) == (200, {"cancelled": True})
        wait(g, lambda s: s["state"] == "cancelled", "cancelled")
        assert call("POST", "/api/idol/jobs/%s/retry" % g) == (200, {"queued": True})
        s = wait(g, lambda s: s["state"] == "done", "done after a cancelled job was taken up again")
        assert "error" not in s and (root / g / "idol.log").read_bytes().count(b" 1/50 ") == 2
        # 4. A job an earlier worker wrote off as interrupted is taken up again.
        stop()
        d = "d" * 32
        (root / d).mkdir()
        (root / d / "input.png").write_bytes(PNG)
        (root / d / "status.json").write_text(json.dumps({
            "id": d, "state": "error", "stage": "Unterbrochen", "error": "Dienst neu gestartet; bitte erneut senden",
            "quality": "fast", "created": int(time.time()), "started": int(time.time()), "step": 21, "total_steps": 50}))
        # ... and so is one that an earlier worker ended at the time limit a stage had then.
        t = "e" * 32
        (root / t).mkdir()
        (root / t / "input.png").write_bytes(PNG)
        (root / t / "splat.ply").write_bytes(b"ply")
        (root / t / "status.json").write_text(json.dumps({
            "id": t, "state": "error", "stage": "Timeout", "error": "Zeitlimit 15 Minuten überschritten", "stage_key": "mesh", "pid": 1,
            "quality": "fast", "created": int(time.time()), "started": int(time.time()), "step": 50, "total_steps": 50}))
        start()
        s = wait(d, lambda s: s["state"] == "done", "written-off job done")
        assert s["resumed"] == 1 and "error" not in s, s
        s = wait(t, lambda s: s["state"] == "done", "the job from the time limit done")
        assert s["resumed"] == 1 and "error" not in s and not (root / t / "idol.log").exists(), s    # from its mesh stage: it did not sample again
        # 4b. No stage has a time limit (every job here takes longer than the two
        #     seconds a standstill is in this test). A stage that stands still is
        #     ended and started again; one that never gets on fails, in words.
        (tmp / "mesh-hangs").write_text("")
        h = order()
        s = wait(h, lambda s: s["state"] == "running" and s.get("stage_key") == "mesh" and s.get("resumed", 0) >= 1, "a stage that stood still started again", 60)
        (tmp / "mesh-hangs").unlink()
        s = wait(h, lambda s: s["state"] == "done", "done after a standstill")
        assert s["resumed"] >= 1 and (root / h / "idol.log").read_bytes().count(b" 1/50 ") == 1, s
        (tmp / "mesh-hangs").write_text("")
        i = order()
        s = wait(i, lambda s: s["state"] == "error", "a stage that never gets on", 90)
        assert s["stage"] == "Stillstand" and s["error"].startswith("Kein Fortschritt mehr") and s["resumed"] == 3, s
        (tmp / "mesh-hangs").unlink()
        # 5. Two jobs queue up; a restart keeps both and their order. Cancel
        #    ends a stage for real, delete removes the folder.
        e, f = order(), order()
        wait(e, lambda s: s["state"] == "running" and s.get("pid"), "first of two running")
        assert status(f)["state"] == "queued"
        stop()
        start()
        assert (status(e)["state"], status(f)["state"]) == ("running", "queued")
        pid = status(e)["pid"]
        assert call("POST", "/api/idol/jobs/%s/cancel" % e)[1] == {"cancelled": True}
        end = time.time() + 10
        while alive(pid) and time.time() < end:
            time.sleep(0.05)
        assert not alive(pid) and status(e)["state"] == "cancelled"
        wait(f, lambda s: s["state"] == "running", "second job starts after the cancel")
        assert call("DELETE", "/api/idol/jobs/" + f)[1] == {"deleted": True}
        assert not (root / f).exists() and call("GET", "/api/idol/jobs/" + f)[0] == 404
        # 6. A stage that fails says why in words; that, not "Exit 2", is what the chat shows.
        code, job = call("POST", "/api/idol/jobs?quality=fast", PNG + b"CUT")
        assert code == 202, job
        end = time.time() + 20
        while status(job["id"])["state"] not in ("error", "done") and time.time() < end:
            time.sleep(0.05)
        s = status(job["id"])
        assert (s["state"], s["error"]) == ("error", "Das Foto ist unvollständig angekommen: unten fehlen etwa 40 % des Bildes."), s
        print("ok")
    finally:
        if worker[0] and worker[0].poll() is None:
            worker[0].kill()
        subprocess.run(["pkill", "-f", str(stage)], check=False)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
