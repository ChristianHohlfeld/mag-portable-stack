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
files=(/etc/mag-stack/room-model.env /etc/mag-stack/room-layout /etc/mag-stack/room-layout.pinned
       /etc/systemd/system/mag-llm-coding@.service
       /usr/local/lib/mag-coding/start-live.sh /usr/local/lib/mag-coding/runtime.sha
       /usr/local/lib/mag-coding/stage.py /usr/local/lib/mag-coding/models.json
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
  # Restore and fully load GLM first: Mistral refuses to start without Q2.
  if grep -Fxq mag-llm-glm@q2.service "$TXN/active"; then
    systemctl start mag-llm-glm@q2.service || fail 'old Q2 service start failed'
    wait_ready mag-llm-glm@q2.service 8192 5040 || fail 'GLM-Q2 rollback not ready'
  fi
  for unit in "${old[@]}"; do
    [[ $unit != mag-llm-glm@q2.service ]] || continue
    if grep -Fxq "$unit" "$TXN/active"; then
      systemctl start "$unit" || fail "cannot restore $unit"
    fi
  done
  if grep -Fxq mag-llm-mistral-room.service "$TXN/active"; then
    wait_ready mag-llm-mistral-room.service 8191 5040 || fail 'Mistral rollback not ready'
  elif grep -Fxq mag-llm-qwen-room.service "$TXN/active"; then
    wait_ready mag-llm-qwen-room.service 8191 5040 || fail 'Qwen rollback not ready'
  fi
  # Node-scoped upgrade (interim -> full): the already-live NUMA0 bot returns too.
  for unit in "${new[@]}"; do
    if grep -Fxq "$unit" "$TXN/active"; then
      systemctl enable "$unit" || true
      systemctl start "$unit" || fail "cannot restore $unit"
    fi
  done
  touch "$TXN/restored"
  echo 'coding layout rollback complete; old model services restored'
}
if [[ $ACTION == rollback ]]; then rollback; exit 0; fi
[[ $ACTION == probe || $ACTION == apply ]] || fail 'unknown action'
selected="$(bash "$ROOT/ops/coding/desired.sh")"
[[ $selected == coding ]] || fail "coding helper invoked for $selected"
identity="coding $(cat "$ROOT/ops/coding/models.json" "$ROOT/ops/coding/layout.sh" "$ROOT/ops/coding/start-live.sh" "$ROOT/ops/coding/prepare.sh" "$ROOT/ops/coding/mag-llm-coding@.service" "$ROOT/ops/room/coding.env" "$ROOT/ops/room/coding0.env" | sha256sum | cut -c1-16)"
if [[ $(cat "$FAILED" 2>/dev/null || true) == "$identity" ]]; then
  echo 'coding layout: a prior live cutover failed; refusing unattended retry' >&2
  [[ $ACTION != probe ]] || exit 78
  fail 'blocked failed identity'
fi
[[ $ACTION != probe ]] || exit 0
[[ -f /etc/mag-stack/room-model.env ]] || fail 'existing MAG room required'
previous=$(cat /etc/mag-stack/room-layout 2>/dev/null || true)
[[ $previous == glm || $previous == coding ]] || fail "unsupported automatic migration from '$previous'"
# Node-scoped: NUMA0 switches as soon as Qwen3.8 is verified; NUMA1 keeps
# GLM-Q2 (ops/room/coding0.env) until Qwen3-Coder-Next is staged and verified.
python3 -I "$ROOT/ops/coding/stage.py" --model general || fail 'Qwen3.8 GGUF not verified'
coder_ready=1
python3 -I "$ROOT/ops/coding/stage.py" --model coder || coder_ready=0
target_env="$ROOT/ops/room/coding.env"
(( coder_ready )) || target_env="$ROOT/ops/room/coding0.env"
interim=0
if [[ $previous == coding ]] && cmp -s "$ROOT/ops/room/coding0.env" /etc/mag-stack/room-model.env; then
  interim=1
fi
if [[ $previous == coding ]] && (( interim && ! coder_ready )); then
  systemctl is-active --quiet mag-llm-coding@0.service || fail 'coding NUMA0 not active'
  wait_ready mag-llm-coding@0.service 8191 6 || fail 'coding NUMA0 unhealthy'
  cmp -s "$ROOT/ops/coding/start-live.sh" /usr/local/lib/mag-coding/start-live.sh || fail 'coding launcher drift'
  cmp -s "$ROOT/ops/coding/mag-llm-coding@.service" /etc/systemd/system/mag-llm-coding@.service || fail 'coding unit drift'
  echo 'coding layout unchanged: Qwen3.8 NUMA0 + GLM-Q2 NUMA1 (Qwen3-Coder-Next not staged yet)'
  exit 0
fi
if [[ $previous == coding ]] && (( ! interim )); then
  # No undeclared, restart-requiring drift is silently applied on ordinary deploy.
  for i in 0 1; do
    systemctl is-active --quiet "mag-llm-coding@$i.service" || fail "coding NUMA$i not active"
    wait_ready "mag-llm-coding@$i.service" "$((8191+i))" 6 || fail "coding NUMA$i unhealthy"
  done
  cmp -s "$ROOT/ops/room/coding.env" /etc/mag-stack/room-model.env || fail 'coding env drift'
  cmp -s "$ROOT/ops/coding/start-live.sh" /usr/local/lib/mag-coding/start-live.sh || fail 'coding launcher drift'
  cmp -s "$ROOT/ops/coding/mag-llm-coding@.service" /etc/systemd/system/mag-llm-coding@.service || fail 'coding unit drift'
  cmp -s "$ROOT/ops/coding/80-coding-output.conf" /etc/systemd/system/mag-120.service.d/80-coding-output.conf || fail 'coding output budget drift'
  echo 'coding layout unchanged; both resident models healthy'
  exit 0
fi
# All heavyweight preparation (stage.py above) is done with the old services still resident.
REV=$(tr -d '[:space:]' < "$ROOT/ops/qwen/runtime.sha")
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
[[ -x $BIN && $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) == "$REV" ]] ||
    fail 'pinned coding runtime missing'
# Do not make recovery from a broken old backend impossible. It is precisely
# why the owner requested replacement. Snapshot the old active/enabled units for
# rollback, but require *new* model health and real decoder output before routing.
if [[ $previous == glm ]]; then
  for port in 8191 8192; do
    if ! curl --noproxy '*' -fsS --max-time 5 -o /dev/null "http://127.0.0.1:$port/health" 2>/dev/null; then
      echo "coding layout: old :$port is unhealthy; continuing with staged replacement" >&2
    fi
  done
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
# The needle also occurs in this heredoc: rewrite only the real line 4.
assert s.index(needle) < s.index("python3 - ")
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
    elif [[ -f "$TXN/started" ]]; then
      # Restore staged files without stopping or waiting on the still-live old models.
      for f in "${files[@]}"; do restore_file "$f"; done
      systemctl daemon-reload
      touch "$TXN/restored"
    fi
  fi
  exit "$rc"
}
trap on_exit EXIT
install -d -m 0755 /etc/mag-stack /usr/local/lib/mag-coding /etc/systemd/system/mag-120.service.d
install -m 0755 "$ROOT/ops/coding/start-live.sh" /usr/local/lib/mag-coding/start-live.sh
install -m 0644 "$ROOT/ops/coding/stage.py" /usr/local/lib/mag-coding/stage.py
install -m 0644 "$ROOT/ops/coding/models.json" /usr/local/lib/mag-coding/models.json
install -m 0644 "$ROOT/ops/qwen/runtime.sha" /usr/local/lib/mag-coding/runtime.sha
install -m 0644 "$ROOT/ops/coding/mag-llm-coding@.service" /etc/systemd/system/mag-llm-coding@.service
install -m 0644 "$ROOT/ops/coding/80-coding-output.conf" /etc/systemd/system/mag-120.service.d/80-coding-output.conf
for name in general coder; do
  install -d -o chris -g chris -m 0750 "/var/lib/mag-llm/slots/coding/$name"
done
systemctl daemon-reload
# Write-ahead marker ensures any failure rolls back the running services.
if [[ $previous == glm ]]; then
  touch "$TXN/switch-started"
  # NUMA0 first: Mistral -> Qwen3.8. GLM-Q2 is left running unless NUMA1 switches too.
  systemctl stop mag-llm-mistral-room.service
  wait_port_free 8191 || fail 'port 8191 still held after old Mistral stopped'
  systemctl start mag-llm-coding@0.service
  wait_ready mag-llm-coding@0.service 8191 1440 || fail 'Qwen3.8-Flash-Next failed health on 8191'
  # Test the actual GGUF decoder, not merely an HTTP listening socket.
  python3 -I "$ROOT/ops/coding/live-smoke.py" --port 8191 --model qwen3.8-flash-next
fi
if (( coder_ready )) && ! systemctl is-active --quiet mag-llm-coding@1.service; then
  touch "$TXN/switch-started"
  # NUMA1: GLM-Q2 -> Qwen3-Coder-Next (GGUF kept; rollback restarts GLM-Q2).
  systemctl stop mag-llm-glm@q2.service
  wait_port_free 8192 || fail 'port 8192 still held after old GLM stopped'
  systemctl start mag-llm-coding@1.service
  wait_ready mag-llm-coding@1.service 8192 1440 || fail 'Qwen3-Coder-Next failed health on 8192'
  python3 -I "$ROOT/ops/coding/live-smoke.py" --port 8192 --model qwen3-coder-next
fi
systemctl is-active --quiet mag-llm-coding@0.service || fail 'coding NUMA0 not active'
wait_ready mag-llm-coding@0.service 8191 6 || fail 'coding NUMA0 not healthy'
if (( coder_ready )); then
  systemctl is-active --quiet mag-llm-coding@1.service || fail 'coding NUMA1 not active'
  wait_ready mag-llm-coding@1.service 8192 6 || fail 'coding NUMA1 not healthy'
else
  systemctl is-active --quiet mag-llm-glm@q2.service || fail 'GLM-Q2 must stay active on NUMA1'
fi
install -m 0644 "$target_env" /etc/mag-stack/room-model.env
printf 'coding\n' > /etc/mag-stack/room-layout
# The old host pin must not silently revert coding to GLM next timer tick.
if [[ $(tr -d '[:space:]' < /etc/mag-stack/room-layout.pinned 2>/dev/null || true) == glm ]]; then
  rm -f /etc/mag-stack/room-layout.pinned
fi
for unit in "${old[@]}"; do
  # Interim: GLM-Q2 keeps its boot state while it still serves NUMA1.
  if (( ! coder_ready )) && [[ $unit == mag-llm-glm@q2.service ]]; then continue; fi
  systemctl disable "$unit" >/dev/null 2>&1 || true
done
systemctl enable mag-llm-coding@0.service
if (( coder_ready )); then systemctl enable mag-llm-coding@1.service; fi
touch "$TXN/coding"
rm -f "$FAILED"
if (( coder_ready )); then
  echo 'coding layout ready: Qwen3.8-Flash-Next NUMA0 + Qwen3-Coder-Next NUMA1, 16k tokens per round with continuation'
else
  echo 'coding layout ready (interim): Qwen3.8-Flash-Next NUMA0 + GLM-Q2 NUMA1; NUMA1 switches once Qwen3-Coder-Next is verified'
fi
