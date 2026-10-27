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
fail() { echo "$new" > "$STATE/failed-sha"; log "FAILED at $1 for $new"; exit 1; }
as nice -n 19 ionice -c 3 bash "$ROOT/ops/update.sh" --no-pull --no-deploy --no-auto || fail build
bash "$ROOT/ops/deploy.sh" || fail deploy
echo "$new" > "$STATE/deployed-sha"
rm -f "$STATE/failed-sha"
bash "$ROOT/ops/auto-update/install.sh" --units-only || log 'timer refresh failed'
bash "$ROOT/ops/runners/install.sh" || log 'runner install failed (deploy itself succeeded)'
bash "$ROOT/ops/boot/ensure-boot.sh" || log 'boot persistence check failed (deploy itself succeeded)'
bash "$ROOT/ops/boot/clear-failed-units.sh" || log 'WARN: a stack unit is still failed'
log "deployed $new"
