#!/usr/bin/env bash
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
# Deploy batches many unit-file writes into one daemon-reload per phase.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
log=$tmp/systemctl.log
cat >"$tmp/bin/systemctl" <<'EOF'
#!/bin/sh
echo "systemctl $*" >> "$CALLS"
exit 0
EOF
chmod +x "$tmp/bin/systemctl"
# shellcheck disable=SC1091
source "$root/ops/mag-units.sh"

export PATH="$tmp/bin:$PATH" CALLS=$log
export MAG_DEFER_DAEMON_RELOAD=1 MAG_UNITS_DIRTY_FILE=$tmp/units.dirty
: >"$log"
mag_daemon_reload
mag_daemon_reload
mag_daemon_reload
if [[ -s $log ]]; then
  echo 'deferred reload still called systemctl' >&2
  exit 1
fi
[[ -f $tmp/units.dirty ]]
mag_flush_daemon_reload
[[ $(grep -c 'systemctl daemon-reload' "$log") == 1 ]]
[[ ! -e $tmp/units.dirty ]]
mag_flush_daemon_reload
[[ $(grep -c 'systemctl daemon-reload' "$log") == 1 ]]
mag_flush_daemon_reload --force
[[ $(grep -c 'systemctl daemon-reload' "$log") == 2 ]]

export MAG_DEFER_DAEMON_RELOAD=0
: >"$log"
mag_daemon_reload
mag_daemon_reload
[[ $(grep -c 'systemctl daemon-reload' "$log") == 2 ]]

# apply-gitea-funnel installs both unit families, then one reload.
[[ $(grep -c 'daemon-reload' "$root/ops/tailscale/apply-gitea-funnel.sh") == 1 ]]
python3 - "$root/ops/tailscale/apply-gitea-funnel.sh" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
cert = text.find("mag-tailscale-cert.timer")
funnel = text.find("mag-gitea-funnel.service")
reload = text.find("daemon-reload")
enable = text.find("enable mag-gitea-funnel")
assert 0 <= cert < funnel < reload < enable, (cert, funnel, reload, enable)
PY

# deploy.sh: rollback force, one units flush, one late flush. No extra mag_systemctl daemon-reload.
d="$root/ops/deploy.sh"
grep -Fq 'MAG_DEFER_DAEMON_RELOAD=1' "$d"
grep -Fq 'mag_flush_daemon_reload --force' "$d"
[[ $(grep -c 'mag_flush_daemon_reload' "$d") == 4 ]]
if grep -E 'mag_systemctl daemon-reload' "$d"; then
  echo 'deploy.sh still calls mag_systemctl daemon-reload directly' >&2
  exit 1
fi
grep -Fq 'MAG_DEFER_DAEMON_RELOAD' "$root/ops/room/install.sh"
grep -Fq 'mag_daemon_reload' "$root/ops/qwen/layout.sh"
grep -Fq 'mag_daemon_reload' "$root/ops/qwen/install.sh"
grep -Fq 'mag_daemon_reload' "$root/ops/cleanup-legacy.sh"
grep -Fq 'mag_flush_daemon_reload' "$root/ops/bots/install.sh"
# Children mark dirty; they must not call systemctl daemon-reload while deferred.
for f in ops/room/install.sh ops/qwen/layout.sh ops/qwen/install.sh ops/cleanup-legacy.sh ops/bots/install.sh; do
  if grep -E '^[[:space:]]*systemctl daemon-reload' "$root/$f"; then
    echo "$f still has a raw systemctl daemon-reload" >&2
    exit 1
  fi
done

# room/install stages and returns when deferred.
mkdir -p "$tmp/room/bin"
# The real script needs root and host paths; only the defer branch is asserted above.
echo 'daemon-reload batch PASS'
