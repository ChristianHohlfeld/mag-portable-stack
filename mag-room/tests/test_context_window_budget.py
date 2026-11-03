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


if __name__ == "__main__":
    unittest.main()
