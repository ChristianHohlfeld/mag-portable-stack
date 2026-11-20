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
    # A separate GGUF family may be selected for Lena while Noah stays on
    # its own process. No Gemma drafter/projector may leak into that profile.
    alternate = b"GGUF" + os.urandom(900)
    conf["profiles"] = {"alternate": {
        "label": "Another architecture", "reasoning_adapter": "mistral",
        "model": {"path": str(models / "alternate.gguf"), "url": "",
                  "size": len(alternate), "sha256": hashlib.sha256(alternate).hexdigest()},
        "args": []}}
    (tmp / "models.json").write_text(json.dumps(conf))
    # Fake systemctl: a unit is "active" once started, and knows since when; every call is logged.
    fake = tmp / "systemctl"
    fake.write_text(f"""#!/bin/sh
echo "$@" >> {tmp}/calls
case "$1" in
  show) if [ -e {tmp}/active.$2 ]; then echo ActiveState=active; echo ActiveEnterTimestampMonotonic=$(cat {tmp}/active.$2); else echo ActiveState=inactive; fi
        echo SubState=x; echo Result=success; echo NRestarts=0; echo FreezerState=running ;;
  start|restart)
    date +%s%N | cut -c1-16 > {tmp}/active.$3
    case "$3" in
      mag-model@*)
        bot=${{3#mag-model@}}; bot=${{bot%.service}}
        {BIN} --config {tmp}/models.json spec-hash "$bot" > {state}/slots/"$bot"/.spec
        ;;
    esac
    ;;
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
        # 5. `since` names the server process, not this supervisor: it is the
        #    moment the unit became active. A restart of the supervisor (every
        #    deploy is one) leaves it as it is, so the room keeps the bot's
        #    slot; a new server process has a new one.
        unit = tmp / "active.mag-model@lena.service"
        since = status("lena")["since"]
        assert since == int(unit.read_text()), (since, unit.read_text())
        proc.terminate()
        proc.communicate(timeout=10)
        (run / "status.json").unlink()
        time.sleep(1.1)
        proc = subprocess.Popen([str(BIN), "--config", str(tmp / "models.json")], env=env, stderr=subprocess.PIPE, text=True)
        assert wait("lena", "ready")["since"] == since, (status("lena"), since)
        subprocess.check_call([str(BIN), "--config", str(tmp / "models.json"), "ctl", "lena", "restart"], env=env)
        end = time.time() + 20
        while status("lena").get("since") == since and time.time() < end:
            time.sleep(0.2)
        assert wait("lena", "ready")["since"] not in (since, None), status("lena")
        # 6. Per-bot selection is persistent and isolated from the other bot.
        (models / "alternate.gguf").write_bytes(alternate)
        subprocess.check_call([str(BIN), "--config", str(tmp / "models.json"),
                               "ctl", "lena", "select:alternate"], env=env)
        end = time.time() + 30
        while time.time() < end:
            updated = status("lena")
            if updated.get("profile") == "alternate" and updated.get("state") == "ready":
                break
            time.sleep(0.2)
        assert status("lena")["profile"] == "alternate", status("lena")
        assert status("lena")["state"] == "ready", status("lena")
        assert status("lena")["reasoning_adapter"] == "mistral"
        assert status("lena")["file"] == "alternate", status("lena")
        assert "draft" not in status("lena") and "mmproj" not in status("lena"), status("lena")
        assert status("noah")["profile"] == "default"
        assert json.loads((state / "selection.json").read_text())["lena"] == "alternate"
        (state / "control" / "lena").write_text("select:../danger\\n")
        time.sleep(1)
        assert json.loads((state / "selection.json").read_text())["lena"] == "alternate"
        subprocess.check_call([str(BIN), "--config", str(tmp / "models.json"),
                               "ctl", "lena", "select:default"], env=env)
        end = time.time() + 30
        while time.time() < end:
            updated = status("lena")
            if updated.get("profile") == "default" and updated.get("state") == "ready": break
            time.sleep(0.2)
        assert status("lena")["profile"] == "default" and status("lena")["state"] == "ready"
        print("ok")
    finally:
        proc.terminate()
        err = proc.communicate(timeout=10)[1]
        if sys.exc_info()[0]:
            sys.stderr.write(err)


if __name__ == "__main__":
    main()
