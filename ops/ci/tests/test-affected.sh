#!/usr/bin/env bash
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
# Path lists in GitHub workflows match ops/ci/affected.sh, and a C++ edit does
# not pull in Playwright, speech or the blank-host bootstrap.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../../.." && pwd)
cd "$root"
aff=ops/ci/affected.sh
python3 - << 'PY'
import subprocess, yaml
from pathlib import Path
root = Path('.')
suites = ['native','pages','nginx','recovery','room','chromium','speech','e2e-bootstrap']
for suite in suites:
    paths = subprocess.check_output(['bash','ops/ci/affected.sh','--paths',suite], text=True).splitlines()
    wf = subprocess.check_output(['bash','ops/ci/affected.sh','--workflow',suite], text=True).strip()
    doc = yaml.safe_load(Path(wf).read_text())
    on = doc.get('on', doc.get(True))
    got = on['pull_request']['paths']
    if got != paths:
        raise SystemExit(f'{wf} paths drifted from affected.sh:\n  yaml={got}\n  tool={paths}')
    if on['push'].get('branches') != ['main'] or 'paths' in (on['push'] or {}):
        raise SystemExit(f'{wf} push is not a full main run')
print('workflow paths match affected.sh')
PY
match() { printf '%s\n' "$2" | bash "$aff" --files "$1"; }
match native mag-room/native/magroom.cpp
match native ops/deploy.sh
match native devices/s23/s23ctl.cpp
if match room mag-room/native/magroom.cpp; then echo 'room filter matched C++' >&2; exit 1; fi
if match speech mag-room/native/magroom.cpp; then echo 'speech filter matched C++' >&2; exit 1; fi
if match e2e-bootstrap mag-room/native/magroom.cpp; then echo 'bootstrap filter matched C++' >&2; exit 1; fi
if match chromium mag-room/native/magroom.cpp; then echo 'chromium filter matched C++' >&2; exit 1; fi
match room mag-room/web/index.html
match chromium mag-room/web/index.html
match e2e-bootstrap ops/tests/e2e-bootstrap.sh
match e2e-bootstrap ops/bootstrap.sh
match speech mag-room/static/speech-ui.js
match speech mag-room/tests/test_speech_real.py
match pages ops/pages/reconcile.sh
match pages mag-room/native/pages.hpp
match nginx ops/private-access/install.sh
match nginx mag-room/native/tailscale_auth.hpp
match recovery ops/backup/backup.sh
match recovery ops/deploy-guard.sh
if match recovery mag-room/native/magroom.cpp; then echo 'recovery filter matched C++' >&2; exit 1; fi
# Same sha is a full run, including the slow suites.
MAG_CI_BASE=abc MAG_CI_SHA=abc bash "$aff" e2e-bootstrap
MAG_CI_BASE=abc MAG_CI_SHA=abc bash "$aff" speech
# Security suites always run.
MAG_CI_BASE=abc MAG_CI_SHA=def bash "$aff" gitleaks
MAG_CI_BASE=abc MAG_CI_SHA=def bash "$aff" secrets
# Unreadable diff fails open (run), rather than skipping coverage.
MAG_CI_BASE=0123456789abcdef0123456789abcdef01234567 MAG_CI_SHA=fedcba9876543210fedcba9876543210fedcba98 \
  MAG_CI_SOURCE=/tmp bash "$aff" room
echo 'affected path filters PASS'
