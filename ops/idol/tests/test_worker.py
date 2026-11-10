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
    out = pathlib.Path(args[args.index("--out") + 1])
    sys.stderr.write("Sampling:   0%|          | 0/50 [00:00<?, ?it/s]")
    sys.stderr.flush()
    for i in range(1, 51):
        time.sleep(delay)
        sys.stderr.write("\rSampling: %3d%%|#| %d/50 [00:01<00:%02d,  1.00s/it]" % (i * 2, i, 50 - i))
        sys.stderr.flush()
    (out / "input.ply").write_bytes(b"ply")
else:
    folder = pathlib.Path(args[1])
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
               MAG_IDOL_CPUS="", FAKE_DELAY="0.1")
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
        assert call("GET", "/api/idol/health")[1]["resumable"] is True
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
        s = wait(c, lambda s: s["state"] == "running" and s.get("phase", 0) >= 3, "meshing")
        stop()
        os.killpg(s["pid"], signal.SIGKILL)
        time.sleep(0.3)
        start()
        s = wait(c, lambda s: s["state"] == "done", "done after a lost mesh stage")
        assert (root / c / "idol.log").read_bytes().count(b" 1/50 ") == 1, "sampling must not run again"
        assert s["phase"] == 5 and s["resumed"] >= 1, s
        # 4. A job an earlier worker wrote off as interrupted is taken up again.
        stop()
        d = "d" * 32
        (root / d).mkdir()
        (root / d / "input.png").write_bytes(PNG)
        (root / d / "status.json").write_text(json.dumps({
            "id": d, "state": "error", "stage": "Unterbrochen", "error": "Dienst neu gestartet; bitte erneut senden",
            "quality": "fast", "created": int(time.time()), "started": int(time.time()), "step": 21, "total_steps": 50}))
        start()
        s = wait(d, lambda s: s["state"] == "done", "written-off job done")
        assert s["resumed"] == 1 and "error" not in s, s
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
        print("ok")
    finally:
        if worker[0] and worker[0].poll() is None:
            worker[0].kill()
        subprocess.run(["pkill", "-f", str(stage)], check=False)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
