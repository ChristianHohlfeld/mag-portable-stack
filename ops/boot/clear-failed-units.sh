#!/usr/bin/env bash
# Make deploy/update leave systemctl --failed without these leftovers.
# hotspot-net-setup runs only when its USB wifi device exists.
# The NVIDIA units run only when the driver device exists.
# On the mixed Qwen stack, mag-llm-ds-node@1 is the retired DeepSeek instance
# on NUMA 1. This script disables and reset-failed that instance only. A
# dual-DeepSeek host keeps @1. It never starts, stops, restarts, or reloads
# mag-llm-ds-node@0.
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091 # deploy-guard.sh is passed to shellcheck on its own
source "$ROOT/ops/deploy-guard.sh"
if [[ ${MAG_FAILED_UNITS_TEST:-} != 1 ]]; then
  [[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
fi
dropin_root=${MAG_SYSTEMD_DROPIN_ROOT:-/etc/systemd/system}

install_dropin() {
  local unit=$1 condition=$2 dir
  mag_systemctl cat "$unit" >/dev/null 2>&1 || return 0
  dir="$dropin_root/${unit}.d"
  mkdir -p "$dir"
  printf '[Unit]\nConditionPathExists=%s\n' "$condition" > "$dir/mag-condition.conf"
  echo "condition $unit $condition"
}

# Exact instance. A glob of mag-llm-ds-node@* would include the live NUMA 0 server.
# Dual-DeepSeek (MAG_ROOM_LAYOUT=deepseek) still boots mag-llm-ds-node@1; only
# the mixed Qwen stack retires that instance. Same gate as ensure-boot.sh.
retired=mag-llm-ds-node@1.service
room_env=${MAG_ROOM_MODEL_ENV:-/etc/mag-stack/room-model.env}
if [[ -r $room_env ]] && grep -Fxq 'MAG_ROOM_MODEL_ID_1=qwen3.8-flash-next-uncensored' "$room_env"; then
  mag_systemctl disable "$retired" >/dev/null 2>&1 || true
  mag_systemctl reset-failed "$retired" >/dev/null 2>&1 || true
  echo "retired $retired"
fi

install_dropin hotspot-net-setup.service /sys/class/net/wlx2023516593f0
install_dropin nvidia-persistenced.service /dev/nvidiactl
install_dropin set-gpu-compute-mode.service /dev/nvidiactl
mag_systemctl daemon-reload
mag_systemctl reset-failed hotspot-net-setup.service >/dev/null 2>&1 || true
mag_systemctl reset-failed nvidia-persistenced.service >/dev/null 2>&1 || true
mag_systemctl reset-failed set-gpu-compute-mode.service >/dev/null 2>&1 || true
bash "$ROOT/ops/boot/check-failed-units.sh"
