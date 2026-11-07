"""Fast, entirely offline safety and data-contract checks for MAG coding candidates."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import ast
import json
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))


class CodingStage(unittest.TestCase):
    def test_candidate_identifiers_and_cpu_layout(self):
        self.assertEqual(DATA["context_tokens"], 262144)
        self.assertEqual(DATA["coding_output_tokens"], 16384)
        self.assertEqual(set(DATA["models"]), {"general", "coder"})
        self.assertEqual([DATA["models"][name]["numa"] for name in ("general", "coder")], [0, 1])
        self.assertEqual([DATA["models"][name]["candidate_port"] for name in ("general", "coder")], [8291, 8292])
        for entry in DATA["models"].values():
            self.assertTrue(re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]))
            self.assertTrue(entry["filename"].endswith(".gguf"))
            self.assertTrue(entry["repo"].count("/") == 1)
            self.assertNotIn("8191", str(entry["candidate_port"]))
            self.assertNotIn("8192", str(entry["candidate_port"]))

    def test_scripts_parse_and_never_stop_or_delete_live_units(self):
        for name in ("stage.py", "smoke.py"):
            ast.parse((ROOT / name).read_text(encoding="utf-8"))
        runner = (ROOT / "run-candidate.sh").read_text(encoding="utf-8")
        p = subprocess.run(["bash", "-n", str(ROOT / "run-candidate.sh")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        for dangerous in ("systemctl stop", "systemctl restart", "systemctl disable",
                          "drop_caches", "rm -rf", "killall"):
            self.assertNotIn(dangerous, runner)
        self.assertIn("systemctl is-active", runner)
        self.assertIn("MAG_CODING_LLAMA_BIN", runner)
        self.assertIn("numactl --physcpubind", runner)

    def test_source_hash_pins_and_long_output_are_explicit(self):
        self.assertEqual(
            DATA["models"]["general"]["sha256"],
            "dec1cee704800267d9d836d5a61aefc33705be939bbb3058fa9006d98191576d")
        self.assertEqual(
            [part["sha256"] for part in DATA["models"]["general"]["parts"]],
            ["3064d3c4c1d6363e9f9ad88e90a3e2c5fb2d6f7ae16ca72135c3ce6a5c984da5",
             "2e9b2732eca7da8324f731653624a4f5c9846258926fd9f468cc703afb51a019",
             "4ca79d8e5107dd1b9bb57b176a7c09948837425dee49f0f1dfd6547a3769fea7"])
        self.assertEqual(
            DATA["models"]["coder"]["sha256"],
            "12d8d5c01bae7f23ea4822b2f96ba069d531f827d02a57c31002f2f95e72614a")
        self.assertIn("default=16384", (ROOT / "smoke.py").read_text())
        self.assertIn("finish == \"length\"", (ROOT / "smoke.py").read_text())


if __name__ == "__main__":
    unittest.main()
