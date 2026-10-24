#!/usr/bin/env bash
# Retire DeepSeek ONLY with healthy Ornith :8191/:8192 and healthy mag-120.
# Never invoke ops/deepseek/mag-llm-maint (it also stops mag-120).
# Model files are never deleted. Rollback is Qwen, not DeepSeek.
set -euo pipefail
[[ $EUID == 0 || -n ${MAG_RETIRE_TEST:-} ]] || { echo 'root required' >&2; exit 1; }
secondary_env=${MAG_SECONDARY_MODEL_ENV:-/etc/mag-stack/secondary-model.env}
health_base=${MAG_ORNITH_HEALTH_BASE:-http://127.0.0.1}
room_ready_url=${MAG_ROOM_READY_URL:-http://127.0.0.1:8766/readyz}
wants_root=${MAG_WANTS_ROOT:-/etc/systemd/system}
runtime_root=${MAG_RETIRE_RUNTIME_ROOT:-/run/systemd/system}
log() { echo "retire-deepseek: $*"; }
room_ready() {
  local data
  systemctl is-active --quiet mag-120.service || return 1
  data=$(curl --noproxy '*' -fsS --max-time 5 "$room_ready_url") || return 1
  python3 -I -c 'import json,sys
try:
    m=json.loads(sys.argv[1])["model"]
    assert m.get("state")=="ready" and m.get("ready") is True
    assert m.get("model")=="ornith-1.5-35b-a3b"
except (ValueError, KeyError, AssertionError, TypeError):
    sys.exit(1)
' "$data"
}
nodes_ready() {
  local p
  for p in 8191 8192; do
    curl --noproxy '*' -fsS -o /dev/null --max-time 5 "$health_base:$p/health" || return 1
  done
}
if [[ ! -r $secondary_env ]] || ! grep -Fxq 'MAG_SECONDARY_MODEL=ornith' "$secondary_env"; then
  log 'WARN: secondary is not Ornith; no units touched'; exit 0
fi
if ! nodes_ready || ! room_ready; then
  log 'WARN: Ornith nodes or room not healthy; no DeepSeek units touched'; exit 0
fi
# Both mag-llm and mag-model-pin have RefuseManualStop=yes and Restart=always.
# Transient /run override allows a sanctioned, explicit systemctl stop.
overrides=()
cleanup() {
  local f
  for f in "${overrides[@]}"; do rm -f -- "$f"; done
  if ((${#overrides[@]})); then systemctl daemon-reload || log 'WARN: final daemon-reload failed'; fi
}
trap cleanup EXIT
for u in mag-llm.service mag-model-pin.service; do
  if systemctl is-active --quiet "$u"; then
    dir="$runtime_root/$u.d"
    install -d -m 0755 "$dir"
    f="$dir/98-retire-deepseek.conf"
    printf '[Unit]\nRefuseManualStop=no\n' > "$f"
    overrides+=("$f")
    log "temporary RefuseManualStop=no for $u"
  fi
done
if ((${#overrides[@]})); then systemctl daemon-reload; fi
fail=0
# Stop gate first, so it cannot start Q4 while the old model is being retired.
for u in mag-model-gate.timer mag-model-gate.service mag-model-pin.service mag-llm-iq3.service mag-llm.service; do
  load=$(systemctl show -p LoadState --value "$u" 2>/dev/null || true)
  [[ $load == loaded ]] || continue
  enabled=$(systemctl is-enabled "$u" 2>/dev/null || true)
  case "$enabled" in
    enabled|enabled-runtime|linked|linked-runtime)
      if ! systemctl disable "$u"; then log "WARN: disabling $u failed"; fail=1; fi ;;
  esac
  if systemctl is-active --quiet "$u"; then
    if ! systemctl stop "$u"; then log "WARN: stopping $u failed"; fail=1; fi
  fi
  if systemctl is-active --quiet "$u"; then
    log "WARN: $u remains active"; fail=1
  else
    log "$u inactive (previous enable state: $enabled)"
  fi
  if ! systemctl is-active --quiet mag-120.service; then
    log 'WARN: room stopped unexpectedly, attempting recovery'
    systemctl start mag-120.service || { log 'ERROR: room could not restart'; exit 1; }
  fi
  if ! nodes_ready || ! room_ready; then
    log 'ERROR: room/Ornith health lost; aborting further retirement'
    exit 1
  fi
done
# Static or legacy systemd wants links are removed without touching GGUF files.
for u in mag-model-gate.timer mag-model-gate.service mag-model-pin.service mag-llm-iq3.service mag-llm.service; do
  for target in timers.target.wants multi-user.target.wants; do
    link="$wants_root/$target/$u"
    if [[ -L $link ]]; then rm -f -- "$link"; log "removed boot link $target/$u"; fi
  done
done
((fail==0)) || { log 'WARN: at least one DeepSeek unit not fully retired'; exit 1; }
log 'done: DeepSeek inactive and disabled; room healthy; GGUF preserved'
