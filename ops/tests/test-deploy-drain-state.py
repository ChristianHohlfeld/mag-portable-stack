#!/usr/bin/env python3
"""Hard-timeout checks for deploy drain classification."""
import pathlib as _mag_path
import sys as _mag_sys

_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / "ops/ci/mag_deadline.py").is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / "ops/ci"))
import mag_deadline as _mag_deadline

_mag_deadline.arm(180)

import importlib.util
import json
import tempfile
import unittest

ROOT = _mag_path.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "deploy_drain_state", ROOT / "ops/deploy-drain-state.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class DrainState(unittest.TestCase):
    def test_new_room_fields(self):
        u, p, s, h, _ = MOD.classify(
            {
                "user_busy": True,
                "selftalk_busy": True,
                "selftalk_held": True,
                "pending": [{"text": "hi"}],
                "queue_paused": False,
            }
        )
        self.assertEqual((u, p, s, h), (1, 1, 1, 1))

    def test_old_room_selftalk_only(self):
        u, p, s, h, _ = MOD.classify(
            {"busy": True, "selftalk": True, "pending": [], "queue": 2}
        )
        self.assertEqual((u, p, s, h), (0, 0, 1, 0))

    def test_old_room_user_busy(self):
        u, p, s, h, _ = MOD.classify(
            {"busy": True, "selftalk": False, "pending": [], "queue": 0}
        )
        self.assertEqual((u, p, s, h), (1, 0, 0, 0))

    def test_queue_json_distinguishes_user_and_selftalk(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = _mag_path.Path(tmp) / "queue.json"
            q.write_text(
                json.dumps(
                    {
                        "pending": [],
                        "jobs": [{"ids": ["b"], "selftalk": True}],
                        "actives": [{"ids": ["a"], "selftalk": False}],
                    }
                )
            )
            u, p, s, h, _ = MOD.classify(
                {"busy": True, "selftalk": True, "pending": []}, tmp
            )
            self.assertEqual((u, p, s, h), (1, 0, 1, 0))

    def test_held_selftalk_without_user_work(self):
        u, p, s, h, _ = MOD.classify(
            {
                "busy": True,
                "user_busy": False,
                "selftalk_busy": True,
                "selftalk_held": True,
                "pending": [],
            }
        )
        self.assertEqual((u, p, s, h), (0, 0, 1, 1))


if __name__ == "__main__":
    unittest.main()
