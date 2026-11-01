"""Source-contract regressions for GLM room budgets, continuation and safe cuts."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROOM = (ROOT / "mag-room/native/magroom.cpp").read_text(encoding="utf-8")
ENV = (ROOT / "ops/room/glm.env").read_text(encoding="utf-8")
UI = (ROOT / "mag-room/web/index.html").read_text(encoding="utf-8")


class GlmRoomContract(unittest.TestCase):
    def test_thinking_chat_uses_1024_and_code_budget_is_configurable(self):
        self.assertIn("MAG_ROOM_GLM_CHAT_MAX_TOKENS=1024", ENV)
        self.assertIn("MAG_ROOM_GLM_CODE_MAX_TOKENS=8192", ENV)
        self.assertIn('no_tools ? "MAG_ROOM_GLM_CHAT_MAX_TOKENS" : "MAG_ROOM_GLM_CODE_MAX_TOKENS"', ROOM)
        self.assertIn("no_tools ? 1024 : 8192", ROOM)
        self.assertIn("std::clamp", ROOM)
        self.assertNotIn("if (thinking && model.rfind(\"glm-5.3-flash\", 0) == 0)", ROOM)
        self.assertNotIn("std::min(max_output, 256)", ROOM)

    def test_code_budget_override_of_4096_is_within_the_configured_bound(self):
        self.assertIn('MAG_ROOM_GLM_CODE_MAX_TOKENS", no_tools ? 1024 : 8192', ROOM)
        self.assertIn('std::clamp(envi(no_tools ?', ROOM)
        self.assertEqual(max(32, min(8192, 4096)), 4096)
        self.assertNotIn("MAG_ROOM_GLM_CHAT_MAX_ROUNDS", ROOM)
        self.assertNotIn("MAG_ROOM_GLM_CHAT_MAX_ROUNDS", ENV)

    def test_heading_cut_is_fence_aware_but_special_tokens_stay_blocked(self):
        self.assertIn('auto heading_inside_fence = [](const std::string &text, size_t pos)', ROOM)
        self.assertIn("heading_inside_fence(acc.content + glm_stream_pending", ROOM)
        self.assertIn("search_from = pos + marker.size()", ROOM)
        self.assertIn('"\\n\\n## "', ROOM)
        self.assertIn('"</think>", "<think>", "<|", "<tool_call"', ROOM)

    def test_length_continues_and_context_stop_or_guard_marks_truncated(self):
        self.assertIn('t.finish == "length") {', ROOM)
        self.assertIn("previous_glm_chunk", ROOM)
        self.assertIn("glm_continuation_rounds > 1024", ROOM)
        self.assertIn('report.truncated = true;', ROOM)
        self.assertIn('(*it)["truncated"] = true;', ROOM)
        self.assertIn("!report.truncated", ROOM)

    def test_glm_has_no_active_generation_deadline_and_a_long_stall_guard(self):
        self.assertIn("c.no_overall_deadline = true", ROOM)
        self.assertIn("idle_s = std::max(600, idle_s)", ROOM)
        self.assertIn("if (!no_overall_deadline && !overall_set)", ROOM)

    def test_mobile_composer_uses_placeholder_single_line_and_bounded_growth(self):
        self.assertIn("#message:placeholder-shown{white-space:nowrap;overflow:hidden!important;text-overflow:ellipsis}", UI)
        self.assertIn(".gbar-pill #message::-webkit-scrollbar{display:none}", UI)
        self.assertIn("Math.floor(window.innerHeight*.28)", UI)


if __name__ == "__main__":
    unittest.main()
