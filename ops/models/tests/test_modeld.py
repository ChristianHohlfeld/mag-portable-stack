#!/usr/bin/env python3
"""mag-modeld contract: a bot starts by itself once its GGUF is complete and
verified, a partial file is "downloading" (never an error), a wrong checksum
is never started and never modified, and control verbs reach systemctl."""
import hashlib, http.server, json, os, pathlib, socket, subprocess, sys, tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parents[3]
BIN = pathlib.Path(os.environ.get("MAG_MODELD_BIN", ROOT / "build/mag-modeld"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="modeld-"))
    state, run, models = tmp / "state", tmp / "run", tmp / "models"
    for d in (state / "control", state / "slots", run, models):
        d.mkdir(parents=True)
    good, bad = b"GGUF" + os.urandom(300_000), b"GGUF" + os.urandom(1000)
    ports = {"lena": free_port(), "noah": free_port()}
    draft, mmproj = b"GGUF" + os.urandom(500), b"GGUF" + os.urandom(700)
    conf = {"runtime": {"rev": "0" * 40, "dir": str(tmp)}, "state_dir": str(state), "bots": {
        "lena": {"label": "L", "numa": 0, "port": ports["lena"], "ctx": 4096, "model": {
            "path": str(models / "lena.gguf"), "url": "https://models.invalid/lena.gguf", "size": len(good),
            "sha256": hashlib.sha256(good).hexdigest()},
            "draft": {"path": str(models / "lena-draft.gguf"), "url": "https://models.invalid/draft.gguf",
                      "size": len(draft), "sha256": hashlib.sha256(draft).hexdigest()},
            "mmproj": {"path": str(models / "lena-mmproj.gguf"), "url": "https://models.invalid/mmproj.gguf",
                       "size": len(mmproj), "sha256": hashlib.sha256(mmproj).hexdigest()}},
        "noah": {"label": "N", "numa": 1, "port": ports["noah"], "ctx": 4096, "model": {
            "path": str(models / "noah.gguf"), "url": "", "size": len(bad),
            "sha256": "0" * 64}}}}
    (tmp / "models.json").write_text(json.dumps(conf))
    # Fake systemctl: a unit is "active" once started; every call is logged.
    fake = tmp / "systemctl"
    fake.write_text(f"""#!/bin/sh
echo "$@" >> {tmp}/calls
case "$1" in
  show) if [ -e {tmp}/active.$2 ]; then echo ActiveState=active; else echo ActiveState=inactive; fi
        echo SubState=x; echo Result=success; echo NRestarts=0; echo FreezerState=running ;;
  start|restart) touch {tmp}/active.$3 ;;
  stop) rm -f {tmp}/active.$3 ;;
esac
""")
    fake.chmod(0o755)
    env = dict(os.environ, MAG_MODELS_RUN=str(run), MAG_MODELD_SYSTEMCTL=str(fake), MAG_MODELD_POLL_MS="300", MAG_MODELD_QUIET_S="2")
    proc = subprocess.Popen([str(BIN), "--config", str(tmp / "models.json")], env=env,
                            stderr=subprocess.PIPE, text=True)

    def status(bot):
        try:
            return json.loads((run / "status.json").read_text())["bots"][bot]
        except Exception:
            return {}

    def wait(bot, want, seconds=40):
        end = time.time() + seconds
        while time.time() < end:
            if status(bot).get("state") == want:
                return status(bot)
            time.sleep(0.2)
        raise AssertionError(f"{bot}: wanted {want}, got {status(bot)}")

    def calls():
        p = tmp / "calls"
        return p.read_text() if p.exists() else ""

    try:
        # 1. Partial download with a foreign writer (somebody's wget): reported
        #    with progress, nothing started, and above all no second writer.
        (models / "lena.gguf").write_bytes(good[:100_000])
        writer = open(models / "lena.gguf", "ab")
        s = wait("lena", "downloading")
        assert s["model"]["bytes"] == 100_000 and s["model"]["total"] == len(good), s
        assert wait("noah", "missing")
        time.sleep(4)  # twice the quiet time
        assert "start --no-block mag-model@" not in calls()
        assert "start --no-block mag-model-fetch@" not in calls(), calls()
        # The writer is gone and the file is still partial: resume exactly this
        # file, and never the drafter before the model is complete.
        writer.close()
        end = time.time() + 20
        while "start --no-block mag-model-fetch@lena-model.service" not in calls() and time.time() < end:
            time.sleep(0.2)
        assert "start --no-block mag-model-fetch@lena-model.service" in calls(), calls()
        assert "start --no-block mag-model-fetch@lena-draft" not in calls(), calls()
        # 2. Complete and correct: verified, started, ready when /health is 200.
        httpd = http.server.HTTPServer(("127.0.0.1", ports["lena"]), Health)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        with open(models / "lena.gguf", "ab") as f:
            f.write(good[100_000:])
        wait("lena", "ready")
        assert "start --no-block mag-model@lena.service" in calls()
        assert (state / "lena.model.ok").read_text().split()[2] == conf["bots"]["lena"]["model"]["sha256"]
        # The optional drafter is fetched on its own once the model is complete.
        end = time.time() + 20
        while "start --no-block mag-model-fetch@lena-draft.service" not in calls() and time.time() < end:
            time.sleep(0.2)
        assert "start --no-block mag-model-fetch@lena-draft.service" in calls(), calls()
        # The vision projector follows the same way: one file at a time.
        assert "mag-model-fetch@lena-mmproj" not in calls(), calls()
        (models / "lena-draft.gguf").write_bytes(draft)
        end = time.time() + 20
        while "start --no-block mag-model-fetch@lena-mmproj.service" not in calls() and time.time() < end:
            time.sleep(0.2)
        assert "start --no-block mag-model-fetch@lena-mmproj.service" in calls(), calls()
        assert status("lena")["mmproj"]["ok"] is False and status("lena")["mmproj"]["total"] == len(mmproj)
        # 3. Complete but wrong checksum: never started, file untouched.
        (models / "noah.gguf").write_bytes(bad)
        wait("noah", "corrupt")
        assert "mag-model@noah.service" not in calls().replace("show mag-model@noah.service", "")
        assert (models / "noah.gguf").read_bytes() == bad
        # 4. Control verbs: stop keeps the bot down until start.
        subprocess.check_call([str(BIN), "--config", str(tmp / "models.json"), "ctl", "lena", "stop"], env=env)
        wait("lena", "stopped")
        assert "stop --no-block mag-model@lena.service" in calls()
        time.sleep(1.5)
        assert status("lena")["state"] == "stopped"
        (state / "control" / "lena").write_text("rm -rf /\n")  # not a verb: ignored
        (state / "control" / "noah").write_text("start\n")     # corrupt stays down
        subprocess.check_call([str(BIN), "--config", str(tmp / "models.json"), "ctl", "lena", "start"], env=env)
        wait("lena", "ready")
        assert status("noah")["state"] == "corrupt"
        print("ok")
    finally:
        proc.terminate()
        err = proc.communicate(timeout=10)[1]
        if sys.exc_info()[0]:
            sys.stderr.write(err)


if __name__ == "__main__":
    main()
