#!/usr/bin/env bash
# Transactional coding-layout switch, invoked by the existing MAG auto-deploy.
# Weight download and compilation MUST finish before this can stop old services.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ACTION=${1:?apply|probe|rollback}; BACKUP=${2:?transaction backup directory}
TXN="$BACKUP/qwen-layout"
FAILED=${MAG_LAYOUT_FAILED_FILE:-/var/lib/mag-stack/layout-failed}
fail(){ echo "coding layout: $*" >&2; exit 4; }
[[ $EUID == 0 ]] || fail 'root required'
new=(mag-llm-coding@0.service mag-llm-coding@1.service)
old=(mag-llm-mistral-room.service mag-llm-glm@q2.service
     mag-llm-qwen-room.service mag-llm-qwen.service mag-llm-glm@q1.service
     mag-llm-ds-node@0.service mag-llm-ds-node@1.service)
files=(/etc/mag-stack/room-model.env /etc/mag-stack/room-layout
       /etc/systemd/system/mag-llm-coding@.service
       /usr/local/lib/mag-coding/start-live.sh /usr/local/lib/mag-coding/runtime.sha
       /etc/systemd/system/mag-120.service.d/80-coding-output.conf)
wait_port_free(){
  local port=$1 i
  for ((i=0;i<90;i++)); do
    [[ -z $(ss -Htan "( sport = :$port )" 2>/dev/null) ]] && return 0
    sleep 2
  done
  return 1
}
wait_ready(){
  local unit=$1 port=$2 limit=$3 i
  for ((i=0;i<limit;i++)); do
    systemctl is-active --quiet "$unit" || return 1
    if curl --noproxy '*' -fsS --max-time 3 -o /dev/null "http://127.0.0.1:$port/health" 2>/dev/null; then return 0; fi
    sleep 5
  done
  return 1
}
restore_file(){
  local file=$1
  if [[ -f "$TXN/files$file" ]]; then
    install -D -m "$(stat -c %a "$TXN/files$file")" "$TXN/files$file" "$file"
  else
    rm -f -- "$file"
  fi
}
rollback(){
  [[ -f "$TXN/switch-started" && ! -e "$TXN/restored" ]] || return 0
  # Stop newly started coding services, and restore the old model configuration.
  for unit in "${new[@]}"; do
    systemctl stop "$unit" || true
    systemctl disable "$unit" || true
  done
  for f in "${files[@]}"; do restore_file "$f"; done
  systemctl daemon-reload
  for unit in "${old[@]}"; do
    if grep -Fxq "$unit" "$TXN/enabled"; then systemctl enable "$unit" || true; fi
  done
  # Old q2 ik backend needs both its port and TIME_WAIT sockets to be clear.
  for port in 8191 8192; do wait_port_free "$port" || fail "rollback port $port still held"; done
  for unit in "${old[@]}"; do
    if grep -Fxq "$unit" "$TXN/active"; then
      systemctl start "$unit" || fail "cannot restore $unit"
    fi
  done
  # Long cold starts are accepted, but never report rollback success prematurely.
  if grep -Fxq mag-llm-glm@q2.service "$TXN/active"; then
    wait_ready mag-llm-glm@q2.service 8192 5040 || fail 'GLM-Q2 rollback not ready'
  fi
  if grep -Fxq mag-llm-mistral-room.service "$TXN/active"; then
    wait_ready mag-llm-mistral-room.service 8191 5040 || fail 'Mistral rollback not ready'
  elif grep -Fxq mag-llm-qwen-room.service "$TXN/active"; then
    wait_ready mag-llm-qwen-room.service 8191 5040 || fail 'Qwen rollback not ready'
  fi
  touch "$TXN/restored"
  echo 'coding layout rollback complete; old model services restored'
}
if [[ $ACTION == rollback ]]; then rollback; exit 0; fi
[[ $ACTION == probe || $ACTION == apply ]] || fail 'unknown action'
selected="$(bash "$ROOT/ops/coding/desired.sh")"
[[ $selected == coding ]] || fail "coding helper invoked for $selected"
identity="coding $(cat "$ROOT/ops/coding/models.json" "$ROOT/ops/coding/layout.sh" "$ROOT/ops/coding/start-live.sh" "$ROOT/ops/coding/prepare.sh" "$ROOT/ops/coding/mag-llm-coding@.service" "$ROOT/ops/room/coding.env" | sha256sum | cut -c1-16)"
if [[ $(cat "$FAILED" 2>/dev/null || true) == "$identity" ]]; then
  echo 'coding layout: a prior live cutover failed; refusing unattended retry' >&2
  [[ $ACTION != probe ]] || exit 78
  fail 'blocked failed identity'
fi
[[ $ACTION != probe ]] || exit 0
[[ -f /etc/mag-stack/room-model.env ]] || fail 'existing MAG room required'
previous=$(cat /etc/mag-stack/room-layout 2>/dev/null || true)
[[ $previous == glm || $previous == coding ]] || fail "unsupported automatic migration from '$previous'"
# All heavyweight preparation is done with the old services still resident.
python3 -I "$ROOT/ops/coding/stage.py" --model both || fail 'models not verified'
REV=$(tr -d '[:space:]' < "$ROOT/ops/mistral/runtime.sha")
BIN="/home/chris/.local/mag-coding/$REV/bin/llama-server"
[[ -x $BIN && $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) == "$REV" ]] ||
    fail 'pinned coding runtime missing'
# Existing model pair must be healthy before a fresh switchover.
if [[ $previous == glm ]]; then
  systemctl is-active --quiet mag-llm-mistral-room.service || fail 'old Mistral not running'
  systemctl is-active --quiet mag-llm-glm@q2.service || fail 'old GLM-Q2 not running'
  curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8191/health >/dev/null ||
    fail 'old NUMA0 unhealthy'
  curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8192/health >/dev/null ||
    fail 'old NUMA1 unhealthy'
fi
install -d -m 0700 "$TXN"
[[ ! -f $TXN/started ]] || fail 'transaction reused'
: > "$TXN/active"; : > "$TXN/enabled"
for unit in "${old[@]}" "${new[@]}"; do
  if systemctl is-active --quiet "$unit"; then echo "$unit" >> "$TXN/active"; fi
  if systemctl is-enabled --quiet "$unit"; then echo "$unit" >> "$TXN/enabled"; fi
done
for f in "${files[@]}"; do
  if [[ -f $f ]]; then install -D -m "$(stat -c %a "$f")" "$f" "$TXN/files$f"; fi
done
# A rollback must work even after the layout source is replaced by a new release.
install -m 0700 "$0" "$TXN/helper.sh"
python3 - "$TXN/helper.sh" "$ROOT" <<'PY'
import pathlib,sys
p=pathlib.Path(sys.argv[1]); s=p.read_text()
needle='ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"'
assert s.count(needle)==1
p.write_text(s.replace(needle,"ROOT="+repr(sys.argv[2]),1))
PY
touch "$TXN/started"
on_exit(){
  local rc=$?
  trap - EXIT
  if (( rc )); then
    if [[ -f "$TXN/switch-started" ]]; then
      install -d -m 0755 "$(dirname "$FAILED")"
      printf '%s\n' "$identity" > "$FAILED"
      rollback || echo 'coding: rollback FAILED; manual recovery required' >&2
    fi
  fi
  exit "$rc"
}
trap on_exit EXIT
install -d -m 0755 /etc/mag-stack /usr/local/lib/mag-coding /etc/systemd/system/mag-120.service.d
install -m 0755 "$ROOT/ops/coding/start-live.sh" /usr/local/lib/mag-coding/start-live.sh
install -m 0644 "$ROOT/ops/mistral/runtime.sha" /usr/local/lib/mag-coding/runtime.sha
install -m 0644 "$ROOT/ops/coding/mag-llm-coding@.service" /etc/systemd/system/mag-llm-coding@.service
install -m 0644 "$ROOT/ops/coding/80-coding-output.conf" /etc/systemd/system/mag-120.service.d/80-coding-output.conf
for name in general coder; do
  install -d -o chris -g chris -m 0750 "/var/lib/mag-llm/slots/coding/$name"
done
systemctl daemon-reload
if [[ $previous == glm ]]; then
  # Write-ahead marker ensures any failure rolls back the running services.
  touch "$TXN/switch-started"
  systemctl stop mag-llm-glm@q2.service
  systemctl stop mag-llm-mistral-room.service
  wait_port_free 8191 || fail 'port 8191 still held after old Mistral stopped'
  wait_port_free 8192 || fail 'port 8192 still held after old GLM stopped'
  systemctl start mag-llm-coding@0.service
  wait_ready mag-llm-coding@0.service 8191 360 || fail 'Qwen3.6 failed health on 8191'
  systemctl start mag-llm-coding@1.service
  wait_ready mag-llm-coding@1.service 8192 360 || fail 'Qwen3-Coder failed health on 8192'
  # Test BOTH actual GGUF decoders, not merely an HTTP listening socket.
  python3 -I "$ROOT/ops/coding/live-smoke.py" --port 8191 --model qwen3.6-35b-a3b
  python3 -I "$ROOT/ops/coding/live-smoke.py" --port 8192 --model qwen3-coder-30b-a3b-instruct
else
  for i in 0 1; do
    systemctl is-active --quiet "mag-llm-coding@$i.service" || fail "coding NUMA$i not active"
    wait_ready "mag-llm-coding@$i.service" "$((8191+i))" 6 ||
      fail "coding NUMA$i not healthy"
  done
fi
install -m 0644 "$ROOT/ops/room/coding.env" /etc/mag-stack/room-model.env
printf 'coding\n' > /etc/mag-stack/room-layout
for unit in "${old[@]}"; do
  systemctl disable "$unit" >/dev/null 2>&1 || true
done
for unit in "${new[@]}"; do systemctl enable "$unit"; done
touch "$TXN/coding"
rm -f "$FAILED"
echo 'coding layout ready: Qwen3.6 NUMA0 + Qwen3-Coder NUMA1, 16k coding budget'
