#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# No root, no live services. The mock records every systemctl verb and
# refuses any stop/restart of mag-llm-ds-node@0.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
log="$tmp/calls"
active="$tmp/active"
enabled="$tmp/enabled"
failed="$tmp/failed"
loaded="$tmp/loaded"
cat > "$tmp/bin/systemctl" <<'MOCK'
#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
cmd=$1; unit=${!#}
case "$cmd $2" in
  "show -p")
    if grep -Fxq "$unit" "$LOADED_FILE" 2>/dev/null; then echo loaded; else echo not-found; fi
    exit 0 ;;
esac
case "$cmd" in
  is-active) grep -Fxq "$unit" "$ACTIVE_FILE"; exit ;;
  is-enabled)
    if grep -Fxq "$unit" "$ENABLED_FILE"; then echo enabled; exit 0; fi
    echo disabled; exit 1 ;;
  disable) sed -i "/^$unit$/d" "$ENABLED_FILE" ;;
  reset-failed) sed -i "/^$unit$/d" "$FAILED_FILE" ;;
  stop|restart|reload|try-restart|kill)
    echo "forbidden verb $cmd $unit" >&2
    exit 9 ;;
esac
exit 0
MOCK
chmod +x "$tmp/bin/systemctl"
run() {
  : > "$log"
  CALLS="$log" ACTIVE_FILE="$active" ENABLED_FILE="$enabled" \
  FAILED_FILE="$failed" LOADED_FILE="$loaded" \
  MAG_CLEAR_FAILED_TEST=1 PATH="$tmp/bin:$PATH" \
    bash "$root/ops/boot/clear-failed-units.sh"
}
reset() {
  printf '%s\n' "$@" > "$loaded"
  : > "$active"; : > "$enabled"; : > "$failed"
}

bash -n "$root/ops/boot/clear-failed-units.sh"
# The live NUMA0 node must never appear on a systemctl command line.
! grep -E 'systemctl[[:space:]]+(stop|restart|reload|try-restart|kill|disable|enable|reset-failed)[[:space:]].*mag-llm-ds-node@0' \
  "$root/ops/boot/clear-failed-units.sh" "$root/ops/deploy.sh" "$root/ops/update.sh"

# Failed retired @1 + leftover host units: disable + reset-failed, no stop.
reset mag-llm-ds-node@1.service hotspot-net-setup.service \
  nvidia-persistenced.service set-gpu-compute-mode.service
printf '%s\n' mag-llm-ds-node@1.service hotspot-net-setup.service \
  nvidia-persistenced.service set-gpu-compute-mode.service > "$enabled"
printf '%s\n' mag-llm-ds-node@1.service hotspot-net-setup.service \
  nvidia-persistenced.service set-gpu-compute-mode.service > "$failed"
out=$(run)
grep -Fq 'mag-llm-ds-node@1.service disabled and reset-failed' <<<"$out"
grep -Fq 'hotspot-net-setup.service disabled and reset-failed' <<<"$out"
grep -Fq 'nvidia-persistenced.service disabled and reset-failed' <<<"$out"
grep -Fq 'set-gpu-compute-mode.service disabled and reset-failed' <<<"$out"
! grep -q mag-llm-ds-node@1.service "$enabled"
! grep -q mag-llm-ds-node@1.service "$failed"
! grep -q hotspot-net-setup.service "$enabled"
! grep -E 'systemctl (stop|restart|reload) ' "$log"
! grep -F 'mag-llm-ds-node@0' "$log"

# A live deepseek-layout @1 is left running (no disable --now / stop).
reset mag-llm-ds-node@1.service
echo mag-llm-ds-node@1.service > "$active"
echo mag-llm-ds-node@1.service > "$enabled"
out=$(run)
grep -Fq 'left running' <<<"$out"
grep -Fxq mag-llm-ds-node@1.service "$enabled"
! grep -E 'systemctl (stop|disable|restart) mag-llm-ds-node@1' "$log"

# Active leftover host unit is not disabled.
reset nvidia-persistenced.service
echo nvidia-persistenced.service > "$active"
echo nvidia-persistenced.service > "$enabled"
out=$(run)
grep -Fq 'nvidia-persistenced.service is active; left running' <<<"$out"
grep -Fxq nvidia-persistenced.service "$enabled"

grep -Fq 'clear-failed-units.sh' "$root/ops/deploy.sh"
echo 'clear-failed-units PASS'
