#!/usr/bin/env bash
# Retire DeepSeek (owner decision 2026-10-23): disable+stop mag-llm (DeepSeek-V4-Flash
# Q4 :8090), mag-llm-iq3, mag-model-gate.timer and mag-model-pin. Idempotent, root,
# called by deploy.sh AFTER the room is healthy. Only on an Ornith host with both
# node servers (8191/8192) healthy; otherwise WARN and skip so the room never loses
# its model. Never restarts mag-120, never deletes model files (~/models/deepseek-v4
# stays; only the owner deletes it). Rollback target is Qwen (ops/ornith/rollback.sh qwen).
set -euo pipefail
[[ $EUID == 0 || -n ${MAG_RETIRE_TEST:-} ]] || { echo 'root required' >&2; exit 1; }
# Overridable for tests only.
secondary_env=${MAG_SECONDARY_MODEL_ENV:-/etc/mag-stack/secondary-model.env}
health_base=${MAG_ORNITH_HEALTH_BASE:-http://127.0.0.1}
wants_dir=${MAG_WANTS_DIR:-/etc/systemd/system/multi-user.target.wants}
log() { echo "retire-deepseek: $*"; }
if ! [[ -r $secondary_env ]] || ! grep -Fxq 'MAG_SECONDARY_MODEL=ornith' "$secondary_env"; then
  log 'WARN: not an Ornith host; DeepSeek left as is'
  exit 0
fi
for p in 8191 8192; do
  if ! curl --noproxy '*' -fsS -o /dev/null --max-time 5 "$health_base:$p/health"; then
    log "WARN: Ornith :$p not healthy; DeepSeek left as is (room must keep a model)"
    exit 0
  fi
done
for u in mag-model-gate.timer mag-model-gate.service mag-model-pin.service mag-llm-iq3.service mag-llm.service; do
  load=$(systemctl show -p LoadState --value "$u" 2>/dev/null || true)
  [[ $load == loaded ]] || continue
  active=$(systemctl is-active "$u" 2>/dev/null || true)
  enabled=$(systemctl is-enabled "$u" 2>/dev/null || true)
  if [[ $active == inactive && $enabled != enabled ]]; then continue; fi
  systemctl disable --now "$u" >/dev/null 2>&1 || log "WARN: could not disable $u"
  log "disabled+stopped $u (was $active/$enabled)"
done
# Static units (no [Install]) can still be pulled in by a manual wants link.
for u in mag-llm-iq3.service mag-llm.service mag-model-pin.service; do
  link=$wants_dir/$u
  if [[ -L $link ]]; then rm -f -- "$link"; log "removed boot link $u"; fi
done
log 'done (model files kept)'
