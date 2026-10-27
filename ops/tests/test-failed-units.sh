#!/usr/bin/env bash
# Deploy clears leftover failed units and does not touch the live DeepSeek node.
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
bash -n "$root/ops/boot/clear-failed-units.sh" "$root/ops/boot/check-failed-units.sh"
# The live NUMA 0 server is not an argument of disable, mask, stop, restart, or reset-failed.
if grep -E 'systemctl (disable|mask|unmask|stop|restart|reload|reset-failed)[^#]*ds-node@0' "$root/ops/boot/clear-failed-units.sh"; then exit 1; fi
if grep -E 'systemctl (disable|mask|unmask|stop|restart|reload)[^#]*ds-node@0' "$root/ops/boot/check-failed-units.sh"; then exit 1; fi
grep -Fq 'mag-llm-ds-node@1.service' "$root/ops/boot/clear-failed-units.sh"
grep -Fq '/sys/class/net/wlx2023516593f0' "$root/ops/boot/clear-failed-units.sh"
grep -Fq '/dev/nvidiactl' "$root/ops/boot/clear-failed-units.sh"
grep -Fq 'ops/boot/clear-failed-units.sh' "$root/ops/update.sh"
grep -Fq 'ops/boot/clear-failed-units.sh' "$root/ops/deploy.sh"
grep -Fq 'ops/boot/clear-failed-units.sh' "$root/ops/auto-update/apply.sh"
# update.sh stays free of model-unit names; the helper owns that.
if grep -Eq 'mag-llm' "$root/ops/update.sh"; then exit 1; fi
if grep -Eq 'systemctl (restart|stop|reload).*mag-llm' "$root/ops/deploy.sh"; then exit 1; fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if bash "$root/ops/boot/clear-failed-units.sh" >"$tmp/root.out" 2>"$tmp/root.err"; then
  echo 'non-root cleanup was accepted' >&2
  exit 1
fi
grep -Fq 'root required' "$tmp/root.err"
cat > "$tmp/systemctl" <<'S'
#!/usr/bin/env bash
printf 'systemctl %s\n' "$*" >> "$CALLS"
if [[ $1 == --failed ]]; then
  if [[ ${MAG_FAKE_FAILED_RC:-0} != 0 ]]; then
    echo 'Failed to connect to bus: Host is down' >&2
    exit "$MAG_FAKE_FAILED_RC"
  fi
  printf '%s\n' "${MAG_FAKE_FAILED:-}"
  exit 0
fi
if [[ $1 == cat ]]; then
  case $2 in
    mag-llm-ds-node@1.service|hotspot-net-setup.service|nvidia-persistenced.service|set-gpu-compute-mode.service) exit 0 ;;
  esac
  exit 1
fi
exit 0
S
chmod +x "$tmp/systemctl"
printf '%s\n' 'MAG_ROOM_MODEL_ID_1=qwen3.8-flash-next-uncensored' > "$tmp/qwen.env"
printf '%s\n' 'MAG_ROOM_MODEL_ID_1=deepseek-v4-flash-0731' > "$tmp/deepseek.env"
calls=$tmp/calls
: > "$calls"
CALLS="$calls" MAG_FAILED_UNITS_TEST=1 MAG_SYSTEMD_DROPIN_ROOT="$tmp/drop" \
  MAG_ROOM_MODEL_ENV="$tmp/qwen.env" \
  MAG_SYSTEMD_QUERY_S=5 MAG_SYSTEMD_BUDGET_S=5 \
  PATH="$tmp:$PATH" bash "$root/ops/boot/clear-failed-units.sh" >"$tmp/out"
grep -Fxq 'systemctl disable mag-llm-ds-node@1.service' "$calls"
grep -Fxq 'systemctl mask mag-llm-ds-node@1.service' "$calls"
grep -Fxq 'systemctl reset-failed mag-llm-ds-node@1.service' "$calls"
grep -Fxq 'systemctl disable hotspot-net-setup.service' "$calls"
grep -Fxq 'systemctl mask hotspot-net-setup.service' "$calls"
grep -Fxq 'systemctl disable nvidia-persistenced.service' "$calls"
grep -Fxq 'systemctl mask nvidia-persistenced.service' "$calls"
grep -Fxq 'systemctl disable set-gpu-compute-mode.service' "$calls"
grep -Fxq 'systemctl mask set-gpu-compute-mode.service' "$calls"
grep -Fxq 'systemctl daemon-reload' "$calls"
if grep -E 'systemctl (disable|mask|unmask|stop|restart|reload|reset-failed) mag-llm-ds-node@0' "$calls"; then
  echo 'cleanup touched ds-node@0' >&2
  exit 1
fi
grep -Fxq 'ConditionPathExists=/sys/class/net/wlx2023516593f0' "$tmp/drop/hotspot-net-setup.service.d/mag-condition.conf"
grep -Fxq 'ConditionPathExists=/dev/nvidiactl' "$tmp/drop/nvidia-persistenced.service.d/mag-condition.conf"
grep -Fxq 'ConditionPathExists=/dev/nvidiactl' "$tmp/drop/set-gpu-compute-mode.service.d/mag-condition.conf"
grep -Fq 'device missing hotspot-net-setup.service' "$tmp/out"
grep -Fq 'stack units: none failed' "$tmp/out"

: > "$calls"
mkdir -p "$tmp/present/net" "$tmp/present/nvidia"
: > "$tmp/present/net/wlx2023516593f0"
: > "$tmp/present/nvidia/nvidiactl"
CALLS="$calls" MAG_FAILED_UNITS_TEST=1 MAG_SYSTEMD_DROPIN_ROOT="$tmp/drop-present" \
  MAG_ROOM_MODEL_ENV="$tmp/qwen.env" \
  MAG_HOTSPOT_DEV="$tmp/present/net/wlx2023516593f0" \
  MAG_NVIDIA_DEV="$tmp/present/nvidia/nvidiactl" \
  MAG_SYSTEMD_QUERY_S=5 MAG_SYSTEMD_BUDGET_S=5 \
  PATH="$tmp:$PATH" bash "$root/ops/boot/clear-failed-units.sh" >"$tmp/present.out"
grep -Fxq 'systemctl unmask hotspot-net-setup.service' "$calls"
grep -Fxq 'systemctl unmask nvidia-persistenced.service' "$calls"
grep -Fxq 'systemctl unmask set-gpu-compute-mode.service' "$calls"
if grep -E 'systemctl (disable|mask) (hotspot-net-setup|nvidia-persistenced|set-gpu-compute-mode)' "$calls"; then
  echo 'present devices were masked' >&2
  exit 1
fi
grep -Fq 'device present hotspot-net-setup.service' "$tmp/present.out"
if grep -E 'systemctl (disable|mask|unmask|stop|restart|reload|reset-failed) mag-llm-ds-node@0' "$calls"; then
  echo 'present-device cleanup touched ds-node@0' >&2
  exit 1
fi

: > "$calls"
CALLS="$calls" MAG_FAILED_UNITS_TEST=1 MAG_SYSTEMD_DROPIN_ROOT="$tmp/drop2" \
  MAG_ROOM_MODEL_ENV="$tmp/deepseek.env" \
  MAG_SYSTEMD_QUERY_S=5 MAG_SYSTEMD_BUDGET_S=5 \
  PATH="$tmp:$PATH" bash "$root/ops/boot/clear-failed-units.sh" >"$tmp/ds.out"
grep -Fxq 'systemctl unmask mag-llm-ds-node@1.service' "$calls"
if grep -E 'systemctl (disable|mask|reset-failed) mag-llm-ds-node@1' "$calls"; then
  echo 'dual-DeepSeek layout retired NUMA1' >&2
  exit 1
fi
grep -Fq 'stack units: none failed' "$tmp/ds.out"

: > "$calls"
if CALLS="$calls" MAG_FAKE_FAILED='hotspot-net-setup.service loaded failed failed' \
  PATH="$tmp:$PATH" bash "$root/ops/boot/check-failed-units.sh" >"$tmp/ok" 2>"$tmp/err"; then
  echo 'failed hotspot was accepted' >&2
  exit 1
fi
grep -Fq 'stack unit failed: hotspot-net-setup.service' "$tmp/err"

if CALLS="$calls" MAG_FAKE_FAILED_RC=1 \
  MAG_SYSTEMD_QUERY_S=5 MAG_SYSTEMD_BUDGET_S=5 PATH="$tmp:$PATH" \
  bash "$root/ops/boot/check-failed-units.sh" >"$tmp/bus.out" 2>"$tmp/bus.err"; then
  echo 'unreachable systemd was accepted' >&2
  exit 1
fi
grep -Fq 'could not query systemctl --failed' "$tmp/bus.err"
echo 'failed units PASS'
