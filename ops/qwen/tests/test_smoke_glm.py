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
        SMOKE.request.last_response = {"choices": [{"finish_reason": "stop"}]}

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
        self.assertEqual(calls[0]["chat_template_kwargs"], {"enable_thinking": False, "reasoning_effort": "low", "clear_thinking": True})
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
        self.assertEqual(calls[0]["chat_template_kwargs"], {"enable_thinking": True, "reasoning_effort": "low", "clear_thinking": True})
        self.assertEqual(calls[0]["max_tokens"], 512)
        SMOKE.require_visible_content(msg, "Normal chat failed")

    def test_chat_requires_ready_and_stop(self):
        src = (ROOT / "ops/qwen/smoke.py").read_text()
        self.assertIn('require_glm_ready(msg, "Normal chat failed")', src)
        self.assertIn('assert finish == "stop"', src)
        self.assertIn('require_glm_ready(cached_msg, "Cached normal chat failed")', src)
        self.assertIn('require_glm_ready(again, "Tool result roundtrip failed", roundtrip=True)', src)

    def test_glm_accepts_real_numa1_reply_and_roundtrip_with_prose(self):
        # The actual IQ2_XS live response on mag: previously a false failure.
        normal = ("READY.\n\nWhat would you like to do? I'm ready to help with "
                  "whatever you need — whether it's a question, a task, or "
                  "just exploring an idea.")
        for content in ("READY", "ready.", " Ready! ", normal,
                        "READY. What would you like to do?"):
            with self.subTest(content=content):
                SMOKE.require_glm_ready({"content": content}, "Normal chat")
        for content in ("READY", "echo: READY.", "Echo: ready!",
                        "ECHO: READY\n\nThe tool successfully returned the value."):
            with self.subTest(content=content):
                SMOKE.require_glm_ready({"content": content}, "Roundtrip",
                                        roundtrip=True)

    def test_glm_rejects_false_positives_and_token_leaks(self):
        for content in (
            "I'm sorry, but I cannot answer that question. </think>READY.",
            "</think>READY",
            "nonsense READY",
            "NOTREADY",
            "READYXYZ",
            "echo: 0x7f4a0e5c...",
            "just another answer",
            "",
            "READY.\n<think>secret thinking</think>",
            "READY.\n[ gMASK ] <|assistant|> a leaked delimiter",
            "READY. " + "x" * 500,
        ):
            with self.subTest(content=content):
                with self.assertRaisesRegex(AssertionError, "READY at start"):
                    SMOKE.require_glm_ready({"content": content}, "GLM")

        for extra in ({"reasoning_content": "unwanted thinking"},
                      {"tool_calls": [{"function": {}}]}):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(AssertionError, "READY at start"):
                    SMOKE.require_glm_ready(dict(content="READY", **extra), "GLM")

        for finish in ("length", "tool_calls", None):
            with self.subTest(finish=finish):
                SMOKE.request.last_response = {"choices": [{"finish_reason": finish}]}
                with self.assertRaisesRegex(AssertionError, "finish_reason=stop"):
                    SMOKE.require_glm_ready({"content": "READY"}, "GLM")
                with self.assertRaisesRegex(AssertionError, "finish_reason=stop"):
                    SMOKE.require_glm_ready({"content": "ECHO: READY"}, "Roundtrip",
                                            roundtrip=True)

    def test_glm_roundtrip_rejects_unrelated_text(self):
        for content in ("echo: 0x7f4a0e5c...", "random text with READY",
                        "</think>READY", "READYish", "READY, 0x1"):
            with self.subTest(content=content):
                with self.assertRaisesRegex(AssertionError, "READY at start"):
                    SMOKE.require_glm_ready({"content": content}, "Roundtrip",
                                            roundtrip=True)

    def test_glm_readiness_and_tool_prompt_are_explicit(self):
        # Check both the initial and identical cached request use the stricter
        # instruction, while the original Qwen user prompts remain available.
        import inspect
        source = inspect.getsource(SMOKE.main)
        self.assertIn("Reply with exactly the single word READY and nothing else.", source)
        self.assertIn("cached_msg = request(opt.port, ready_prompt)", source)
        self.assertIn("begin your final reply with ECHO: READY", source)

    def test_qwen_keeps_previous_contract(self):
        SMOKE.request.model = "qwen3.8-flash-next-uncensored"
        calls, opener = self.fake(content="READY", reasoning="", finish="stop")
        with mock.patch.object(SMOKE.urllib.request, "urlopen", side_effect=opener):
            SMOKE.request(8192, [{"role": "user", "content": "Reply READY."}])
        self.assertEqual(calls[0]["chat_template_kwargs"], {"enable_thinking": False, "reasoning_effort": "low", "clear_thinking": True})
        self.assertEqual(calls[0]["max_tokens"], 192)

    def test_reasoning_alone_never_passes_roundtrip(self):
        SMOKE.request.last_response = {"choices": [{"finish_reason": "stop"}]}
        with self.assertRaisesRegex(AssertionError, "missing parsed visible answer"):
            SMOKE.require_visible_content({"content": "", "reasoning_content": "reason"}, "Roundtrip")


if __name__ == "__main__":
    unittest.main()
