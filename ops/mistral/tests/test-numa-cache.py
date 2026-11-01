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
SPEC = importlib.util.spec_from_file_location("prefetch_numa0", ROOT / "prefetch-numa0.py")
PREFETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFETCH)
CACHE_SPEC = importlib.util.spec_from_file_location("drop_shard_cache", ROOT / "drop-shard-cache.py")
CACHE = importlib.util.module_from_spec(CACHE_SPEC)
CACHE_SPEC.loader.exec_module(CACHE)


class NumaPrefetchTests(unittest.TestCase):
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
        return names

    def test_prefetch_reads_only_six_shards_sequentially_on_numa0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = self.make_model(root)
            qwen = root / "Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf"
            qwen.write_bytes(b"qwen")
            glm = root / "GLM-5.3-Flash-IQ2_XS.gguf"
            glm.write_bytes(b"must never be touched")
            calls = []

            def runner(args, check):
                calls.append(args)
                self.assertTrue(check)

            with patch.object(PREFETCH, "evict_inactive_qwen_cache", return_value=False):
                result = PREFETCH.prefetch(root, qwen, qwen_running=True, runner=runner)

            self.assertEqual(result, names)
            self.assertEqual(len(calls), 6)
            for call, name in zip(calls, names):
                self.assertEqual(
                    call,
                    ["/usr/bin/numactl", "--membind=0", "/usr/bin/dd",
                     f"if={root / name}", "of=/dev/null", "bs=16M", "status=none"],
                )
            self.assertTrue(glm.exists())

    def test_inactive_qwen_cache_eviction_targets_only_exact_qwen_gguf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qwen = root / "Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf"
            qwen.write_bytes(b"qwen")
            glm = root / "GLM-5.3-Flash-IQ2_XS.gguf"
            glm.write_bytes(b"glm")
            touched = []

            def record(fd, offset, length, advice):
                touched.append((Path(os.readlink(f"/proc/self/fd/{fd}")).name,
                                offset, length, advice))

            with patch.object(PREFETCH.os, "posix_fadvise", side_effect=record):
                self.assertTrue(PREFETCH.evict_inactive_qwen_cache(qwen, running=False))

            self.assertEqual(len(touched), 1)
            self.assertEqual(touched[0], (qwen.name, 0, 0, os.POSIX_FADV_DONTNEED))
            self.assertTrue(qwen.exists())
            self.assertTrue(glm.exists())

    def test_running_qwen_is_never_evicted(self):
        with tempfile.TemporaryDirectory() as tmp:
            qwen = Path(tmp) / "Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf"
            qwen.write_bytes(b"qwen")
            with patch.object(PREFETCH.os, "posix_fadvise") as advise:
                self.assertFalse(PREFETCH.evict_inactive_qwen_cache(qwen, running=True))
            advise.assert_not_called()

    def test_manifest_rejects_wrong_or_incomplete_shard_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_model(root)
            (root / "SHA256SUMS").write_text("a" * 64 + "  GLM-5.3-Flash-IQ2_XS.gguf\n")
            with self.assertRaises(ValueError):
                PREFETCH.shard_names(root / "SHA256SUMS")


class MistralRoomContractTests(unittest.TestCase):
    def test_unit_orders_cache_eviction_then_prefetch_before_server(self):
        unit = (ROOT / "mag-llm-mistral-room.service").read_text()
        self.assertLess(unit.index("drop-shard-cache.py"), unit.index("prefetch-numa0.py"))
        self.assertLess(unit.index("prefetch-numa0.py"), unit.index("ExecStart="))
        self.assertIn("ExecStartPre=+/usr/bin/python3 -I /usr/local/lib/mag-mistral/prefetch-numa0.py", unit)
        install = (ROOT / "install.sh").read_text()
        self.assertIn("prefetch-numa0.py", install)

    def test_mistral_short_chat_keeps_official_low_effort_and_caps_total_tokens(self):
        room = (ROOT.parents[1] / "mag-room/native/magroom.cpp").read_text()
        self.assertIn('(!thinking_turn || bot_model_id(bot_idx) == "mistral-large4")', room)
        self.assertIn("if (mistral_chat && short_chat)", room)
        self.assertIn("max_output = std::min(max_output, 128)", room)
        self.assertIn('body["reasoning_effort"] = "low"', room)
        self.assertIn('kwargs["reasoning_effort"] = "low"', room)
        self.assertIn('if (!mistral_chat)\n    kwargs["enable_thinking"] = thinking && !TL_SELFTALK && !short_chat;', room)
        self.assertIn("reasoning cannot be disabled", room)
        start = room.index("} else if (mistral_chat)")
        end = room.index("// The Qwen4Exp", start)
        self.assertNotIn('body["reasoning_effort"] = "none"', room[start:end])

    def test_cold_start_smoke_and_deploy_deadline_cover_long_load(self):
        smoke = (ROOT / "smoke.py").read_text()
        layout = (ROOT.parents[1] / "ops/qwen/layout.sh").read_text()
        deploy = (ROOT.parents[1] / "ops/deploy.sh").read_text()
        self.assertIn("default=7200", smoke)
        self.assertIn("--timeout 7200", layout)
        self.assertIn("_deploy_default_deadline=36000", deploy)



class ExistingCacheSafetyTests(unittest.TestCase):
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

    def test_drop_cache_advises_exactly_six_mistral_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = self.make_model(root)
            advised = []

            def record(fd, offset, length, advice):
                advised.append((Path(os.readlink(f"/proc/self/fd/{fd}")).name,
                                offset, length, advice))

            with patch.object(CACHE.os, "posix_fadvise", side_effect=record):
                result = CACHE.evict_shard_cache(root)

            self.assertEqual(result, names)
            self.assertEqual({x[0] for x in advised}, set(names))
            self.assertEqual(len(advised), 6)
            self.assertTrue(all(x[1:] == (0, 0, os.POSIX_FADV_DONTNEED) for x in advised))

    def test_drop_cache_rejects_incomplete_manifest_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = self.make_model(root)
            (root / "SHA256SUMS").write_text("a" * 64 + "  unrelated.gguf\n")
            with self.assertRaises(ValueError):
                CACHE.shard_names(root / "SHA256SUMS")
            self.make_model(root)
            (root / names[0]).unlink()
            (root / names[0]).symlink_to(root / "unrelated-glm-q2.gguf")
            with self.assertRaises(ValueError):
                CACHE.evict_shard_cache(root)

    def test_guarded_apply_restarts_only_mistral_and_checks_q2_identity(self):
        apply = (ROOT / "apply.sh").read_text()
        self.assertIn("systemctl restart mag-llm-mistral-room.service", apply)
        self.assertNotIn("systemctl restart mag-llm-glm@q2.service", apply)
        self.assertIn("GLM-Q2 PID changed", apply)
        self.assertIn("http://127.0.0.1:8192/health", apply)


if __name__ == "__main__":
    unittest.main()
