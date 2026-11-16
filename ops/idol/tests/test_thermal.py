#!/usr/bin/env python3
"""Deterministic MI50 thermal governor regressions; no GPU or elevated access."""
import importlib.util
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / "worker.py"

def run():
    with tempfile.TemporaryDirectory(prefix="mi50-thermal-test-") as path:
        device = Path(path)
        (device / "power_dpm_force_performance_level").write_text("auto\n")
        (device / "pp_dpm_sclk").write_text("0\n")
        # A thermal patch MUST NEVER modify the separate 78 W power cap.
        (device / "power1_cap").write_text("78000000\n")
        first_cap_mtime = (device / "power1_cap").stat().st_mtime_ns
        with patch.dict(os.environ, MAG_GPU_SYSFS=str(device),
                        MAG_GPU_THROTTLE_RESTORE_S="60"):
            spec = importlib.util.spec_from_file_location("mag_thermal_worker_test", HERE)
            worker = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(worker)

        w = worker
        def clocks():
            return ((device / "power_dpm_force_performance_level").read_text().strip(),
                    (device / "pp_dpm_sclk").read_text().strip())

        assert w.set_clocks("auto")
        w.govern_clocks(83, 1)
        assert clocks() == ("manual", "0 1 2")
        w.govern_clocks(89, 2)
        assert clocks() == ("manual", "0")
        # Oscillation 80..90 C, including every-second 87/88 crossings:
        # no low/lowest ping-pong, and no temperature-triggered SIGSTOP.
        for n, temperature in enumerate([87, 88, 80, 90, 84, 88, 87, 81] * 12):
            w.govern_clocks(temperature, 3 + n)
            assert clocks() == ("manual", "0"), (n, temperature, clocks())
        t = 100
        w.govern_clocks(77, t)      # first cool sample starts the hold
        w.govern_clocks(79, t + 60) # warmer sample cancels it
        assert clocks() == ("manual", "0")
        w.govern_clocks(77, t + 61)
        w.govern_clocks(77, t + 121)
        assert clocks() == ("manual", "0 1 2")
        w.govern_clocks(74, t + 122)
        w.govern_clocks(74, t + 182)
        assert clocks()[0] == "auto"

        job = "c85d53f5" + "0" * 24
        item = {"id": job, "pid": 100, "pid_start": 101}
        assert not w.should_hold(job, 90, 100, 10), "active 100% GPU kernel is not a process hold"
        assert w.should_hold(job, 90, 0, 10), "quiescent GPU can be stopped for cooling"
        assert w.should_hold(job, 90, None, 10), "missing busy telemetry keeps safety fallback"
        assert w.should_hold(job, 95, 100, 10), "emergency stops new submissions even while busy"
        w.held_at[job] = 10
        with patch.object(w, "stage_alive", return_value=True):
            for sec in range(10, 23):
                w.note_busy(job, 91, 100, sec)
            assert w.release_reason(job, item, 91, 23) == "gpu_busy"
            w.held_hist[job] = [(10, 95), (23, 91)]
            # Above emergency do not bypass the thermal safeguard.
            assert w.release_reason(job, item, 95, 23) is None
        w.forget_hold(job)
        w.hold_rearm_at[job] = 150
        assert not w.should_hold(job, 90, 0, 100), "re-arm hysteresis prevents rapid re-hold"
        assert w.should_hold(job, 90, 0, 151)
        assert w.should_hold(job, 95, 100, 100), "emergency bypasses re-arm"
        assert (device / "power1_cap").read_text() == "78000000\n"
        assert (device / "power1_cap").stat().st_mtime_ns == first_cap_mtime
        print("mi50 thermal governor ok")


if __name__ == "__main__":
    run()
