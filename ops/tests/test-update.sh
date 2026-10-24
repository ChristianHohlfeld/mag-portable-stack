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
# Post-restart local health checks wait for the listener (Type=simple units
# return from `systemctl restart` before they listen): no bare top-level curl,
# and wait_http survives a listener that comes up late.
! grep -nE '^curl .*127\.0\.0\.1:(8766|18784)' "$root/ops/deploy.sh"
grep -Fxq 'wait_http http://127.0.0.1:18784/health' "$root/ops/deploy.sh"
# First-install gateway gets the same bounded readiness wait: immediately
# curling :8080 after restarting a Type=simple unit races the port bind.
grep -Fq 'gateway_models=$(wait_http http://127.0.0.1:8080/v1/models)' "$root/ops/deploy.sh"
[[ $(grep -n 'gateway_models=$(wait_http http://127.0.0.1:8080/v1/models)' "$root/ops/deploy.sh" | cut -d: -f1) -gt \
   $(grep -n '^systemctl restart mag-audit-gateway.service' "$root/ops/deploy.sh" | tail -1 | cut -d: -f1) ]]
[[ $(grep -n 'wait_http http://127.0.0.1:18784/health' "$root/ops/deploy.sh" | cut -d: -f1) -gt \
   $(grep -n 'systemctl restart mag-browser-webrtc.service' "$root/ops/deploy.sh" | tail -1 | cut -d: -f1) ]]
[[ $(tail -2 "$root/ops/rollback.sh" | head -1) == 'for i in {1..30}; do curl '*'/healthz 2>/dev/null && break; sleep 1; done' ]]
mkdir "$tmp/bin"
printf '#!/bin/sh\nn=$(($(cat "%s/n" 2>/dev/null || echo 0) + 1)); echo $n > "%s/n"\n[ $n -ge 4 ] || exit 7\ncase " $* " in *" -o "*) ;; *) echo ok ;; esac\n' "$tmp" "$tmp" > "$tmp/bin/curl"
printf '#!/bin/sh\n' > "$tmp/bin/sleep"; chmod +x "$tmp/bin/curl" "$tmp/bin/sleep"
fn=$(sed -n '/^wait_http() {/,/^}/p' "$root/ops/deploy.sh")
[[ $(PATH="$tmp/bin:$PATH" bash -euc "$fn"$'\nwait_http http://x/health') == ok ]]
echo 0 > "$tmp/n"; printf '#!/bin/sh\nexit 7\n' > "$tmp/bin/curl"
! PATH="$tmp/bin:$PATH" bash -euc "$fn"$'\nwait_http http://x/health'
echo 'Update dry-run PASS'
