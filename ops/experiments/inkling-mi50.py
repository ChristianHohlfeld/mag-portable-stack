#!/usr/bin/env python3
"""Opt-in, single-MI50 Inkling 2 Mini GPU benchmark. Never changes MAG services."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

REPO = "unsloth/Inkling-2-Mini-GGUF"
WORK = Path.home() / ".local" / "inkling2-mi50-test"
SOURCE = WORK / "llama.cpp"
BUILD = WORK / "build"
MODELS = Path.home() / "models" / "inkling2-mi50-test"
ROCM = Path(os.environ.get("MAG_INKLING_ROCM", str(Path.home() / "idol-mi50/rocm-6.3.4/current")))
LOCK = Path("/var/lib/mag-idol/gpu.lock")
JOBS = Path("/var/lib/mag-idol/jobs")
# Sizes in bytes approximate: Q3_K_XL 13.63 GB, Q4_K_M 16.61 GB.
QUANTS = {"UD-Q3_K_XL": 13_630_000_000, "UD-Q4_K_M": 16_610_000_000}
VRAM_RESERVE = 2 * 1024**3
MAX_TEMP_MC = 80_000
START_TEMP_MC = 75_000
POWER_CAP_UW = 78_000_000


def fail(message):
    raise RuntimeError(message)


def run_command(argv, **kwargs):
    print("+", " ".join(map(str, argv)), flush=True)
    subprocess.run([str(x) for x in argv], check=True, **kwargs)


def number(path):
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError, PermissionError):
        return None


def mi50():
    candidates = []
    for dev in Path("/sys/bus/pci/devices").glob("*"):
        try:
            if dev.joinpath("vendor").read_text().strip() == "0x1002" and dev.joinpath("device").read_text().strip() == "0x66a1":
                candidates.append(dev)
        except (OSError, ValueError):
            pass
    if len(candidates) != 1:
        fail(f"Erwartet genau eine MI50 (1002:66a1), gefunden: {len(candidates)}")
    dev = candidates[0]
    total = number(dev / "mem_info_vram_total")
    used = number(dev / "mem_info_vram_used")
    if not total or used is None or used < 0:
        fail("MI50 VRAM-Anzeige über sysfs nicht lesbar; kein Schätzwert")
    return dev, total, used


def sensors(dev):
    temperatures = []
    caps = []
    for hwmon in dev.glob("hwmon/hwmon*"):
        for item in hwmon.glob("temp*_input"):
            value = number(item)
            if value is not None and -50_000 < value < 200_000:
                temperatures.append((item, value))
        for item in hwmon.glob("power1_cap"):
            value = number(item)
            if value is not None:
                caps.append(value)
    return temperatures, caps


def quant_for(total):
    if total >= 24 * 1024**3:
        return "UD-Q4_K_M"
    if total >= 16 * 1024**3:
        return "UD-Q3_K_XL"
    fail(f"Nur {total/1024**3:.1f} GiB VRAM. Kein sicher passender getesteter Quant.")


def active_jobs():
    if not JOBS.exists():
        fail(f"Idol-Jobs-Verzeichnis fehlt: {JOBS}")
    active = []
    for path in JOBS.glob("*/status.json"):
        try:
            state = json.loads(path.read_text()).get("state")
            if state in {"running", "queued"}:
                active.append(str(path.parent.name))
        except (OSError, ValueError):
            fail(f"Jobstatus nicht lesbar: {path}")
    return active


def inspect(strict=False):
    dev, total, used = mi50()
    temps, caps = sensors(dev)
    quant = quant_for(total)
    print(f"MI50 {dev.name}: VRAM {total/1024**3:.2f} GiB, belegt {used/1024**3:.2f} GiB, Quant {quant}")
    print("Temperaturen:", ", ".join(f"{p.name}={v/1000:.1f} C" for p, v in temps) or "nicht lesbar")
    print("Power-Cap:", ", ".join(f"{v/1e6:.1f} W" for v in caps) or "nicht lesbar")
    jobs = active_jobs()
    print("Idol-Jobs running/queued:", ", ".join(jobs) if jobs else "keine")
    required = QUANTS[quant] + VRAM_RESERVE
    free = total - used
    print(f"VRAM frei {free/1024**3:.2f} GiB, Mindestbedarf (Gewichte + Reserve) {required/1024**3:.2f} GiB")
    if strict:
        if not temps or not caps:
            fail("Temperatur oder Power-Cap nicht lesbar: GPU-Test verboten")
        if max(v for _, v in temps) >= START_TEMP_MC:
            fail("MI50 vor dem Test schon zu warm (>=75 C)")
        if any(v > POWER_CAP_UW for v in caps):
            fail("78-W-Grenze nicht aktiv; nicht automatisch ändern")
        if jobs:
            fail("Idol hat offene Jobs; Benchmark startet nicht")
        if free < required:
            fail("Zu wenig freier VRAM für Voll-GPU; kein CPU-Offload als Ersatz")
    return dev, quant


def prepare():
    if shutil.which("cmake") is None or shutil.which("ninja") is None:
        fail("cmake und ninja fehlen")
    hipcc = ROCM / "bin" / "hipcc"
    if not hipcc.is_file():
        fail(f"HIP-Compiler fehlt: {hipcc} (ROCm 6.3.4 aus ops/video/h3/rocm.sh)")
    WORK.mkdir(parents=True, exist_ok=True)
    if not (SOURCE / ".git").is_dir():
        if SOURCE.exists():
            fail(f"{SOURCE} existiert bereits und ist kein Git-Checkout")
        run_command(["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp.git", SOURCE])
    else:
        dirty = subprocess.check_output(["git", "-C", str(SOURCE), "status", "--porcelain"], text=True)
        if dirty.strip():
            fail("Experimenteller llama.cpp Checkout wurde verändert; nicht überschreiben")
        run_command(["git", "-C", SOURCE, "fetch", "--depth", "1", "origin", "master"])
        run_command(["git", "-C", SOURCE, "checkout", "--detach", "FETCH_HEAD"])
    revision = subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip()
    arch = SOURCE / "src" / "llama-arch.cpp"
    if "INKLING2_LOOP" not in arch.read_text():
        fail("llama.cpp-Revision unterstützt inkling2_loop nicht")
    print("Experimentelles llama.cpp SHA:", revision)
    env = os.environ.copy()
    env["PATH"] = f"{ROCM / 'bin'}:{env.get('PATH', '')}"
    env["LD_LIBRARY_PATH"] = f"{ROCM / 'lib'}:{ROCM / 'lib64'}:{env.get('LD_LIBRARY_PATH', '')}"
    run_command(["taskset", "-c", "28-55", "cmake", "-S", SOURCE, "-B", BUILD, "-G", "Ninja",
                 "-DCMAKE_BUILD_TYPE=Release", "-DGGML_HIP=ON",
                 "-DAMDGPU_TARGETS=gfx906", "-DGGML_HIP_NO_VMM=ON", f"-DCMAKE_PREFIX_PATH={ROCM}"], env=env)
    run_command(["taskset", "-c", "28-55", "cmake", "--build", BUILD, "--parallel", "8",
                 "--target", "llama-cli", "llama-bench"], env=env)
    (WORK / "revision.txt").write_text(revision + "\n")
    print("Build fertig: Produktions-runtime und mag-modeld unverändert.")


def download():
    _, quant = inspect()
    if not shutil.which("hf"):
        fail("Hugging Face CLI 'hf' fehlt (pip install -U huggingface_hub)")
    name = f"Inkling-2-Mini-{quant}"
    target = MODELS / f"{name}.gguf"
    if target.is_file() and target.stat().st_size > int(QUANTS[quant] * 0.95):
        print("Bereits vorhanden:", target)
        return
    if shutil.disk_usage(Path.home()).free < 2 * QUANTS[quant] + 5 * 1024**3:
        fail("Zu wenig freier Platz für Download plus Cache. Nichts löschen.")
    MODELS.mkdir(parents=True, exist_ok=True)
    remote = f"Mainline_Support/{name}.gguf_file"
    run_command(["hf", "download", REPO, "--include", remote,
                 "--local-dir", MODELS])
    source = MODELS / remote
    if not source.is_file() or source.stat().st_size < int(QUANTS[quant] * 0.95):
        fail("Download unvollständig; keine Modell-Ausführung")
    source.rename(target)
    print("Fertig:", target)


def terminate_group(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def guarded_command(argv, dev, outfile, env, limit=180):
    start = time.monotonic()
    print(f"Benchmark: {outfile}")
    with outfile.open("w") as log:
        proc = subprocess.Popen([str(a) for a in argv], stdout=log,
                                stderr=subprocess.STDOUT, env=env,
                                start_new_session=True)
        reason = None
        try:
            while proc.poll() is None:
                temps, caps = sensors(dev)
                if not temps or not caps:
                    reason = "Sensoren nicht mehr lesbar"
                elif max(v for _, v in temps) >= MAX_TEMP_MC:
                    reason = "Temperaturgrenze 80 C"
                elif any(v > POWER_CAP_UW for v in caps):
                    reason = "Power-Cap über 78 W"
                elif time.monotonic() - start > limit:
                    reason = f"Zeitgrenze {limit} s"
                if reason:
                    terminate_group(proc)
                    break
                time.sleep(1)
        finally:
            terminate_group(proc)
    if reason:
        fail(f"GPU-Benchmark abgebrochen: {reason}; Details: {outfile}")
    if proc.returncode:
        fail(f"llama.cpp Exitcode {proc.returncode}; Details: {outfile}")
    print(f"Fertig in {time.monotonic()-start:.1f}s: {outfile}")


def benchmark():
    dev, total, _ = mi50()
    quant = quant_for(total)
    model = MODELS / f"Inkling-2-Mini-{quant}.gguf"
    cli = BUILD / "bin" / "llama-cli"
    bench = BUILD / "bin" / "llama-bench"
    if not model.is_file() or not cli.is_file() or not bench.is_file():
        fail("Zuerst 'prepare' und 'download' ausführen")
    if not LOCK.is_file():
        fail(f"Produktions-GPU-Lock fehlt: {LOCK}")
    WORK.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PATH"] = f"{ROCM / 'bin'}:{env.get('PATH', '')}"
    env["LD_LIBRARY_PATH"] = f"{ROCM / 'lib'}:{ROCM / 'lib64'}:{env.get('LD_LIBRARY_PATH', '')}"
    env["HIP_VISIBLE_DEVICES"] = "0"
    # Non-blocking: if Idol owns the MI50, do not queue behind its job.
    with LOCK.open("r+") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("MI50 ist belegt (Idol GPU-Lock); Benchmark nicht gestartet")
        inspect(strict=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        sample_log = WORK / f"inkling-sample-{timestamp}.log"
        guarded_command(["taskset", "-c", "28-55", cli, "-m", model, "-ngl", "99", "-c", "4096",
                         "-n", "128", "--temp", "1.0", "--top-p", "1.0",
                         "--top-k", "0", "--min-p", "0.0",
                         "-p", "Write a C++17 function that detects whether a singly linked list has a cycle. Explain time and space complexity."],
                        dev, sample_log, env)
        report = sample_log.read_text(errors="replace")
        matches = re.findall(r"offloaded\s+(\d+)/(\d+)\s+layers?\s+to GPU", report, re.I)
        if not matches or not any(int(a) == int(b) and int(a) > 0 for a, b in matches):
            fail(f"Vollständiges GPU-Offloading nicht bestätigt. Kein CPU-Fallback akzeptiert: {sample_log}")
        perf_log = WORK / f"inkling-perf-{timestamp}.json"
        guarded_command(["taskset", "-c", "28-55", bench, "-m", model, "-ngl", "99", "-p", "128",
                         "-n", "128", "-r", "1", "-o", "json"],
                        dev, perf_log, env)
        print("Ausgabe und GPU-Geschwindigkeit:", sample_log, perf_log)
        print("Nur ein isolierter Test; Lena/Noah und MAG Idol bleiben unverändert.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("preflight", "prepare", "download", "run"))
    args = ap.parse_args()
    if args.command == "preflight":
        inspect()
    elif args.command == "prepare":
        prepare()
    elif args.command == "download":
        download()
    else:
        benchmark()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print("FEHLER:", error, file=sys.stderr)
        sys.exit(1)
