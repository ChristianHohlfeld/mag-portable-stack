#!/usr/bin/env bash
# Runs as root from mag-auto-update.timer. Branch `green` is the newest main
# commit on which all CI checks passed (.github/workflows/green.yml). If it
# moved: fast-forward the checkout, build as the checkout owner, then deploy.
# deploy.sh keeps its backup and automatic rollback. A failed commit is retried
# once an hour. No network or no `green` yet: nothing happens. no model unit is ever touched.
set -euo pipefail
ROOT="$(cd -- "$(dirname "$0")/../.." && pwd)"
STATE=/var/lib/mag-stack/auto-update
log() { echo "auto-update: $*"; }
[[ $EUID == 0 ]] || { log 'root required' >&2; exit 1; }
[[ ! -e /etc/mag-stack/auto-update.disabled ]] || { log 'disabled'; exit 0; }
install -d -m 0755 /run/lock "$STATE"
exec 9>/run/lock/mag-auto-update.lock
flock -n 9 || { log 'another run is active'; exit 0; }
OWNER="$(stat -c %U "$ROOT")"
as() { runuser -u "$OWNER" -- "$@"; }
branch="$(as git -C "$ROOT" symbolic-ref --short HEAD 2>/dev/null || true)"
[[ "$branch" == main ]] || { log "checkout is on '$branch', not main; skipping"; exit 0; }
[[ -z "$(as git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || { log 'local changes in checkout; skipping'; exit 0; }
as git -C "$ROOT" fetch -q origin green 2>/dev/null || { log 'origin/green not reachable yet; nothing to do'; exit 0; }
new="$(as git -C "$ROOT" rev-parse origin/green)"
[[ "$new" != "$(cat "$STATE/deployed-sha" 2>/dev/null || true)" ]] || exit 0
if [[ "$new" == "$(cat "$STATE/failed-sha" 2>/dev/null || true)" && -z "$(find "$STATE/failed-sha" -mmin +60)" ]]; then
  log "$new failed less than an hour ago; retrying later"; exit 0
fi
fail() { echo "$new" > "$STATE/failed-sha"; log "FAILED at $1 for $new"; exit 1; }
log "updating to $new"
as git -C "$ROOT" merge -q --ff-only origin/green || fail merge
[[ "$(as git -C "$ROOT" rev-parse HEAD)" == "$new" ]] || { log 'checkout is ahead of green; skipping'; exit 0; }
# Continue with the freshly merged code; the lock (fd 9) is inherited.
exec bash "$ROOT/ops/auto-update/apply.sh" "$new"
