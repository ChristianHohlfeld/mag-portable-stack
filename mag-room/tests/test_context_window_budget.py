"""Compaction follows the backend's real token window (prefix cache stays valid)."""
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

SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "native/magroom.cpp").read_text()


class ContextWindowBudget(unittest.TestCase):
    def test_budget_uses_real_n_ctx(self):
        # Live Flint: system+tools are 46k bytes > the 52000-byte budget, so every
        # new user turn rewrote messages[2] and re-prefilled ~49k tokens.
        self.assertIn('"default_generation_settings"', SOURCE)
        self.assertIn('n->asNum()', SOURCE)
        self.assertIn('(double)n_ctx * bpt * 0.80', SOURCE)
        self.assertIn('attempt_budget = window_context_budget(bot_idx, attempt_budget)', SOURCE)
        self.assertIn('note_wire_ratio(model, prompt_wire_bytes(messages, tools), saw_evaluated)', SOURCE)
        # Never smaller than the configured byte budget.
        self.assertIn('return std::max(fallback,', SOURCE)

    def test_history_window_caps_wire(self):
        # The per-bot wire must not grow unbounded (live: 49k tokens, minutes of prefill).
        self.assertIn('envi("MAG_ROOM_HISTORY_TOKENS", 32000)', SOURCE)
        self.assertIn('return std::max(fallback, std::min(window, history_cap));', SOURCE)
        # Trim in one big step so the cached prefix stays stable between trims.
        self.assertIn('const size_t target = limit * 60 / 100;', SOURCE)
        # System prompt stays byte-identical: compaction starts after messages[1].
        self.assertIn('const size_t at = rollup.empty() ? 2 : 3;', SOURCE)

    def test_backend_restart_restores_saved_slot(self):
        # A restarted llama-server has an empty KV: the warm flag must drop so the
        # prewarm restores the saved prefix file instead of skipping as "ram".
        self.assertIn('if (ROOM_MODEL_HEALTH[s - 1] == 2) {', SOURCE)
        self.assertIn('SLOT_WARM[s - 1] = 0;', SOURCE)
        self.assertIn('slot_mark_cold(s);', SOURCE)
        self.assertIn('restore_prefix_if_unwarmed(bot_idx, model.empty() ? bot_model_id(bot_idx) : model);', SOURCE)
        self.assertIn('restore_or_cold(source);', SOURCE)
        self.assertIn('std::string("/slots/0?action=") + action', SOURCE)
        # A fresh /slots document has no n_prompt_tokens. That is cold, not a
        # resident prefix, even if SLOT_WARM was left at 1.
        self.assertNotIn('!count_known && model_slot(idx) >= 1 &&', SOURCE)

    def test_chipin_reasoning_budget(self):
        # Complement turns bound hidden reasoning only; replies stay uncapped.
        self.assertIn('envi("MAG_ROOM_CHIPIN_REASONING_BUDGET", 256)', SOURCE)
        self.assertIn('if (coding_model && TL_CHIPIN && !TL_SELFTALK) {', SOURCE)
        self.assertIn('body["reasoning_budget_tokens"] = budget;', SOURCE)
        self.assertIn('const bool uncapped = coding_model && !TL_SELFTALK;', SOURCE)

    def test_short_turn_reasoning_budget(self):
        # Short first rounds bound hidden reasoning; tool rounds stay unlimited.
        self.assertIn('envi("MAG_ROOM_SHORT_REASONING_BUDGET", 256)', SOURCE)
        self.assertIn('envi("MAG_ROOM_SHORT_CODE_REASONING_BUDGET", 1024)', SOURCE)
        self.assertIn('envi("MAG_ROOM_SHORT_TURN_BYTES", 400)', SOURCE)
        self.assertIn('mag::str(messages.back(), "role") == "user"', SOURCE)
        # "Hi" is short even when the wire user message is the whole transcript.
        self.assertIn('!TURN_INPUT.empty() ? TURN_INPUT : wire_last', SOURCE)

    def test_reasoning_only_tail_not_persisted(self):
        self.assertIn('wire-drop-reasoning-only bot=', SOURCE)

    def test_finished_turns_drop_reasoning(self):
        # Old reasoning_content is not replayed (server: --no-reasoning-preserve).
        self.assertIn('if(CONFIGURED_MODEL&&envi("MAG_ROOM_WIRE_REASONING",0)==0) {', SOURCE)
        self.assertIn('wire-strip-reasoning bot=', SOURCE)
        launcher = (ROOT / 'ops/coding/start-live.sh').read_text() if 'ROOT' in globals() else ''
        if launcher:
            self.assertIn('--checkpoint-min-step 1024 --no-reasoning-preserve', launcher)

    def test_chipin_uses_append_only_wire(self):
        # Complements append to the bot's wire instead of a fresh [Raumverlauf] prompt.
        self.assertIn('} else if(CONFIGURED_MODEL && (!CURRENT_JOB || TL_CHIPIN) && !TL_TASK) {', SOURCE)
        self.assertIn('else if(TL_CHIPIN) extra+=group_chat::gate_instruction();', SOURCE)

    def test_stray_think_stripped(self):
        # A literal </think> in Flint's visible text must not reach room.json or the wire.
        self.assertIn('auto strip_stray_think = [&](std::string &text, bool move) {', SOURCE)
        self.assertIn('const bool fixed = strip_stray_think(t.text, true);', SOURCE)
        def strip(text):
            at = text.rfind('</think>')
            if at < 0: return text
            head, tail = text[:at].replace('<think>', ''), text[at + 8:].strip()
            return tail if tail else head.strip()
        self.assertEqual(strip('Hi!</think>Hi Chris!'), 'Hi Chris!')
        self.assertEqual(strip('Antwort</think>'), 'Antwort')
        self.assertEqual(strip('ok'), 'ok')

    def test_history_window_math(self):
        def budget(n_ctx, bpt, fallback=52000, tokens=32000):
            window = int(n_ctx * bpt * 0.80)
            return max(fallback, min(window, int(tokens * bpt)))
        # Flint 262k ctx, ~4 bytes/token: cap is 32k tokens, not ~210k.
        self.assertEqual(budget(262144, 4.0), 128000)
        # Small backend windows keep the n_ctx rule.
        self.assertEqual(budget(32768, 4.0), 104857)
        # Trim target leaves 40% headroom.
        self.assertEqual(128000 * 60 // 100, 76800)
        # First request after a restart must not compact the resident prefix.
        self.assertIn('double bpt = 4.0;', SOURCE)


if __name__ == "__main__":
    unittest.main()
