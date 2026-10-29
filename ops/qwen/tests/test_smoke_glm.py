"""GLM-5.3 smoke contract: enable_thinking via the pinned template, real diagnostic output."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import contextlib
import importlib.util
import io
import json
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("mag_qwen_smoke", ROOT / "ops/qwen/smoke.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class SmokeGLM(unittest.TestCase):
    def setUp(self):
        SMOKE.request.model = "glm-5.3-flash"
        SMOKE.request.diagnose_only = False

    def fake(self, content="", reasoning="working", finish="length"):
        answer = {"choices": [{"message": {"content": content, "reasoning_content": reasoning},
                                "finish_reason": finish}],
                  "usage": {"completion_tokens": 192}}
        received = []

        def urlopen(req, timeout):
            received.append(json.loads(req.data))
            return io.BytesIO(json.dumps(answer).encode())
        return received, urlopen

    def test_glm_thinking_off_uses_enable_thinking_and_reports_empty_content(self):
        calls, opener = self.fake()
        with mock.patch.object(SMOKE.urllib.request, "urlopen", side_effect=opener):
            with contextlib.redirect_stdout(io.StringIO()) as log:
                msg = SMOKE.request(8192, [{"role": "user", "content": "Reply READY."}])
        # The room's contract; the pinned GLM template honours it.
        self.assertEqual(calls[0]["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(calls[0]["max_tokens"], 192)
        self.assertIn('"finish_reason": "length"', log.getvalue())
        self.assertIn('"reasoning_content": "working"', log.getvalue())
        with self.assertRaisesRegex(AssertionError, "reasoning exhausted max_tokens"):
            SMOKE.require_visible_content(msg, "Normal chat failed")

    def test_glm_thinking_on(self):
        calls, opener = self.fake(content="READY", reasoning="small", finish="stop")
        with mock.patch.object(SMOKE.urllib.request, "urlopen", side_effect=opener):
            with contextlib.redirect_stdout(io.StringIO()):
                msg = SMOKE.request(8192, [{"role": "user", "content": "Reply READY."}], thinking=True)
        self.assertEqual(calls[0]["chat_template_kwargs"], {"enable_thinking": True})
        self.assertEqual(calls[0]["max_tokens"], 512)
        SMOKE.require_visible_content(msg, "Normal chat failed")

    def test_chat_requires_ready_and_stop(self):
        src = (ROOT / "ops/qwen/smoke.py").read_text()
        self.assertIn('assert "READY" in msg["content"].upper()', src)
        self.assertIn('assert finish == "stop"', src)

    def test_qwen_keeps_previous_contract(self):
        SMOKE.request.model = "qwen3.8-flash-next-uncensored"
        calls, opener = self.fake(content="READY", reasoning="", finish="stop")
        with mock.patch.object(SMOKE.urllib.request, "urlopen", side_effect=opener):
            SMOKE.request(8192, [{"role": "user", "content": "Reply READY."}])
        self.assertEqual(calls[0]["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(calls[0]["max_tokens"], 192)

    def test_reasoning_alone_never_passes_roundtrip(self):
        SMOKE.request.last_response = {"choices": [{"finish_reason": "stop"}]}
        with self.assertRaisesRegex(AssertionError, "missing parsed visible answer"):
            SMOKE.require_visible_content({"content": "", "reasoning_content": "reason"}, "Roundtrip")


if __name__ == "__main__":
    unittest.main()
