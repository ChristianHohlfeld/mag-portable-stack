#!/usr/bin/env bash
# Dry-run only: update builds before it deploys and never touches mag-llm.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
bash -n "$root/ops/update.sh"
out=$(bash "$root/ops/update.sh" --dry-run)
for phase in pull build deploy auto-update runners; do grep -Fq "PHASE $phase" <<<"$out"; done
grep -Fq 'cmake --build' <<<"$out"
grep -Fq 'ops/deploy.sh' <<<"$out"
[[ $(grep -n 'PHASE build' <<<"$out" | cut -d: -f1) -lt $(grep -n 'PHASE deploy' <<<"$out" | cut -d: -f1) ]]
! grep -Eq 'mag-llm' "$root/ops/update.sh"
bash -n "$root"/ops/auto-update/*.sh
grep -Fq 'auto-update/install.sh' <<<"$out"
grep -Fq -- '--ff-only' "$root/ops/auto-update/auto-update.sh"
grep -Fq 'failed-sha' "$root/ops/auto-update/auto-update.sh"
! grep -Eq 'mag-llm' "$root"/ops/auto-update/*
grep -Fq 'OnUnitInactiveSec' "$root/ops/auto-update/mag-auto-update.timer"
# deploy.sh runs as root from the timer (no SUDO_UID): every git call on the
# user-owned checkout must allow it explicitly.
! grep -E '(^|[^-])\bgit -C' "$root/ops/deploy.sh" | grep -vF 'safe.directory=' | grep -q .
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git init -q "$tmp/repo" && git -C "$tmp/repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m t
line=$(grep -E '\bgit .*rev-parse HEAD' "$root/ops/deploy.sh")
[[ $(env -u SUDO_UID GIT_TEST_ASSUME_DIFFERENT_OWNER=1 SOURCE="$tmp/repo" bash -c "${line%%>*}") =~ ^[0-9a-f]{40}$ ]]
echo 'Update dry-run PASS'
