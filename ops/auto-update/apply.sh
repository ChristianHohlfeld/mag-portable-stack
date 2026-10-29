#!/usr/bin/env bash
# Second half of auto-update.sh, run from the merged checkout: build, deploy,
# then refresh the timer and the runners. Not meant to be run by hand.
set -euo pipefail
ROOT="$(cd -- "$(dirname "$0")/../.." && pwd)"
STATE=/var/lib/mag-stack/auto-update
new="${1:?commit}"
log() { echo "auto-update: $*"; }
OWNER="$(stat -c %U "$ROOT")"
as() { runuser -u "$OWNER" -- "$@"; }
source "$ROOT/ops/auto-update/failure-state.sh"
fail() { mag_record_failure "$new" "$1"; log "FAILED at $1 for $new"; exit 1; }
as nice -n 19 ionice -c 3 bash "$ROOT/ops/update.sh" --no-pull --no-deploy --no-auto || fail build
# A prior failed model cutover is not a successful deployment of the old layout.
# Probe the pinned, source-declared desired layout *before* backup, release
# staging or any model service is touched; a matching failure is held until
# its definition changes or an operator explicitly requests a new attempt.
layout_rc=0
bash "$ROOT/ops/qwen/layout.sh" probe "$STATE" || layout_rc=$?
if (( layout_rc == 78 )); then
  log "deployment held: desired NUMA1 layout previously failed; deployed-sha unchanged"
  exit 0
elif (( layout_rc != 0 )); then
  fail "layout-probe-rc-$layout_rc"
fi
deploy_rc=0
bash "$ROOT/ops/deploy.sh" || deploy_rc=$?
if (( deploy_rc == 75 )); then
  # Drain postponed a live conversation: not a broken release. Retry on the
  # next regular timer tick, without penalizing this SHA for one hour.
  log "deployment postponed by active user work (rc=75); retry on next timer tick"
  exit 0
elif (( deploy_rc != 0 )); then
  fail "deploy-rc-$deploy_rc"
fi
echo "$new" > "$STATE/deployed-sha"
rm -f "$STATE/failed-sha" "$STATE/failed-count" "$STATE/failed-stage"
bash "$ROOT/ops/auto-update/install.sh" --units-only || log 'timer refresh failed'
bash "$ROOT/ops/runners/install.sh" || log 'runner install failed (deploy itself succeeded)'
bash "$ROOT/ops/boot/ensure-boot.sh" || log 'boot persistence check failed (deploy itself succeeded)'
log "deployed $new"
