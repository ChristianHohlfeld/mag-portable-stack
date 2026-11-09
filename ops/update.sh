#!/usr/bin/env bash
# One command on the MAG host: pull Gitea green, build the C++ binaries, deploy.
# Run as the normal user that owns the checkout (not as root); only the final
# deploy step uses sudo. A failed pull or build stops before anything on the
# live system is touched. deploy.sh keeps its own backup and automatic rollback.
set -euo pipefail
# Everything this script starts (build, stage.py, checksums) stays off the
# model cores 0-27; systemd-restarted services do not inherit the affinity.
if [[ -z ${MAG_BUILD_PINNED:-} ]] && command -v ionice >/dev/null && taskset -c "${MAG_BUILD_CPUS:-28-55}" true 2>/dev/null; then
  MAG_BUILD_PINNED=1 exec taskset -c "${MAG_BUILD_CPUS:-28-55}" nice -n 19 ionice -c3 bash "$0" "$@"
fi
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
  # One source: GitHub's last promoted green commit. Never fall back to main.
  if (( DRY )); then
    echo "+ fetch GitHub green (no fallback to Gitea or unverified main)"
  else
    ref="$(bash "$ROOT/ops/ci/fetch-github-green.sh" "$ROOT")" || {
      echo 'GitHub green unavailable; no changes made' >&2
      exit 1
    }
    run git -C "$ROOT" merge --ff-only "$ref"
  fi
else
  echo 'skipped (--no-pull)'
fi

phase build
run cmake -S "$ROOT" -B "$ROOT/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
# Model cores 0-27 (NUMA0 0-13, NUMA1 14-27) run the lockstep llama threads;
# a compile there stalled decode (live 17:52: cc1plus at 100% on core 14).
# The whole script re-execs under taskset 28-55 / nice 19 / ionice -c3 (top).
run nice -n 19 cmake --build "$ROOT/build" -j 8 --target "${MAG_BUILD_TARGETS[@]}"
if (( DRY )); then
  echo '+ go build -mod=mod -o build/mag-browser-webrtc ./mag-room/webrtc'
elif [[ ! -x "$ROOT/build/mag-browser-webrtc" ]]; then
  (cd "$ROOT/mag-room/webrtc" && go build -mod=mod -o "$ROOT/build/mag-browser-webrtc" .)
fi

phase deploy
deploy_rc=0
if (( DEPLOY )); then
  # Room deploys are immediate by policy; active bot completions are cancelled.
  # Keep explicit layout forwarding; no drain, force or exit-75 postpone.
  deploy_env=()
  for name in MAG_ROOM_LAYOUT MAG_DEPLOY_DEADLINE_S MAG_ROOM_STATE; do
    if [[ -n ${!name:-} ]]; then deploy_env+=("$name=${!name}"); fi
  done
  if ((${#deploy_env[@]})); then
    run sudo env "${deploy_env[@]}" bash "$ROOT/ops/deploy.sh" || deploy_rc=$?
  else
    run sudo bash "$ROOT/ops/deploy.sh" || deploy_rc=$?
  fi
  if (( deploy_rc )); then
    echo "deploy aborted (rc=$deploy_rc)" >&2
    exit "$deploy_rc"
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
else
  echo 'skipped'
fi
echo 'update done'

