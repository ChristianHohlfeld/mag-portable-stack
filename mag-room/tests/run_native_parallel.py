#!/usr/bin/env python3
"""Run isolated native RoomContract tests in parallel without skipping cases.

Each test already creates private state directories, fake model servers, and
ports. Keep unittest's per-test diagnostics; only shard execution changes.
"""
import os
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_native import RoomContract  # noqa: E402


def run_shard(number, names):
    command = [sys.executable, "-u", str(HERE / "test_native.py")]
    command += ["RoomContract." + name for name in names]
    print(f"[native {number}] START {len(names)} tests", flush=True)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1, env=os.environ.copy())
    try:
        for line in process.stdout:
            print(f"[native {number}] {line}", end="", flush=True)
    finally:
        process.stdout.close()
    code = process.wait()
    print(f"[native {number}] {'PASS' if code == 0 else 'FAIL'} (exit {code})", flush=True)
    return code


def main():
    names = sorted(name for name in dir(RoomContract)
                   if name.startswith("test_") and callable(getattr(RoomContract, name)))
    if not names:
        raise RuntimeError("no native tests discovered")
    workers = min(4, len(names))
    batches = [names[i::workers] for i in range(workers)]
    print(f"Native contract: {len(names)} distinct tests in {workers} isolated processes", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = [executor.submit(run_shard, i + 1, batch) for i, batch in enumerate(batches)]
        status = [future.result() for future in as_completed(pending)]
    return 0 if all(code == 0 for code in status) else 1


if __name__ == "__main__":
    sys.exit(main())
