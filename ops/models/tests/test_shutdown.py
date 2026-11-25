#!/usr/bin/env python3
"""Send SIGTERM during a real llama-server prefill using a small dummy GGUF."""
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from test_modeld import free_port


def test_shutdown(binary, model):
    port = free_port()
    with tempfile.TemporaryFile() as log:
        server = subprocess.Popen(
            [binary, "--model", model, "--host", "127.0.0.1", "--port", str(port),
             "--ctx-size", "16384", "--parallel", "1", "--threads", "1",
             "--override-kv", "llama.context_length=int:16384",
             "--batch-size", "128", "--ubatch-size", "128", "--no-warmup", "--no-ui"],
            stdout=log, stderr=subprocess.STDOUT,
        )
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and server.poll() is None:
                try:
                    conn.request("GET", "/health")
                    with conn.getresponse() as response:
                        response.read()
                        if response.status == 200:
                            break
                except OSError:
                    conn.close()
                time.sleep(0.1)
            else:
                raise AssertionError("llama-server did not become ready")

            body = json.dumps(dict(prompt=[11] * 14000, n_predict=1, stream=True, return_progress=True))
            conn.request("POST", "/completion", body, {"Content-Type": "application/json"})
            with conn.getresponse() as response:
                assert response.status == 200, response.read()
                for line in response:
                    if not line.startswith(b"data: {"):
                        continue
                    progress = json.loads(line[6:]).get("prompt_progress")
                    if progress and 0 < progress["processed"] < progress["total"]:
                        server.terminate()
                        assert server.wait(timeout=10) == 0
                        break
                else:
                    raise AssertionError("llama-server did not report an in-flight prefill")
            log.seek(0)
            output = log.read()
            assert b"error: Server shutting down" in output, "prefill finished before shutdown"
            assert b"stop processing:" in output, "active slot was not released"
        except Exception:
            log.seek(0)
            print(log.read().decode(errors="replace"), file=sys.stderr)
            raise
        finally:
            conn.close()
            if server.poll() is None:
                server.kill()
                server.wait()


if __name__ == "__main__":
    test_shutdown(*(str(Path(path).resolve()) for path in sys.argv[1:]))
