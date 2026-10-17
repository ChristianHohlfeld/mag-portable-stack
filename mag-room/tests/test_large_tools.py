"""Offline regression: the exact embedded guest commit helper, >100 KiB, append and failure atomicity."""
import os
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "mag-room/native/vm.cpp").read_text()
MATCH = re.search(r'ATOMIC_FILE_PY = R"MAGPY\((.*?)\)MAGPY"', SOURCE, re.S)
assert MATCH, "guest atomic implementation missing"
HELPER = MATCH.group(1)

class LargeFileTools(unittest.TestCase):
    def test_atomic_write_append_over_100k(self):
        with tempfile.TemporaryDirectory(prefix="mag-tool-") as home:
            home = pathlib.Path(home)
            env = dict(os.environ, HOME=str(home))
            stage = home / "payload"
            target = home / "projects" / "file.txt"
            data = "Große Datei äöü\n".encode() * 10000
            self.assertGreater(len(data), 100 * 1024)
            stage.write_bytes(data)
            def call(mode, path=target):
                return subprocess.run(["python3", "-c", HELPER, str(stage), str(path), mode],
                                      env=env, capture_output=True, text=True)
            wrote = call("--write")
            self.assertEqual(wrote.returncode, 0, wrote.stderr)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(call("--append").returncode, 0)
            self.assertEqual(target.read_bytes(), data + data)
            stage.write_bytes(b"replacement")
            self.assertEqual(call("--write").returncode, 0)
            self.assertEqual(target.read_bytes(), b"replacement")
            stage.unlink()
            failed = call("--append")
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(target.read_bytes(), b"replacement",
                             "failed append must preserve original exactly")
            outside = home.parent / ("mag-outside-" + home.name)
            blocked = call("--write", outside)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertFalse(outside.exists(), "guest file tool must not escape bot home")

if __name__ == "__main__":
    unittest.main()
