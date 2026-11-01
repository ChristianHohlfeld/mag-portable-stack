#!/usr/bin/env bash
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
python3 - "$root" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
deploy = (root / "ops/deploy.sh").read_text()
guard = (root / "ops/deploy-guard.sh").read_text()
start = (root / "ops/mistral/start.sh").read_text()
layout = deploy.index("if [[ $_layout_for_deadline == glm || $_layout_for_deadline == coding ]]; then")
long_deadline = deploy.index("_deploy_default_deadline=36000", layout)
export_deadline = deploy.index('export MAG_DEPLOY_DEADLINE_S="${MAG_DEPLOY_DEADLINE_S:-$_deploy_default_deadline}"', long_deadline)
wrapper = deploy.index('mag_deploy_arm_deadline "$@"', export_deadline)
lock = deploy.index('exec 8>/run/lock/mag-private-deploy.lock')
watchdog = deploy.index('deploy_watchdog_arm "$MAG_DEPLOY_DEADLINE_S"', lock)
assert layout < long_deadline < export_deadline < wrapper < lock < watchdog
assert "glm || $_layout_for_deadline == coding" in deploy, "coding rollback needs the Mistral cold-start budget"
assert 'local deadline=${MAG_DEPLOY_DEADLINE_S:-3600}' in guard
assert "--load-mode mmap" in start and "--mmap" not in start
print("Mistral deploy deadline is selected before timeout wrapper; launcher uses --load-mode mmap")
PY
bash -n "$root/ops/deploy.sh" "$root/ops/mistral/start.sh" "$root/ops/tests/test-deploy-deadline.sh"
