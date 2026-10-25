#!/usr/bin/env bash
# No root, no live services. The mocks enforce RefuseManualStop and track state.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/run" "$tmp/wants/timers.target.wants" "$tmp/wants/multi-user.target.wants"
log="$tmp/calls"; active="$tmp/active"; enabled="$tmp/enabled"; room="$tmp/room"
cat > "$tmp/bin/systemctl" <<'MOCK'
#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
cmd=$1; unit=${!#}
case "$cmd" in
  show) echo loaded ;;
  is-active)
    if [[ $unit == mag-120.service ]]; then [[ -e $ROOM_FILE ]]; exit; fi
    grep -Fxq "$unit" "$ACTIVE_FILE" ;;
  is-enabled) if grep -Fxq "$unit" "$ENABLED_FILE"; then echo enabled; else echo disabled; fi ;;
  disable) sed -i "/^$unit$/d" "$ENABLED_FILE" ;;
  daemon-reload) : ;;
  start) [[ $unit == mag-120.service ]] && touch "$ROOM_FILE" ;;
  stop)
    if [[ $unit == mag-llm.service || $unit == mag-model-pin.service ]]; then
      [[ -f "$RUNTIME_ROOT/$unit.d/98-retire-deepseek.conf" ]] || exit 11
      grep -Fxq 'RefuseManualStop=no' "$RUNTIME_ROOT/$unit.d/98-retire-deepseek.conf" || exit 12
    fi
    sed -i "/^$unit$/d" "$ACTIVE_FILE"
    if [[ ${CASCADE_ROOM:-0} == 1 && $unit == mag-llm.service ]]; then rm -f "$ROOM_FILE"; fi ;;
esac
exit 0
MOCK
cat > "$tmp/bin/curl" <<'MOCK'
#!/usr/bin/env bash
for a; do
  case "$a" in
    *:8191/health) [[ $H0 == 1 ]]; exit ;;
    *:8192/health) [[ $H1 == 1 ]]; exit ;;
    */readyz)
      if [[ $ROOM_OK == 1 ]]; then
        echo '{"model":{"state":"ready","ready":true,"model":"ornith-1.5-35b-a3b"}}'
      else
        echo '{"model":{"state":"ready","ready":true,"model":"deepseek-v4-flash-0731"}}'
      fi
      exit ;;
  esac
done
exit 7
MOCK
chmod +x "$tmp/bin/"*
echo MAG_SECONDARY_MODEL=ornith > "$tmp/ornith.env"
echo MAG_SECONDARY_MODEL=qwen > "$tmp/qwen.env"
live="mag-model-gate.timer mag-model-gate.service mag-model-pin.service mag-llm-iq3.service mag-llm.service"
reset() {
  : > "$log"; : > "$active"; : > "$enabled"; touch "$room"
  for u in $live; do echo "$u" >> "$active"; echo "$u" >> "$enabled"; done
}
run() {
  local envfile=$1 h0=$2 h1=$3 room_ok=$4 cascade=${5:-0}
  CALLS="$log" ACTIVE_FILE="$active" ENABLED_FILE="$enabled" ROOM_FILE="$room" \
  RUNTIME_ROOT="$tmp/run" H0="$h0" H1="$h1" ROOM_OK="$room_ok" CASCADE_ROOM="$cascade" \
  MAG_RETIRE_TEST=1 MAG_SECONDARY_MODEL_ENV="$envfile" MAG_WANTS_ROOT="$tmp/wants" \
  MAG_RETIRE_RUNTIME_ROOT="$tmp/run" PATH="$tmp/bin:$PATH" \
    bash "$root/ops/models/retire-deepseek.sh"
}
bash -n "$root/ops/models/retire-deepseek.sh"
# Health and config are strict prerequisites: no unit stopped if either fails.
for args in "$tmp/ornith.env 1 0 1" "$tmp/ornith.env 1 1 0" "$tmp/qwen.env 1 1 1"; do
  reset
  # deliberate word splitting in this test
  out=$(run $args)
  grep -Fq WARN <<<"$out"
  ! grep -q 'systemctl stop ' "$log"
done
reset
ln -s /dev/null "$tmp/wants/multi-user.target.wants/mag-llm.service"
ln -s /dev/null "$tmp/wants/timers.target.wants/mag-model-gate.timer"
out=$(run "$tmp/ornith.env" 1 1 1)
for u in $live; do
  grep -Fxq "systemctl stop $u" "$log" || { echo "never stopped $u" >&2; exit 1; }
  grep -Fxq "systemctl disable $u" "$log" || { echo "never disabled $u" >&2; exit 1; }
done
grep -Fxq 'systemctl daemon-reload' "$log"
! grep -q 'systemctl stop mag-120.service' "$log"
[[ ! -L "$tmp/wants/multi-user.target.wants/mag-llm.service" ]]
[[ ! -L "$tmp/wants/timers.target.wants/mag-model-gate.timer" ]]
[[ ! -e "$tmp/run/mag-llm.service.d/98-retire-deepseek.conf" ]]
[[ ! -e "$tmp/run/mag-model-pin.service.d/98-retire-deepseek.conf" ]]
[[ -e "$room" ]]
# No enabled or active units -> idempotent.
: > "$log"
run "$tmp/ornith.env" 1 1 1 >/dev/null
! grep -q 'systemctl stop ' "$log"
! grep -q 'systemctl disable ' "$log"
# Unexpected room stop -> recover and verify before returning.
reset
: > "$log"
run "$tmp/ornith.env" 1 1 1 1 >/dev/null
grep -Fxq 'systemctl start mag-120.service' "$log"
[[ -e "$room" ]]
# Production wiring and no unintended model-file deletion.
d="$root/ops/deploy.sh"
r=$(grep -n '^bash .*retire-deepseek.sh' "$d" | head -1 | cut -d: -f1)
t=$(grep -n '^trap - EXIT INT TERM' "$d" | cut -d: -f1)
b=$(grep -n 'ops/boot/ensure-boot.sh"' "$d" | cut -d: -f1)
(( t < r && r < b ))
! grep -q 'retire-v41.sh' "$d"
grep -Fq '20-room-audit.conf' "$d"
! grep -q '^After=.*mag-llm.service' "$root/ops/mag-120.service"
echo 'Retire DeepSeek PASS'
