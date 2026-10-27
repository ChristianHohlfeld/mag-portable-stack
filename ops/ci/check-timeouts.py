#!/usr/bin/env python3
"""Fail when a test or a workflow job/step has no deadline.

    python3 ops/ci/check-timeouts.py          # CI guard
    python3 ops/ci/check-timeouts.py --stamp  # insert missing markers

Python tests call ops/ci/mag_deadline.py. Shell tests re-exec under
timeout(1). Native binaries call mag_test_deadline. Node contracts arm
mag_deadline.cjs. The Go UDP test refuses a missing or long -timeout.
Every job and step in .github/workflows and .gitea/workflows carries
timeout-minutes.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PY_SECONDS = {
    'mag-room/tests/test_speech_real.py': 600,
    'mag-room/tests/test_room_e2e.py': 240,
    'mag-room/tests/test_hang_invariants.py': 90,
    'ops/deepseek/preflight-tests.py': 3900,
    'mag-room/tests/test_native.py': 300,
}
SH_SECONDS = {
    'ops/tests/e2e-bootstrap.sh': 5400,
    'ops/pages/tests/test-pages-lifecycle.sh': 600,
    'ops/pages/tests/test-pages-sandbox.sh': 600,
    'ops/tests/test-hang-deadlines.sh': 60,
    'ops/ci/tests/test-affected.sh': 60,
    'ops/ci/tests/test-ci-reap.sh': 60,
    'ops/ci/tests/test-native-build.sh': 60,
    'ops/ci/tests/test-local-ci.sh': 60,
    'ops/tests/test-daemon-reload-batch.sh': 60,
    'ops/backup/tests/test-backup.sh': 300,
}
JOB_MINUTES = {
    'native': 90,
    'room': 45,
    'speech': 60,
    'gitleaks': 15,
    'pages': 40,
    'secrets': 15,
    'nginx': 30,
    'recovery': 45,
    'chromium': 30,
    'e2e-bootstrap': 120,
    'promote-green': 120,
    'deploy-live': 5,
    'publish': 5,
    'checks': 1440,
}
