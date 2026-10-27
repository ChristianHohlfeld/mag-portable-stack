#!/usr/bin/env bash
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
# Deploy lock, deploy deadline, and the free-MCP probe. No host services.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
guard=$root/ops/deploy-guard.sh
probe=$root/ops/trust/probe-free-mcp.sh
# shellcheck source=../deploy-guard.sh
source "$guard"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# The deploy shell never has the lock fd, so a child cannot inherit it.
# After the holder exits the lock is free while that child is still asleep.
lock=$tmp/lock
listing=$tmp/fds
ready=$tmp/ready
pidfile=$tmp/child
cat >"$tmp/child.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ls -l /proc/$$/fd > "$1"
echo ok > "$2"
exec sleep 40
EOF
cat >"$tmp/holder.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "$guard"
export MAG_DEPLOY_LOCK=\$1
mag_deploy_lock "\$@"
bash "$tmp/child.sh" "\$2" "\$3" &
echo \$! > "\$4"
disown || true
for _ in \$(seq 1 50); do
  [ -s "\$3" ] && exit 0
  sleep 0.05
done
exit 1
EOF
bash "$tmp/holder.sh" "$lock" "$listing" "$ready" "$pidfile"
[ -s "$ready" ]
if grep -F "$lock" "$listing"; then
  echo 'deploy lock fd was inherited by a child' >&2
  exit 1
fi
flock -n "$lock" -c true
kill "$(cat "$pidfile")" 2>/dev/null || true
wait "$(cat "$pidfile")" 2>/dev/null || true

# The deploy process group dies inside its deadline and releases the lock.
slow=$tmp/slow.sh
lock2=$tmp/lock2
cat >"$slow" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "$guard"
export MAG_DEPLOY_DEADLINE_S=2
export MAG_DEPLOY_LOCK=$lock2
mag_deploy_arm_deadline "\$@"
mag_deploy_lock "\$@"
sleep 30
EOF
chmod +x "$slow"
start=$SECONDS
set +e
bash "$slow"
code=$?
set -e
elapsed=$((SECONDS - start))
[ "$code" -ne 0 ]
[ "$elapsed" -lt 8 ]
flock -n "$lock2" -c true

# A hung curl cannot outlive the probe budget. HTTP 429 is not a failure.
bindir=$tmp/bin
mkdir -p "$bindir"
ca=$tmp/ca.pem
printf 'dummy\n' >"$ca"
cat >"$bindir/curl" <<'EOF'
#!/bin/sh
sleep 30
EOF
chmod +x "$bindir/curl"
start=$SECONDS
set +e
PATH="$bindir:$PATH" MAG_FREE_MCP_PROBE_TIMEOUT=2 MAG_FREE_MCP_CA="$ca" \
  MAG_ROOM_MCP_DP_URL=https://example.invalid/mcp \
  bash "$probe"
code=$?
set -e
elapsed=$((SECONDS - start))
[ "$code" -eq 124 ]
[ "$elapsed" -lt 8 ]
cat >"$bindir/curl" <<'EOF'
#!/bin/sh
printf '429'
EOF
chmod +x "$bindir/curl"
PATH="$bindir:$PATH" MAG_FREE_MCP_PROBE_TIMEOUT=5 MAG_FREE_MCP_CA="$ca" \
  MAG_ROOM_MCP_DP_URL=https://example.invalid/mcp \
  bash "$probe"
if grep -E '^[[:space:]]*[^#].*--retry' "$probe"; then
  echo 'free MCP probe must not retry' >&2
  exit 1
fi
grep -q mag_deploy_arm_deadline "$root/ops/deploy.sh"
grep -q deploy_watchdog_arm "$root/ops/deploy.sh"
grep -q deploy_lock_cloexec "$root/ops/deploy.sh"
! grep -Eq 'systemctl (restart|stop|reload).*mag-llm' "$root/ops/deploy.sh"
echo HANG_DEADLINES_OK
