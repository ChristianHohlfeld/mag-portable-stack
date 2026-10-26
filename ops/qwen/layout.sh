#!/usr/bin/env bash
# Transactional NUMA1 switch used by deploy.sh. NUMA0 is never stopped/restarted.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
ACTION=${1:?apply|rollback}; BACKUP=${2:?deployment backup directory}
TXN="$BACKUP/qwen-layout"
fail(){ echo "NUMA1 layout: $*" >&2; exit 4; }
[[ $EUID == 0 ]] || fail 'root required'
units=(mag-llm-ds-node@1.service mag-llm-qwen.service)
files=(/etc/mag-stack/room-model.env /etc/mag-stack/room-layout /etc/mag-qwen/runtime.env
       /etc/systemd/system/mag-llm-qwen.service /etc/systemd/system/mag-llm-ds-node@.service
       /usr/local/lib/mag-qwen/start.sh /usr/local/lib/mag-qwen/verify-model.sh
       /usr/local/lib/mag-qwen/runtime.sha /etc/mag-qwen/model.sha256)
disable_if_installed(){
  # A pure-DeepSeek host may never have had a Qwen unit file.
  if systemctl cat "$1" >/dev/null 2>&1; then systemctl disable "$1"; fi
}
rollback(){
  [[ -f $TXN/started && ! -f $TXN/restored ]] || return 0
  # Stop only an instance this transaction changed. Never touch ds-node@0.
  if [[ -f $TXN/changed ]]; then systemctl stop "${units[@]}"; fi
  for unit in "${units[@]}"; do disable_if_installed "$unit"; done
  for file in "${files[@]}"; do
    if [[ -f $TXN/files$file ]]; then
      install -D -m "$(stat -c %a "$TXN/files$file")" "$TXN/files$file" "$file"
    else rm -f "$file"; fi
  done
  systemctl daemon-reload
  for unit in "${units[@]}"; do
    if grep -Fxq "$unit" "$TXN/enabled"; then systemctl enable "$unit"; fi
    if [[ -f $TXN/changed ]] && grep -Fxq "$unit" "$TXN/active"; then systemctl start "$unit"; fi
  done
  touch "$TXN/restored"
}
if [[ $ACTION == rollback ]]; then rollback; exit; fi
[[ $ACTION == apply ]] || fail 'unknown action'
# A rollback selection persists across subsequent automatic updates.
layout=${MAG_ROOM_LAYOUT:-$(cat /etc/mag-stack/room-layout 2>/dev/null || echo mixed)}
[[ $layout == mixed || $layout == deepseek ]] || fail 'MAG_ROOM_LAYOUT must be mixed or deepseek'
# Fresh bootstrap has no Qwen download. Keep its existing DeepSeek provisioning.
[[ -f /etc/mag-stack/room-model.env ]] || exit 0
if [[ $layout == mixed ]]; then
  REV=$(cat "$ROOT/ops/qwen/runtime.sha")
  BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
  if [[ ! -x $BIN || $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) != "$REV" ]]; then
    runuser -u chris -- env HOME=/home/chris nice -n 19 ionice -c3 bash "$ROOT/ops/qwen/build-runtime.sh"
  fi
  [[ -s /home/chris/models/qwen3.8-flash-next-uncensored/upstream.sha256 ]] || fail 'download/upstream.sha256 missing'
fi
install -d -m 0700 "$TXN"
[[ ! -e $TXN/started ]] || fail 'transaction already exists'
: > "$TXN/active"; : > "$TXN/enabled"
for unit in "${units[@]}"; do
  if systemctl is-active --quiet "$unit"; then echo "$unit" >> "$TXN/active"; fi
  if systemctl is-enabled --quiet "$unit"; then echo "$unit" >> "$TXN/enabled"; fi
done
for file in "${files[@]}"; do
  if [[ -f $file ]]; then install -D -m "$(stat -c %a "$file")" "$file" "$TXN/files$file"; fi
done
install -m 0700 "$0" "$TXN/helper.sh"
touch "$TXN/started"
trap 'rc=$?; trap - EXIT; if (( rc )); then rollback; fi; exit "$rc"' EXIT
# Qwen stage verifies all 98 GB (or its root-owned identity stamp) BEFORE stopping ds1.
if [[ $layout == mixed ]]; then bash "$ROOT/ops/qwen/install.sh" refresh; fi
# Remove the obsolete global Qwen conflict before starting it; reload changes
# systemd's dependency graph, not the running DeepSeek process on NUMA0.
install -m 0644 "$ROOT/ops/room/mag-llm-ds-node@.service" /etc/systemd/system/mag-llm-ds-node@.service
install -d -m 0755 /etc/mag-stack /etc/mag-qwen
if [[ $layout == mixed ]]; then
  selected=mag-llm-qwen.service; other=mag-llm-ds-node@1.service; config=mixed.env
  printf 'QWEN_PORT=8192\n' > /etc/mag-qwen/runtime.env
else
  selected=mag-llm-ds-node@1.service; other=mag-llm-qwen.service; config=model.env
fi
# Existing warm Qwen on :8192 stays resident on ordinary redeploys.
change=0
if ! systemctl is-active --quiet "$selected"; then change=1; fi
if [[ $layout == mixed ]]; then
  for file in /usr/local/lib/mag-qwen/start.sh /etc/systemd/system/mag-llm-qwen.service /etc/mag-qwen/runtime.env; do
    cmp -s "$file" "$TXN/files$file" || change=1
  done
fi
install -m 0644 "$ROOT/ops/room/$config" /etc/mag-stack/room-model.env
printf '%s\n' "$layout" > /etc/mag-stack/room-layout
systemctl daemon-reload
if systemctl is-active --quiet "$other"; then change=1; fi
if (( change )); then
  touch "$TXN/changed"
  if systemctl is-active --quiet "$other"; then systemctl stop "$other"; fi
  # A previous :8091 Qwen or changed runtime may need one NUMA1 restart.
  if systemctl is-active --quiet "$selected"; then systemctl stop "$selected"; fi
fi
disable_if_installed "$other"
systemctl enable "$selected"
if (( change )); then
  systemctl start "$selected"
  # Model loading is minutes on CPU. Each poll remains short and bounded.
  ready=0
  for ((i=0; i<1080; i++)); do
    systemctl is-active --quiet "$selected" || fail "$selected exited during load"
    if curl --noproxy '*' -fsS --max-time 3 -o /dev/null http://127.0.0.1:8192/health; then ready=1; break; fi
    sleep 5
  done
  (( ready )) || fail 'NUMA1 health timeout'
  if [[ $layout == mixed ]]; then
    python3 -I "$ROOT/ops/qwen/smoke.py" --port 8192
    # Acceptance used a tiny synthetic prompt. Clear only this newly started
    # Qwen slot so the room can asynchronously prewarm its real system prefix.
    # This is never done to an already-warm Qwen on ordinary redeploys.
    curl --noproxy '*' -fsS --max-time 30 -H 'Content-Type: application/json' \
      -d '{}' 'http://127.0.0.1:8192/slots/0?action=erase' >/dev/null
  fi
fi
echo "NUMA1 layout ready: $layout; NUMA0 resident process untouched"
