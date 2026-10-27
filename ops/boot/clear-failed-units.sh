#!/usr/bin/env bash
# Make deploy/update leave systemctl --failed without these leftovers.
# hotspot-net-setup runs only when its USB wifi device exists.
# The NVIDIA units run only when the driver device exists.
# On the mixed Qwen stack, mag-llm-ds-node@1 is the retired DeepSeek instance
# on NUMA 1. This script disables, masks, and reset-failed that instance only.
# A dual-DeepSeek host unmasks @1. It never starts, stops, restarts, reloads,
# disables, or masks mag-llm-ds-node@0.
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
hotspot_dev=${MAG_HOTSPOT_DEV:-/sys/class/net/wlx2023516593f0}
nvidia_dev=${MAG_NVIDIA_DEV:-/dev/nvidiactl}

install_dropin() {
  local unit=$1 condition=$2 dir
  mag_systemctl cat "$unit" >/dev/null 2>&1 || return 0
  dir="$dropin_root/${unit}.d"
  mkdir -p "$dir"
  printf '[Unit]\nConditionPathExists=%s\n' "$condition" > "$dir/mag-condition.conf"
  echo "condition $unit $condition"
}

# Mask only when the device is gone. Unmask when it returns so the
# ConditionPathExists drop-in can start the unit again.
gate_device_unit() {
  local unit=$1 condition=$2
  mag_systemctl cat "$unit" >/dev/null 2>&1 || return 0
  install_dropin "$unit" "$condition"
  if [[ -e $condition ]]; then
    mag_systemctl unmask "$unit" >/dev/null 2>&1 || true
    echo "device present $unit"
  else
    mag_systemctl disable "$unit" >/dev/null 2>&1 || true
    mag_systemctl mask "$unit" >/dev/null 2>&1 || true
    echo "device missing $unit"
  fi
}

# Exact instance. A glob of mag-llm-ds-node@* would include the live NUMA 0 server.
# Default is uninstall (disable, mask, drop the wants link, reset-failed).
# Dual-DeepSeek keeps that instance: MAG_ROOM_LAYOUT=deepseek or ID_1=deepseek.
# Qwen on NUMA 1 always uninstalls it.
retired=mag-llm-ds-node@1.service
room_env=${MAG_ROOM_MODEL_ENV:-/etc/mag-stack/room-model.env}
layout_file=${MAG_ROOM_LAYOUT_FILE:-/etc/mag-stack/room-layout}
unit_root=${MAG_SYSTEMD_UNIT_ROOT:-/etc/systemd/system}
keep_ds1=0
if [[ -r $layout_file && $(tr -d '[:space:]' <"$layout_file") == deepseek ]]; then
  keep_ds1=1
fi
if [[ -r $room_env ]] && grep -Eq '^MAG_ROOM_MODEL_ID_1=deepseek' "$room_env"; then
  keep_ds1=1
fi
if [[ -r $room_env ]] && grep -Fxq 'MAG_ROOM_MODEL_ID_1=qwen3.8-flash-next-uncensored' "$room_env"; then
  keep_ds1=0
fi
if (( keep_ds1 )); then
  mag_systemctl unmask "$retired" >/dev/null 2>&1 || true
else
  mag_systemctl disable "$retired" >/dev/null 2>&1 || true
  mag_systemctl mask "$retired" >/dev/null 2>&1 || true
  wants=$unit_root/multi-user.target.wants/$retired
  if [[ -L $wants || -f $wants ]]; then
    rm -f -- "$wants" || true
  fi
  mag_systemctl reset-failed "$retired" >/dev/null 2>&1 || true
  echo "retired $retired"
fi

gate_device_unit hotspot-net-setup.service "$hotspot_dev"
gate_device_unit nvidia-persistenced.service "$nvidia_dev"
gate_device_unit set-gpu-compute-mode.service "$nvidia_dev"
mag_systemctl daemon-reload
mag_systemctl reset-failed hotspot-net-setup.service >/dev/null 2>&1 || true
mag_systemctl reset-failed nvidia-persistenced.service >/dev/null 2>&1 || true
mag_systemctl reset-failed set-gpu-compute-mode.service >/dev/null 2>&1 || true
bash "$ROOT/ops/boot/check-failed-units.sh"
