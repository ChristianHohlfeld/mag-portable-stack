#!/usr/bin/env bash
# Runs as root from mag-auto-update.timer (and from mag-deploy-request.path).
# Branch `green` is promoted by GitHub Actions, never by Gitea.
# If it moved: fast-forward the checkout, build as the checkout owner, then
# deploy. deploy.sh keeps its backup and automatic rollback. A failed commit is
# retried once an hour. GitHub unreachable, or no `green` yet: nothing happens.
# No model unit is ever touched. GitHub is the only source.
set -euo pipefail
SCRIPT_ROOT="$(cd -- "$(dirname "$0")/../.." && pwd)"
log() { echo "auto-update: $*" >&2; }

if [[ ${MAG_AUTO_UPDATE_TEST:-} == 1 ]]; then
  ROOT="${MAG_AUTO_UPDATE_ROOT:?}"
  STATE="${MAG_AUTO_UPDATE_STATE:?}"
  as() { "$@"; }
else
  ROOT="$SCRIPT_ROOT"
  STATE=/var/lib/mag-stack/auto-update
  [[ $EUID == 0 ]] || { log 'root required' >&2; exit 1; }
  [[ ! -e /etc/mag-stack/auto-update.disabled ]] || { log 'disabled'; exit 0; }
  install -d -m 0755 /run/lock "$STATE"
  exec 9>/run/lock/mag-auto-update.lock
  flock -n 9 || { log 'another run is active'; exit 0; }
  OWNER="$(stat -c %U "$ROOT")"
  as() { runuser -u "$OWNER" -- "$@"; }
fi

branch="$(as git -C "$ROOT" symbolic-ref --short HEAD 2>/dev/null || true)"
[[ "$branch" == main ]] || { log "checkout is on '$branch', not main; skipping"; exit 0; }
[[ -z "$(as git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || { log 'local changes in checkout; skipping'; exit 0; }
ref="$(as bash "$SCRIPT_ROOT/ops/ci/fetch-github-green.sh" --green-only "$ROOT")" || { log 'GitHub green not reachable yet; nothing to do'; exit 0; }
new="$(as git -C "$ROOT" rev-parse "$ref")"
[[ "$new" != "$(cat "$STATE/deployed-sha" 2>/dev/null || true)" ]] || exit 0
# The failed-sha file is a SHA-specific cooldown, not a permanent block.
# An unrelated new green SHA is always attempted immediately.
source "$SCRIPT_ROOT/ops/auto-update/failure-state.sh"
remaining=$(mag_retry_remaining "$new")
if (( remaining > 0 )); then
  log "$new: last failure at $(cat "$STATE/failed-stage" 2>/dev/null || echo unknown); retry in ${remaining}s"
  exit 0
fi
fail() { mag_record_failure "$new" "$1"; log "FAILED at $1 for $new"; exit 1; }
log "updating to $new"
as git -C "$ROOT" merge -q --ff-only "$ref" || fail merge
[[ "$(as git -C "$ROOT" rev-parse HEAD)" == "$new" ]] || { log 'checkout is ahead of green; skipping'; exit 0; }
if [[ ${MAG_AUTO_UPDATE_TEST:-} == 1 ]]; then
  printf '%s\n' "$new"
  exit 0
fi
# Continue with the freshly merged code; the lock (fd 9) is inherited.
exec bash "$ROOT/ops/auto-update/apply.sh" "$new"
