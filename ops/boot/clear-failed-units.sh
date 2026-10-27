#!/usr/bin/env bash
# Clear leftover failed systemd units after a deploy. Never starts, stops,
# restarts or reloads a model. mag-llm-ds-node@0 (NUMA0, :8191) is never
# named in a systemctl verb other than a comment.
#   - mag-llm-ds-node@1: retired DeepSeek NUMA1; disable + reset-failed
#     when it is not active (a live deepseek-layout instance is left alone).
#   - hotspot-net-setup, nvidia-persistenced, set-gpu-compute-mode: host
#     leftovers that are not part of the MAG room. If they are failed or
#     inactive, disable + reset-failed so `systemctl --failed` stays empty.
set -euo pipefail
[[ $EUID == 0 || -n ${MAG_CLEAR_FAILED_TEST:-} ]] || { echo 'root required' >&2; exit 1; }
log() { echo "clear-failed: $*"; }

# Safety: this file must never stop/restart the live NUMA0 DeepSeek node.
if grep -En 'systemctl[[:space:]]+(stop|restart|reload|try-restart|kill)[[:space:]].*mag-llm-ds-node@0' "$0" \
   | grep -vq '^[[:space:]]*#'; then
  echo 'clear-failed: refusing to run; @0 stop/restart leaked into this script' >&2
  exit 1
fi

is_loaded() {
  local st
  st=$(systemctl show -p LoadState --value "$1" 2>/dev/null || true)
  [[ $st == loaded ]]
}

disable_quiet() {
  local unit=$1 enabled
  enabled=$(systemctl is-enabled "$unit" 2>/dev/null || true)
  case "$enabled" in
    enabled|enabled-runtime|linked|linked-runtime|alias)
      systemctl disable "$unit" >/dev/null 2>&1 || log "WARN: disable $unit failed" ;;
  esac
}

reset_quiet() {
  systemctl reset-failed "$1" >/dev/null 2>&1 || true
}

# Retired DeepSeek NUMA1. Never --now (that would stop a running instance).
# Never mention mag-llm-ds-node@0 in a systemctl command.
ds1=mag-llm-ds-node@1.service
if is_loaded "$ds1"; then
  if systemctl is-active --quiet "$ds1"; then
    log "$ds1 is active; left running (deepseek layout)"
  else
    disable_quiet "$ds1"
    reset_quiet "$ds1"
    log "$ds1 disabled and reset-failed"
  fi
else
  reset_quiet "$ds1"
fi

# Unused host leftovers. Disable only when they are not doing work.
for unit in hotspot-net-setup.service nvidia-persistenced.service set-gpu-compute-mode.service; do
  is_loaded "$unit" || { reset_quiet "$unit"; continue; }
  if systemctl is-active --quiet "$unit"; then
    log "$unit is active; left running"
    continue
  fi
  disable_quiet "$unit"
  reset_quiet "$unit"
  log "$unit disabled and reset-failed"
done
log 'done'
