#!/usr/bin/env bash
# One command on the MAG host: pull the newest green main, build the C++ binaries, deploy.
# Run as the normal user that owns the checkout (not as root); only the final
# deploy step uses sudo. A failed pull or build stops before anything on the
# live system is touched. deploy.sh keeps its own backup and automatic rollback.
set -euo pipefail
ROOT="$(cd -- "$(dirname "$0")/.." && pwd)"
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
  # `green` = newest main commit with all CI checks passed; main only until it exists.
  ref=origin/green
  if (( DRY )); then
    echo "+ git -C $ROOT fetch origin green (falls back to main)"
  elif ! git -C "$ROOT" fetch origin green 2>/dev/null; then
    git -C "$ROOT" fetch origin main
    ref=origin/main
  fi
  run git -C "$ROOT" merge --ff-only "$ref"
else
  echo 'skipped (--no-pull)'
fi

phase build
run cmake -S "$ROOT" -B "$ROOT/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
run cmake --build "$ROOT/build" -j "$(nproc)" --target magroom mag-audit-gateway mag-browser magbot-native
if (( DRY )); then
  echo '+ go build -mod=mod -o build/mag-browser-webrtc ./mag-room/webrtc'
elif [[ ! -x "$ROOT/build/mag-browser-webrtc" ]]; then
  (cd "$ROOT/mag-room/webrtc" && go build -mod=mod -o "$ROOT/build/mag-browser-webrtc" .)
fi

phase deploy
if (( DEPLOY )); then
  run sudo bash "$ROOT/ops/deploy.sh"
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
