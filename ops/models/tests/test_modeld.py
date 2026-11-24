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


def test_hip_build():
    """A matching legacy binary and stale build tree cannot satisfy the PIC build."""
    rev = json.loads((ROOT / "ops/models/models.json").read_text())["runtime"]["rev"]
    with tempfile.TemporaryDirectory(prefix="model-hip-") as work:
        home = pathlib.Path(work)
        src = home / ".local/src/mag-coding-llama.cpp-hip"
        stale = src / "build-mag-models"
        stale.mkdir(parents=True)
        (stale / "CMakeCache.txt").write_text("CMAKE_POSITION_INDEPENDENT_CODE:BOOL=OFF\n")
        (src / ".git").mkdir()
        (src / "src").mkdir()
        (src / "src/llama-arch.cpp").write_text("LLM_ARCH_GEMMA4 LLM_ARCH_OPENAI_MOE\n")

        def executable(path, script):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/bash\nset -euo pipefail\n" + script)
            path.chmod(0o755)

        bin_dir = home / ".local/mag-coding-rt" / rev / "hip-gfx906/bin"
        executable(bin_dir / "llama-server", "echo legacy\n")
        (bin_dir / "source-commit").write_text(rev + "\n")
        executable(home / "idol-mi50/rocm-6.3.4/current/lib/llvm/bin/clang++", "exit 1\n")
        tools = home / "tools"
        executable(tools / "getent", 'printf "user:x:0:0::%s:/bin/bash\\n" "$TEST_HOME"\n')
        executable(tools / "git", 'if [[ $3 == rev-parse ]]; then echo "$TEST_REV"; fi\n')
        executable(tools / "cmake", r"""printf '%s\n' "$*" >> "$TEST_HOME/cmake.log"
if [[ $1 == --build ]]; then
  mkdir -p "$2/bin"
  printf '#!/bin/bash\necho rebuilt\n' > "$2/bin/llama-server"
  chmod +x "$2/bin/llama-server"
fi
""")
        env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}", MAG_BUILD_PINNED="1",
                   MAG_OWNER=subprocess.check_output(["id", "-un"], text=True).strip(),
                   MAG_HOST_ENV=str(home / "no-host-env"), MAG_IDOL_BASE=str(home / "idol-mi50"),
                   TEST_HOME=work, TEST_REV=rev)
        command = ["bash", str(ROOT / "ops/models/build-runtime.sh"), "hip"]
        assert "rebuilt" in subprocess.check_output(command, env=env, text=True)
        log = (home / "cmake.log").read_text().splitlines()
        assert len(log) == 2 and str(stale) + "-hip-pic-" + rev in log[0], log
        assert "-DCMAKE_POSITION_INDEPENDENT_CODE=ON" in log[0], log
        assert "-DCMAKE_HIP_FLAGS=-fPIC" in log[0], log
        assert "already built" in subprocess.check_output(command, env=env, text=True)
        assert (home / "cmake.log").read_text().splitlines() == log


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
    # Fake systemctl: a unit is "active" once started, and knows since when; every call is logged.
    fake = tmp / "systemctl"
    fake.write_text(f"""#!/bin/sh
echo "$@" >> {tmp}/calls
case "$1" in
  show) if [ -e {tmp}/active.$2 ]; then echo ActiveState=active; echo ActiveEnterTimestampMonotonic=$(cat {tmp}/active.$2); else echo ActiveState=inactive; fi
        echo SubState=x; echo Result=success; echo NRestarts=0; echo FreezerState=running ;;
  start|restart) date +%s%N | cut -c1-16 > {tmp}/active.$3 ;;
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
        # Offline weights are discovered by quantization and never downloaded.
        local = models / "qwen"
        local.mkdir()
        path = local / "Qwen3.8-27B-Q6_K.gguf"
        path.write_bytes(good)
        projector = local / "mmproj-F16.gguf"
        projector.write_bytes(mmproj)
        file = tmp / "models.json"
        conf["bots"]["qwen"] = {"gpu": "mi50", "port": free_port(), "ctx": 16384,
                                  "cpus": str(min(os.sched_getaffinity(0))), "threads": 2,
                                  "model": {"local_dir": str(local)},
                                  "mmproj": {"path": str(projector), "local": True}}
        file.write_text(json.dumps(conf))
        command = [str(BIN), "--config", str(file)]
        assert subprocess.check_output(command + ["model-path", "qwen"], text=True).strip() == str(path)
        second = local / "another-Q6_K.gguf"
        second.write_bytes(good)
        assert subprocess.run(command + ["model-path", "qwen"]).returncode == 78
        second.unlink()
        proc.terminate()
        proc.communicate(timeout=10)
        qwen_http = http.server.HTTPServer(("127.0.0.1", conf["bots"]["qwen"]["port"]), Health)
        threading.Thread(target=qwen_http.serve_forever, daemon=True).start()
        qwen_slots = state / "slots/qwen"
        qwen_slots.mkdir()
        gpu_state = qwen_slots / ".gpu"
        gpu_state.write_text("waiting\n")
        proc = subprocess.Popen(command, env=env, stderr=subprocess.PIPE, text=True)
        assert wait("qwen", "waiting_runtime")["gpu"] == "MI50"
        runtime = tmp / ("0" * 40) / "hip-gfx906/bin/llama-server"
        runtime.parent.mkdir(parents=True)
        runtime.touch()
        runtime.chmod(0o755)
        assert wait("qwen", "waiting_gpu")["gpu"] == "MI50"
        assert status("qwen")["mmproj"]["ok"] is True
        assert "mag-model-fetch@qwen" not in calls()
        # Exercise the installed launcher with the supervisor's real stamps.
        (tmp / "mag-modeld").symlink_to(BIN.resolve())
        launch = tmp / "run-mi50.sh"
        launch.write_bytes((ROOT / "ops/models/run-mi50.sh").read_bytes())
        (tmp / "gpu-guard.py").write_text(
            "import json, sys; print(json.dumps(sys.argv[3:]))\n"
        )
        def launch_args():
            return json.loads(subprocess.check_output(
                ["bash", str(launch), "qwen"], env=dict(env, MAG_MODELS_CONF=str(file)), text=True
            ))

        args = launch_args()
        assert args[args.index("--mmproj") + 1] == str(projector)
        assert (qwen_slots / ".spec").read_text().endswith("draft=0 mmproj=3\n")
        assert status("qwen")["mmproj"]["total"] == len(mmproj)
        gpu_state.write_text("running 1234\n")
        ready = wait("qwen", "ready")
        assert ready["since"] == 1234 and ready["mmproj"]["active"]
        (state / "qwen.mmproj.off").touch()
        assert "--mmproj" not in launch_args()
        assert (qwen_slots / ".spec").read_text().endswith("draft=0 mmproj=0\n")
        gpu_state.write_text("waiting\n")
        assert "since" not in wait("qwen", "waiting_gpu")
        gpu_state.write_text("running 5678\n")
        assert wait("qwen", "ready")["since"] == 5678
        assert path.read_bytes() == good
        print("ok")
    finally:
        proc.terminate()
        err = proc.communicate(timeout=10)[1]
        if sys.exc_info()[0]:
            sys.stderr.write(err)


if __name__ == "__main__":
    test_hip_build()
    main()
