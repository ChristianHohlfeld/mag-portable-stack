#!/usr/bin/env bash
# Root helper for the MI50's sclk ceiling. The Idol worker runs as the owner
# with NoNewPrivileges, so it cannot raise privileges itself: `prepare` (udev
# on device add, and ExecStartPre) puts the clocks back on auto and hands the
# two clock files to that user. The worker then writes them. `auto` is also
# ExecStop, for a stop whose handler did not run.
#
# This script never opens the power cap. power-cap.sh is the only writer of
# that file, and it only lowers it.
set -euo pipefail
mode=${1:-}
[[ $# -eq 1 ]] || { echo "usage: gpu-clock.sh prepare|auto|low|lowest" >&2; exit 2; }
case $mode in
  prepare|auto|low|lowest) ;;
  *) echo "usage: gpu-clock.sh prepare|auto|low|lowest" >&2; exit 2 ;;
esac

owner=chris
env_file=${MAG_HOST_ENV:-/etc/mag-stack/host.env}
if [[ -r $env_file ]]; then
  line=$(grep -E '^MAG_OWNER=' "$env_file" | head -1 || true)
  parsed=${line#MAG_OWNER=}
  parsed=${parsed//\"/}
  if [[ $parsed =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    owner=$parsed
  fi
fi

shopt -s nullglob
devs=()
if [[ -n ${MAG_GPU_SYSFS:-} ]]; then
  devs=("$MAG_GPU_SYSFS")
else
  for dev in /sys/bus/pci/devices/*; do
    [[ $(cat "$dev/vendor" 2>/dev/null) == 0x1002 && $(cat "$dev/device" 2>/dev/null) == 0x66a1 ]] || continue
    devs+=("$dev")
  done
fi

for dev in "${devs[@]}"; do
  level=$dev/power_dpm_force_performance_level
  sclk=$dev/pp_dpm_sclk
  [[ -e $level && -e $sclk ]] || continue
  case $mode in
    auto|prepare)
      printf 'auto\n' > "$level"
      if [[ $mode == prepare && $(id -u) -eq 0 ]]; then
        chown "$owner:" "$level" "$sclk"
        chmod 0640 "$level" "$sclk"
      fi
      ;;
    low)
      printf 'manual\n' > "$level"
      printf '0 1 2\n' > "$sclk"
      ;;
    lowest)
      printf 'manual\n' > "$level"
      printf '0\n' > "$sclk"
      ;;
  esac
  echo "MI50 clocks $mode"
done
exit 0
