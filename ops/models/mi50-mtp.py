#!/usr/bin/env python3
"""Probe GGUF tensor names and try the optional embedded MTP server."""
import http.client
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys

# GGUF metadata types, indexed by their on-disk type number.
SCALAR_BYTES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1,
                10: 8, 11: 8, 12: 8}


def has_mtp(model):
    # Read only the metadata and tensor directory. Tensor names in the
    # tokenizer metadata or in the weight data do not advertise MTP.
    with open(model, "rb") as file:
        size = os.fstat(file.fileno()).st_size

        def number(fmt):
            return struct.unpack("<" + fmt, file.read(struct.calcsize("<" + fmt)))[0]

        def skip(bytes_):
            if file.tell() + bytes_ > size:
                raise ValueError("truncated GGUF")
            file.seek(bytes_, os.SEEK_CUR)

        def skip_value(kind, count=1):
            if kind == 8:
                for _ in range(count):
                    skip(number("Q"))
            else:
                skip(SCALAR_BYTES[kind] * count)

        if file.read(4) != b"GGUF" or number("I") not in (2, 3):
            return False
        tensors, metadata = number("Q"), number("Q")
        for _ in range(metadata):
            skip(number("Q"))
            kind = number("I")
            if kind == 9:
                item_kind = number("I")
                skip_value(item_kind, number("Q"))
            else:
                skip_value(kind)
        for _ in range(tensors):
            length = number("Q")
            # GGUF tensor names fit in GGML_MAX_NAME.
            if not 0 < length <= 64:
                return False
            name = file.read(length)
            dims = number("I")
            if not 1 <= dims <= 4:
                return False
            skip(8 * dims + 4 + 8)  # dimensions, tensor type and data offset
            if re.search(rb"(?:^|\.)(?:nextn|mtp)\.", name):
                return True
    return False


def ready(port):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
    try:
        conn.request("GET", "/health")
        return conn.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def serve(port, spec, draft_count, command):
    normal = command[:-draft_count]
    # Idol can preempt and relaunch the command under the same guard. Keep
    # the fallback until a fresh invocation of run-mi50.sh resets the spec.
    active_spec = spec.read_text()
    if " draft=0 " in active_spec:
        os.execv(normal[0], normal)
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        child = subprocess.Popen(command)
    except OSError as error:
        print(f"MTP launch failed: {error}", file=sys.stderr)
    else:
        was_ready = False
        while not stopped:
            try:
                code = child.wait(timeout=0.2)
                if was_ready:
                    return code if code > 0 else 1
                break
            except subprocess.TimeoutExpired:
                was_ready = was_ready or ready(port)
        if stopped and child.poll() is None:
            # Also cover a child spawned concurrently with the group signal.
            child.terminate()
            child.wait()
    if stopped:
        return 0
    print("MTP startup failed, retrying llama-server without MTP", file=sys.stderr)
    pending = spec.with_suffix(".spec.next")
    pending.write_text(active_spec.replace(f" draft={draft_count} ", " draft=0 ", 1))
    pending.replace(spec)
    os.execv(normal[0], normal)


if __name__ == "__main__":
    if sys.argv[1] == "probe":
        try:
            sys.exit(0 if has_mtp(sys.argv[2]) else 1)
        except (OSError, ValueError, KeyError, struct.error):
            sys.exit(1)
    port, spec, draft_count = sys.argv[1:4]
    sys.exit(serve(int(port), Path(spec), int(draft_count), sys.argv[4:]))
