#!/usr/bin/env bash
# One command on the MAG host: pull Gitea green, build the C++ binaries, deploy.
# Run as the normal user that owns the checkout (not as root); only the final
# deploy step uses sudo. A failed pull or build stops before anything on the
# live system is touched. deploy.sh keeps its own backup and automatic rollback.
set -euo pipefail
ROOT="$(cd -- "$(dirname "$0")/.." && pwd)"
# shellcheck source=build-targets.sh
source "$ROOT/ops/build-targets.sh"
DRY=0 PULL=1 DEPLOY=1 AUTO=1
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --no-pull) PULL=0 ;;
    --no-deploy) DEPLOY=0 ;;
    --no-auto) AUTO=0 ;;
    -h|--help) echo 'usage: ops/update.sh [--dry-run] [--no-pull] [--no-deploy] [--no-auto]'; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
run() { if (( DRY )); then echo "+ $*"; else "$@"; fi; }
phase() { echo "PHASE $1"; }
if (( ! DRY )) && [[ $EUID == 0 ]]; then
  echo 'Run as your normal user, not root (a root-owned build/ breaks later builds). sudo is used for the deploy step.' >&2
  exit 1
fi

phase pull
if (( PULL )); then
  # Gitea `green` is the newest main commit whose merge gate passed.
  # Gitea `main` is used only until that ref exists. GitHub is not fetched.
  if (( DRY )); then
    echo "+ git fetch gitea green (falls back to gitea main)"
  else
    ref="$(bash "$ROOT/ops/ci/fetch-gitea-green.sh" "$ROOT")" || { echo 'gitea green/main not reachable' >&2; exit 1; }
    run git -C "$ROOT" merge --ff-only "$ref"
  fi
else
  echo 'skipped (--no-pull)'
fi

phase build
run cmake -S "$ROOT" -B "$ROOT/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
run cmake --build "$ROOT/build" -j "$(nproc)" --target "${MAG_BUILD_TARGETS[@]}"
if (( DRY )); then
  echo '+ go build -mod=mod -o build/mag-browser-webrtc ./mag-room/webrtc'
elif [[ ! -x "$ROOT/build/mag-browser-webrtc" ]]; then
  (cd "$ROOT/mag-room/webrtc" && go build -mod=mod -o "$ROOT/build/mag-browser-webrtc" .)
fi

phase deploy
if (( DEPLOY )); then
  # sudo resets the environment. Drain/force/layout must be passed explicitly
  # or a forced deploy still waits the full MAG_DEPLOY_DRAIN_S default (900s).
  deploy_env=()
  for name in MAG_ROOM_LAYOUT MAG_DEPLOY_DRAIN_S MAG_DEPLOY_FORCE MAG_DEPLOY_STALE_S MAG_DEPLOY_DEADLINE_S; do
    if [[ -n ${!name:-} ]]; then deploy_env+=("$name=${!name}"); fi
  done
  if ((${#deploy_env[@]})); then
    run sudo env "${deploy_env[@]}" bash "$ROOT/ops/deploy.sh"
  else
    run sudo bash "$ROOT/ops/deploy.sh"
  fi
else
  echo 'skipped (--no-deploy)'
fi
phase auto-update
if (( DEPLOY && AUTO )); then
  run sudo bash "$ROOT/ops/auto-update/install.sh"
else
  echo 'skipped'
fi
phase runners
if (( DEPLOY && AUTO )); then
  run sudo bash "$ROOT/ops/runners/install.sh" || echo 'runner install failed (deploy itself succeeded)' >&2
else
  echo 'skipped'
fi
phase boot
if (( DEPLOY )); then
  run sudo bash "$ROOT/ops/boot/ensure-boot.sh" || echo 'boot persistence check failed (deploy itself succeeded)' >&2
  run sudo bash "$ROOT/ops/boot/clear-failed-units.sh" || echo 'WARN: a stack unit is still failed' >&2
else
  echo 'skipped'
fi
echo 'update done'

