#!/usr/bin/env python3
"""mag-modeld contract: a bot starts by itself once its GGUF is complete and
verified, a partial file is "downloading" (never an error), a wrong checksum
is never started and never modified, and control verbs reach systemctl."""
import hashlib, http.server, json, os, pathlib, socket, struct, subprocess, sys, tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parents[3]
BIN = pathlib.Path(os.environ.get("MAG_MODELD_BIN", ROOT / "build/mag-modeld"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(getattr(self.server, "health", 200))
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def test_unit_refresh():
    """Install over the old 20s template with only a manager reload."""
    with tempfile.TemporaryDirectory(prefix="model-units-") as work:
        root = pathlib.Path(work)
        units = root / "units"
        units.mkdir()
        template = units / "mag-model@.service"
        template.write_text("[Service]\nTimeoutStopSec=20s\n")
        systemctl = root / "systemctl"
        systemctl.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_CALLS"\n')
        systemctl.chmod(0o755)
        calls = root / "calls"
        env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", TEST_CALLS=str(calls),
                   MAG_OWNER=subprocess.check_output(["id", "-un"], text=True).strip(), MAG_HOST_ENV=str(root / "no-host-env"))
        command = ["bash", str(ROOT / "ops/models/install-units.sh"), str(units)]
        subprocess.check_call(command, env=env)
        assert "TimeoutStopSec=5min" in template.read_text()
        assert f"User={env['MAG_OWNER']}" in template.read_text()
        assert "KillMode=mixed" in (units / "mag-model@qwen.service.d/20-mi50.conf").read_text()
        for bot, cpus, node in (("lena", "0-13", 0), ("noah", "14-27", 1)):
            spec = json.loads((ROOT / "ops/models/models.json").read_text())["bots"][bot]
            dropin = units / f"mag-model@{bot}.service.d/20-cpu-numa.conf"
            assert spec["cpus"] == cpus and spec["numa"] == node
            assert dropin.read_text() == (
                f"[Service]\nCPUAffinity={cpus}\nAllowedCPUs={cpus}\nAllowedMemoryNodes={node}\n"
            )
        assert not (units / "mag-model@qwen.service.d/20-cpu-numa.conf").exists()
        assert calls.read_text() == "daemon-reload\n"


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
        assert len(log) == 2 and str(stale) + "-hip-pic-shutdown-" + rev in log[0], log
        assert "-DCMAKE_POSITION_INDEPENDENT_CODE=ON" in log[0], log
        assert "-DCMAKE_HIP_FLAGS=-fPIC" in log[0], log
        assert "already built" in subprocess.check_output(command, env=env, text=True)
        assert (home / "cmake.log").read_text().splitlines() == log


def test_mi50_mtp():
    defaults = json.loads((ROOT / "ops/models/models.json").read_text())
    with tempfile.TemporaryDirectory(prefix="model-mtp-") as work:
        tmp = pathlib.Path(work)
        launcher = ROOT / "ops/models/run-mi50.sh"
        model = tmp / "Qwen3.8-27B-Q6_K.gguf"
        state = tmp / "state"
        state.mkdir()
        config = tmp / "models.json"
        qwen = defaults["bots"]["qwen"]
        qwen.update(model={"local_dir": work}, cpus=str(min(os.sched_getaffinity(0))),
                    mmproj=None, port=free_port())
        qwen["args"] += ["--chat-template", "value with spaces"]
        defaults.update(runtime=dict(defaults["runtime"], dir=work), state_dir=str(state))
        config.write_text(json.dumps(defaults))
        (tmp / "mag-modeld").symlink_to(BIN.resolve())
        server = tmp / defaults["runtime"]["rev"] / "hip-gfx906/bin/llama-server"
        server.parent.mkdir(parents=True)
        server.write_text("""#!/usr/bin/env python3
import http.server, json, os, sys
args = sys.argv[1:]
print(json.dumps(args), flush=True)
if "--spec-type" not in args:
    sys.exit(78)  # the guard returns the child status without a relaunch
if os.environ["TEST_MTP_START"] == "fail":
    sys.exit(42)
class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass
server = http.server.HTTPServer(("127.0.0.1", int(args[args.index("--port") + 1])), Health)
server.handle_request()
sys.exit(78)
""")
        server.chmod(0o755)
        lock = tmp / "gpu.lock"
        lock.touch()
        (tmp / "gpu.lock.idol").touch()
        env = dict(os.environ, MAG_MODELS_CONF=str(config), MAG_GPU_LOCK=str(lock),
                   MAG_H3D_PORT=str(free_port()))

        def string(value):
            return struct.pack("<Q", len(value)) + value

        def write_gguf(tensor):
            # A metadata string containing MTP must never trigger detection.
            contents = b"GGUF" + struct.pack("<IQQ", 3, 1, 1)
            contents += string(b"tokenizer.example") + struct.pack("<IIQ", 9, 8, 1)
            contents += string(b"blk.64.nextn.eh_proj.weight")
            contents += string(tensor) + struct.pack("<IQIQ", 1, 1, 0, 0) + b"\0" * 64
            model.write_bytes(contents)
            (state / "qwen.model.ok").write_text(
                f"{len(contents)} {int(model.stat().st_mtime)} {hashlib.sha256(contents).hexdigest()} {model}\n"
            )

        def launch(mode):
            run = subprocess.run(["bash", str(launcher), "qwen"], env=dict(env, TEST_MTP_START=mode),
                                 text=True, capture_output=True, timeout=10)
            assert run.returncode == 78, run
            spec = (state / "slots/qwen/.spec").read_text()
            return [json.loads(line) for line in run.stdout.splitlines()], spec, run.stderr

        write_gguf(b"blk.0.attn_q.weight")
        calls, spec, _ = launch("fail")
        assert len(calls) == 1 and "--spec-type" not in calls[0]
        assert spec.split()[1:] == ["draft=0", "mmproj=0", "runtime=upstream"]
        normal = calls[0]
        for flag, value in (("--flash-attn", "on"), ("-ctk", "q8_0"), ("-ctv", "q8_0"),
                            ("--chat-template", "value with spaces"), ("--model", str(model))):
            assert normal[normal.index(flag) + 1] == value, normal

        write_gguf(b"blk.64.nextn.eh_proj.weight")
        calls, spec, log = launch("fail")
        assert calls == [normal + qwen["mtp_args"], normal], calls
        assert spec.split()[1:] == ["draft=0", "mmproj=0", "runtime=upstream"]
        assert "retrying llama-server without MTP" in log

        calls, spec, _ = launch("ready")
        assert calls == [normal + qwen["mtp_args"]], calls
        assert spec.split()[1:] == ["draft=4", "mmproj=0", "runtime=upstream"]


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
        echo SubState=running
        echo Result=success
        if [ -e {tmp}/restarts.$2 ]; then echo NRestarts=1; else echo NRestarts=0; fi
        if [ -e {tmp}/frozen.$2 ]; then echo FreezerState=frozen; else echo FreezerState=running; fi ;;
  thaw) [ "$2" != "--no-block" ] || exit 99
        [ -e {tmp}/frozen.$2 ] && rm {tmp}/frozen.$2 ;;
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
        # Re-adopt a live, busy process after restarting the supervisor.
        # The old restart-count fallback would restart the process after a
        # counter increase before checking health, even though it was alive.
        unit = tmp / "active.mag-model@lena.service"
        since = status("lena")["since"]
        assert since == int(unit.read_text()), (since, unit.read_text())
        proc.terminate()
        proc.communicate(timeout=10)
        (run / "status.json").unlink()
        slot = state / "slots/lena"
        slot.mkdir()
        (slot / ".spec").write_text(f"{'0' * 64} draft=0 mmproj=2\n")
        httpd.health = 503
        proc = subprocess.Popen([str(BIN), "--config", str(tmp / "models.json")], env=env, stderr=subprocess.PIPE, text=True)
        assert wait("lena", "loading")["restart_pending"]
        (tmp / "restarts.mag-model@lena.service").touch()
        time.sleep(1)
        assert int(unit.read_text()) == since and not (state / "lena.mmproj.off").exists()
        httpd.health = 200
        assert wait("lena", "ready")["since"] == since, status("lena")
        lease = state / "control" / "lena.background"
        lease.touch()
        weight = "set-property --runtime mag-model@lena.service CPUWeight="
        end = time.time() + 5
        while weight + "10\n" not in calls() and time.time() < end:
            time.sleep(0.1)
        assert weight + "10\n" in calls(), calls()
        os.utime(lease, (time.time() - 20, time.time() - 20))
        end = time.time() + 5
        while not calls().rpartition(weight + "10\n")[2].count(weight + "100\n") and time.time() < end:
            time.sleep(0.1)
        assert weight + "100\n" in calls().rpartition(weight + "10\n")[2], calls()
        assert "thaw " not in calls(), "stop and start must leave an unfrozen unit alone"
        (tmp / "frozen.mag-model@lena.service").touch()
        subprocess.check_call([str(BIN), "--config", str(tmp / "models.json"), "ctl", "lena", "restart"], env=env)
        end = time.time() + 20
        while status("lena").get("since") == since and time.time() < end:
            time.sleep(0.2)
        assert wait("lena", "ready")["since"] not in (since, None), status("lena")
        assert "thaw mag-model@lena.service\n" in calls()
        assert "thaw --no-block" not in calls()
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
        assert (qwen_slots / ".spec").read_text().endswith("draft=0 mmproj=3 runtime=upstream\n")
        assert status("qwen")["mmproj"]["total"] == len(mmproj)
        gpu_state.write_text("running 1234\n")
        ready = wait("qwen", "ready")
        assert ready["since"] == 1234 and ready["mmproj"]["active"]
        (state / "qwen.mmproj.off").touch()
        assert "--mmproj" not in launch_args()
        assert (qwen_slots / ".spec").read_text().endswith("draft=0 mmproj=0 runtime=upstream\n")
        gpu_state.write_text("waiting\n")
        assert "since" not in wait("qwen", "waiting_gpu")
        gpu_state.write_text("running 5678\n")
        assert wait("qwen", "ready")["since"] == 5678
        assert path.read_bytes() == good

        # The Noah cutover requires both pinned local files. Reloading modeld
        # must adopt the already running Blake and Qwen processes.
        proc.terminate()
        proc.communicate(timeout=10)
        noah_model = models / "DaisyRose-27B-Q6_K.gguf"
        noah_projector = models / "mmproj-DaisyRose-27B-BF16.gguf"
        noah_model.write_bytes(good)
        conf["bots"]["noah"].update(
            model={"path": str(noah_model), "local": True, "sha256": hashlib.sha256(good).hexdigest()},
            mmproj={"path": str(noah_projector), "local": True, "required": True,
                    "sha256": hashlib.sha256(mmproj).hexdigest()},
        )
        file.write_text(json.dumps(conf))
        lena_since = status("lena")["since"]
        proc = subprocess.Popen(command, env=env, stderr=subprocess.PIPE, text=True)
        end = time.time() + 20
        while not status("noah").get("model", {}).get("ok") and time.time() < end:
            time.sleep(0.2)
        assert status("noah")["model"]["ok"] and status("noah")["state"] == "missing"
        assert "start --no-block mag-model@noah.service" not in calls()
        noah_projector.write_bytes(mmproj)
        assert wait("noah", "loading")["mmproj"]["ok"]
        assert "start --no-block mag-model@noah.service" in calls()
        assert status("lena")["since"] == lena_since
        assert status("qwen")["since"] == 5678
        print("ok")
    finally:
        proc.terminate()
        err = proc.communicate(timeout=10)[1]
        if sys.exc_info()[0]:
            sys.stderr.write(err)


if __name__ == "__main__":
    test_unit_refresh()
    test_hip_build()
    test_mi50_mtp()
    main()
