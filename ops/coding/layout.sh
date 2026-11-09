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
# Operator-disabled Noah is an explicit opt-out, not an unhealthy deployment.
# Never stage/hash his huge GGUF or start/change his systemd unit in this mode.
noah_enabled=1
if ! systemctl is-enabled --quiet mag-llm-coding@1.service; then
  noah_enabled=0
  echo 'coding layout: Noah NUMA1 disabled by operator; Lena-only deploy'
fi
new=(mag-llm-coding@0.service)
if (( noah_enabled )); then new+=(mag-llm-coding@1.service); fi
old=(mag-llm-mistral-room.service mag-llm-glm@q2.service
     mag-llm-qwen-room.service mag-llm-qwen.service mag-llm-glm@q1.service
     mag-llm-ds-node@0.service mag-llm-ds-node@1.service)
files=(/etc/mag-stack/room-model.env /etc/mag-stack/room-layout /etc/mag-stack/room-layout.pinned
       /etc/systemd/system/mag-llm-coding@.service
       /etc/systemd/system/mag-llm-coding@1.service.d/10-numa-reviewer.conf
       /usr/local/lib/mag-coding/start-live.sh /usr/local/lib/mag-coding/runtime.sha
       /usr/local/lib/mag-coding/flint-chat.jinja /usr/local/lib/mag-coding/ornith-chat.jinja /usr/local/lib/mag-coding/deepseek-v4-flash-chat.jinja
       /usr/local/lib/mag-coding/runtime-coder.sha
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
  # A coding->coding NUMA1-only swap must never stop or restart Lena.
  if [[ -f "$TXN/coding-swap-nodes" ]]; then
    while IFS= read -r node; do
      [[ $node == 0 || $node == 1 ]] || continue
      systemctl stop "mag-llm-coding@$node.service" || true
    done < "$TXN/coding-swap-nodes"
    for f in "${files[@]}"; do restore_file "$f"; done
    systemctl daemon-reload
    while IFS= read -r node; do
      [[ $node == 0 || $node == 1 ]] || continue
      systemctl start "mag-llm-coding@$node.service" || fail "failed to restore NUMA$node"
    done < "$TXN/coding-swap-nodes"
    touch "$TXN/restored"
    return 0
  fi
  # Old-layout migration rollback: restore both old services.
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
identity="coding $(cat "$ROOT/ops/coding/models.json" "$ROOT/ops/coding/layout.sh" "$ROOT/ops/coding/start-live.sh" "$ROOT/ops/coding/flint-chat.jinja" "$ROOT/ops/coding/ornith-chat.jinja" "$ROOT/ops/coding/deepseek-v4-flash-chat.jinja" "$ROOT/ops/coding/prepare.sh" "$ROOT/ops/coding/mag-llm-coding@.service" "$ROOT/ops/room/coding.env" "$ROOT/ops/room/coding0.env" | sha256sum | cut -c1-16)"
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
coder_ready=0
if (( noah_enabled )); then
  coder_ready=1
  python3 -I "$ROOT/ops/coding/stage.py" --model coder || coder_ready=0
fi
target_env="$ROOT/ops/room/coding.env"
if (( noah_enabled && ! coder_ready )); then target_env="$ROOT/ops/room/coding0.env"; fi
interim=0
if [[ $previous == coding ]] && cmp -s "$ROOT/ops/room/coding0.env" /etc/mag-stack/room-model.env; then
  interim=1
fi
# Node-scoped model change on an already full coding layout: only a node whose
# models.json entry differs from the installed manifest is restarted (e.g.
# NUMA1 Qwen3-Coder-Next -> Flint while NUMA0 Qwen3.8 stays resident). The
# snapshot below restores the previous model on failure.
swap_nodes=()
if [[ $previous == coding ]] && (( ! interim )) && [[ -f /usr/local/lib/mag-coding/models.json ]]; then
  mapfile -t swap_nodes < <(python3 -I - "$ROOT/ops/coding/models.json" /usr/local/lib/mag-coding/models.json "$noah_enabled" <<'PY'
import json, sys
new = json.load(open(sys.argv[1]))["models"]; old = json.load(open(sys.argv[2]))["models"]
for name in ("general", "coder"):
    # Tests without a third parameter still validate the two-node comparison.
    if name == "coder" and len(sys.argv) > 3 and sys.argv[3] == "0":
        continue
    if new[name] != old.get(name):
        print(new[name]["numa"])
PY
  )
  if (( ${#swap_nodes[@]} )) && [[ " ${swap_nodes[*]} " == *" 1 "* ]]; then
    (( coder_ready )) || fail 'new NUMA1 coder GGUF not staged; current pair left running'
  fi
fi
swap=$(( ${#swap_nodes[@]} > 0 ))
if [[ $previous == coding ]] && (( noah_enabled && interim && ! coder_ready )); then
  systemctl is-active --quiet mag-llm-coding@0.service || fail 'coding NUMA0 not active'
  wait_ready mag-llm-coding@0.service 8191 6 || fail 'coding NUMA0 unhealthy'
  cmp -s "$ROOT/ops/coding/start-live.sh" /usr/local/lib/mag-coding/start-live.sh || fail 'coding launcher drift'
  cmp -s "$ROOT/ops/coding/flint-chat.jinja" /usr/local/lib/mag-coding/flint-chat.jinja || fail 'coding chat template drift'
  cmp -s "$ROOT/ops/coding/ornith-chat.jinja" /usr/local/lib/mag-coding/ornith-chat.jinja || fail 'coding chat template drift'
  cmp -s "$ROOT/ops/coding/deepseek-v4-flash-chat.jinja" /usr/local/lib/mag-coding/deepseek-v4-flash-chat.jinja || fail 'coding chat template drift'
  cmp -s "$ROOT/ops/coding/mag-llm-coding@.service" /etc/systemd/system/mag-llm-coding@.service || fail 'coding unit drift'
  echo 'coding layout unchanged: Qwen3.8 NUMA0 + GLM-Q2 NUMA1 (Qwen3-Coder-Next not staged yet)'
  exit 0
fi
if [[ $previous == coding ]] && (( ! interim && ! swap )); then
  # No undeclared, restart-requiring drift is silently applied on ordinary deploy.
  check_nodes=(0)
  if (( noah_enabled )); then check_nodes+=(1); fi
  for i in "${check_nodes[@]}"; do
    systemctl is-active --quiet "mag-llm-coding@$i.service" || fail "coding NUMA$i not active"
    wait_ready "mag-llm-coding@$i.service" "$((8191+i))" 6 || fail "coding NUMA$i unhealthy"
  done
  cmp -s "$target_env" /etc/mag-stack/room-model.env || fail 'coding env drift'
  cmp -s "$ROOT/ops/coding/start-live.sh" /usr/local/lib/mag-coding/start-live.sh || fail 'coding launcher drift'
  cmp -s "$ROOT/ops/coding/flint-chat.jinja" /usr/local/lib/mag-coding/flint-chat.jinja || fail 'coding chat template drift'
  cmp -s "$ROOT/ops/coding/ornith-chat.jinja" /usr/local/lib/mag-coding/ornith-chat.jinja || fail 'coding chat template drift'
  cmp -s "$ROOT/ops/coding/deepseek-v4-flash-chat.jinja" /usr/local/lib/mag-coding/deepseek-v4-flash-chat.jinja || fail 'coding chat template drift'
  cmp -s "$ROOT/ops/coding/mag-llm-coding@.service" /etc/systemd/system/mag-llm-coding@.service || fail 'coding unit drift'
  cmp -s "$ROOT/ops/coding/80-coding-output.conf" /etc/systemd/system/mag-120.service.d/80-coding-output.conf || fail 'coding output budget drift'
  if (( noah_enabled )); then echo 'coding layout unchanged; both resident models healthy'
  else echo 'coding layout unchanged; Lena NUMA0 healthy, disabled Noah deliberately skipped'; fi
  exit 0
fi
# All heavyweight preparation (stage.py above) is done with the old services still resident.
REV=$(tr -d '[:space:]' < "$ROOT/ops/qwen/runtime.sha")
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
[[ -x $BIN && $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) == "$REV" ]] ||
    fail 'pinned coding runtime missing'
if (( noah_enabled )); then
  CREV=$(tr -d '[:space:]' < "$ROOT/ops/coding/runtime.sha")
  CBIN="/home/chris/.local/mag-coding-rt/$CREV/bin/llama-server"
  [[ -x $CBIN && $(cat "$(dirname "$CBIN")/source-commit" 2>/dev/null || true) == "$CREV" ]] ||
      fail 'pinned NUMA1 (flint) runtime missing: run ops/coding/build-runtime.sh'
fi
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
if [[ $previous == coding && $swap == 1 ]]; then
  printf '%s\n' "${swap_nodes[@]}" > "$TXN/coding-swap-nodes"
fi
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
install -m 0644 "$ROOT/ops/coding/flint-chat.jinja" /usr/local/lib/mag-coding/flint-chat.jinja
install -m 0644 "$ROOT/ops/coding/ornith-chat.jinja" /usr/local/lib/mag-coding/ornith-chat.jinja
install -m 0644 "$ROOT/ops/coding/deepseek-v4-flash-chat.jinja" /usr/local/lib/mag-coding/deepseek-v4-flash-chat.jinja
install -m 0755 "$ROOT/ops/coding/save-slots.sh" /usr/local/lib/mag-coding/save-slots.sh
install -m 0644 "$ROOT/ops/coding/stage.py" /usr/local/lib/mag-coding/stage.py
install -m 0644 "$ROOT/ops/coding/models.json" /usr/local/lib/mag-coding/models.json
install -m 0644 "$ROOT/ops/qwen/runtime.sha" /usr/local/lib/mag-coding/runtime.sha
install -m 0644 "$ROOT/ops/coding/runtime.sha" /usr/local/lib/mag-coding/runtime-coder.sha
install -m 0644 "$ROOT/ops/coding/mag-llm-coding@.service" /etc/systemd/system/mag-llm-coding@.service
install -D -m 0644 "$ROOT/ops/coding/mag-llm-coding@1.service.d/10-numa-reviewer.conf" /etc/systemd/system/mag-llm-coding@1.service.d/10-numa-reviewer.conf
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
  python3 -I "$ROOT/ops/coding/live-smoke.py" --port 8191
fi
if (( swap )); then
  touch "$TXN/switch-started"
  for i in "${swap_nodes[@]}"; do
    port=$((8191 + i))
    # Persist KV + checkpoints of the running model first (idle slot, then save).
    bash "$ROOT/ops/coding/save-slots.sh" "$port" || true
    systemctl stop "mag-llm-coding@$i.service"
    wait_port_free "$port" || fail "port $port still held after the old coding model stopped"
    # The saved prefix KV was computed by the previous weights/template; the
    # room would restore it into the new model after the down edge. Drop it
    # (slot cache only, never a GGUF) so the new model prefills its own.
    slot_name=$( (( i == 0 )) && echo general || echo coder )
    find "/var/lib/mag-llm/slots/coding/$slot_name" -maxdepth 1 -type f -name 'mag-prefix-*.bin' -delete
    systemctl start "mag-llm-coding@$i.service"
    wait_ready "mag-llm-coding@$i.service" "$port" 1440 || fail "new coding model failed health on $port"
    # Noah switches immediately after readiness; the only human live check is "Hi beide".
    # Keep the decoder smoke for future NUMA0 changes, not for this NUMA1 swap.
    if (( i == 0 )); then python3 -I "$ROOT/ops/coding/live-smoke.py" --port "$port"; fi
  done
fi
if (( noah_enabled && coder_ready )) && ! systemctl is-active --quiet mag-llm-coding@1.service; then
  touch "$TXN/switch-started"
  # NUMA1: GLM-Q2 -> Qwen3-Coder-Next (GGUF kept; rollback restarts GLM-Q2).
  systemctl stop mag-llm-glm@q2.service
  wait_port_free 8192 || fail 'port 8192 still held after old GLM stopped'
  systemctl start mag-llm-coding@1.service
  wait_ready mag-llm-coding@1.service 8192 1440 || fail 'Qwen3-Coder-Next failed health on 8192'
  python3 -I "$ROOT/ops/coding/live-smoke.py" --port 8192
fi
systemctl is-active --quiet mag-llm-coding@0.service || fail 'coding NUMA0 not active'
wait_ready mag-llm-coding@0.service 8191 6 || fail 'coding NUMA0 not healthy'
if (( noah_enabled )); then
  if (( coder_ready )); then
    systemctl is-active --quiet mag-llm-coding@1.service || fail 'coding NUMA1 not active'
    wait_ready mag-llm-coding@1.service 8192 6 || fail 'coding NUMA1 not healthy'
  else
    systemctl is-active --quiet mag-llm-glm@q2.service || fail 'GLM-Q2 must stay active on NUMA1'
  fi
fi
install -m 0644 "$target_env" /etc/mag-stack/room-model.env
printf 'coding\n' > /etc/mag-stack/room-layout
# The old host pin must not silently revert coding to GLM next timer tick.
if [[ $(tr -d '[:space:]' < /etc/mag-stack/room-layout.pinned 2>/dev/null || true) == glm ]]; then
  rm -f /etc/mag-stack/room-layout.pinned
fi
for unit in "${old[@]}"; do
  # Interim: GLM-Q2 keeps its boot state while it still serves NUMA1.
  if (( ! noah_enabled || ! coder_ready )) && [[ $unit == mag-llm-glm@q2.service ]]; then continue; fi
  systemctl disable "$unit" >/dev/null 2>&1 || true
done
systemctl enable mag-llm-coding@0.service
if (( noah_enabled && coder_ready )); then systemctl enable mag-llm-coding@1.service; fi
touch "$TXN/coding"
rm -f "$FAILED"
if (( ! noah_enabled )); then
  echo 'coding layout ready: Lena NUMA0 :8191; Noah disabled by operator, NUMA1 untouched'
elif (( coder_ready )); then
  echo 'coding layout ready: Qwen3.8-Flash-Next NUMA0 + Flint NUMA1, 16k tokens per round with continuation'
else
  echo 'coding layout ready (interim): Qwen3.8-Flash-Next NUMA0 + GLM-Q2 NUMA1; NUMA1 switches once Qwen3-Coder-Next is verified'
fi
