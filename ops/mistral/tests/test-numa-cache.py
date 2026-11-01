#!/usr/bin/env python3
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("drop_shard_cache", ROOT / "drop-shard-cache.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DropShardCacheTests(unittest.TestCase):
    def make_model(self, root):
        names = [
            f"Mistral-Large-4-1T-A52B-UD-IQ1_S-0000{i}-of-00006.gguf"
            for i in range(1, 7)
        ]
        (root / "SHA256SUMS").write_text(
            "".join(f"{'a' * 64}  {name}\n" for name in names), encoding="utf-8"
        )
        for name in names:
            (root / name).write_bytes(b"mistral shard")
        (root / "unrelated-glm-q2.gguf").write_bytes(b"must not be opened")
        return names

    def test_advises_exactly_six_shards_and_no_unrelated_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = self.make_model(root)
            advised = []

            def record(fd, offset, length, advice):
                advised.append((os.readlink(f"/proc/self/fd/{fd}"), offset, length, advice))

            with patch.object(MODULE.os, "posix_fadvise", side_effect=record):
                result = MODULE.evict_shard_cache(root)

            self.assertEqual(result, names)
            self.assertEqual(len(advised), 6)
            self.assertEqual({Path(item[0]).name for item in advised}, set(names))
            self.assertTrue(all(item[1:] == (0, 0, os.POSIX_FADV_DONTNEED) for item in advised))

    def test_rejects_incomplete_or_unexpected_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_model(root)
            (root / "SHA256SUMS").write_text("a" * 64 + "  unrelated.gguf\n")
            with self.assertRaises(ValueError):
                MODULE.shard_names(root / "SHA256SUMS")

    def test_rejects_symlinked_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = self.make_model(root)
            target = root / names[0]
            target.unlink()
            target.symlink_to(root / "unrelated-glm-q2.gguf")
            with self.assertRaises(ValueError):
                MODULE.evict_shard_cache(root)

    def test_service_orders_node0_verification_then_targeted_eviction(self):
        unit = (ROOT / "mag-llm-mistral-room.service").read_text()
        self.assertIn("ExecStartPre=+/usr/bin/numactl --membind=0 /usr/local/lib/mag-mistral/verify-model.sh", unit)
        self.assertIn("ExecStartPre=+/usr/bin/python3 -I /usr/local/lib/mag-mistral/drop-shard-cache.py", unit)
        self.assertLess(unit.index("verify-model.sh"), unit.index("drop-shard-cache.py"))
        self.assertIn("ExecStartPost=+/usr/local/lib/mag-mistral/check-numa-placement.sh", unit)

    def test_apply_restarts_only_mistral_and_guards_glm_q2_pid(self):
        apply = (ROOT / "apply.sh").read_text()
        self.assertIn("systemctl restart mag-llm-mistral-room.service", apply)
        self.assertNotIn("systemctl restart mag-llm-glm@q2.service", apply)
        self.assertIn("GLM-Q2 PID changed", apply)
        self.assertIn("numastat", (ROOT / "check-numa-placement.sh").read_text())


if __name__ == "__main__":
    unittest.main()
