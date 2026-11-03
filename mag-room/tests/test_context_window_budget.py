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
