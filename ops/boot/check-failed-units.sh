#!/usr/bin/env bash
# Fail when a stack unit is still listed by systemctl --failed.
# Read-only. It does not start, stop, restart, or reload anything.
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091 # deploy-guard.sh is passed to shellcheck on its own
source "$ROOT/ops/deploy-guard.sh"
units=(
  mag-llm-ds-node@1.service
  hotspot-net-setup.service
  nvidia-persistenced.service
  set-gpu-compute-mode.service
  mag-120.service
  mag-llm-ds-node@0.service
  mag-llm-qwen.service
  gitea.service
  mag-gitea-runner.service
  tailscaled.service
)
failed=$(mag_systemctl --failed --plain --no-legend || true)
bad=0
for unit in "${units[@]}"; do
  if grep -Fq -- "$unit" <<<"$failed"; then
    echo "stack unit failed: $unit" >&2
    bad=1
  fi
done
if (( bad )); then
  exit 1
fi
echo 'stack units: none failed'
