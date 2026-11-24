#!/usr/bin/env python3
"""Run one MI50 model below Idol's priority, releasing the device on demand."""
import fcntl
import http.client
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def try_lock(file, mode):
    try:
        fcntl.flock(file, mode | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def idol_waiting(priority):
    if not try_lock(priority, fcntl.LOCK_EX):
        return True
    fcntl.flock(priority, fcntl.LOCK_UN)
    return False


def unload_h3():
    # h3d caches H3 between video jobs. Only try this while owning gpu.lock.
    # Missing server or endpoint is OK under the GPU lock. Other failures
    # are retried without launching llama-server.
    conn = http.client.HTTPConnection("127.0.0.1", int(os.environ.get("MAG_H3D_PORT", "8796")), timeout=2)
    try:
        conn.request("POST", "/sdcpp/v1/models/unload")
        return conn.getresponse().status in (200, 404)
    except ConnectionRefusedError:
        return True
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def run(slots, command):
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    def status(value):
        tmp.write_text(value + "\n")
        tmp.replace(state)

    def serve(gpu, priority):
        child = subprocess.Popen(command, start_new_session=True, pass_fds=(gpu.fileno(),))
        try:
            status(f"running {time.monotonic_ns() // 1000}")
            while not stopped and not idol_waiting(priority):
                code = child.poll()
                if code is not None:
                    return code if code > 0 else 1
                time.sleep(0.2)
        finally:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=None if stopped else 30)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            status("waiting")
        return 0

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    state = Path(slots) / ".gpu"
    tmp = state.with_suffix(".gpu.tmp")
    lock = os.environ.get("MAG_GPU_LOCK", "/var/lib/mag-idol/gpu.lock")
    status("waiting")
    with open(lock, "rb") as gpu, open(lock + ".idol", "rb") as priority:
        while not stopped:
            if not idol_waiting(priority) and try_lock(gpu, fcntl.LOCK_EX):
                try:
                    if not stopped and not idol_waiting(priority) and unload_h3() and not stopped and not idol_waiting(priority):
                        code = serve(gpu, priority)
                        if code:
                            return code
                finally:
                    fcntl.flock(gpu, fcntl.LOCK_UN)
            time.sleep(0.2)
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2:]))
