#!/usr/bin/env bash
# Blank-host LLM runtimes and units (called by ops/bootstrap.sh, phase llm-runtime).
#  1. llama.cpp  (ops/deepseek/build-llama.sh, pinned ref + patches)  as chris
#  2. ik_llama   (ops/deepseek/build-ik.sh, pinned commit + patches)  as chris
#  3. Qwen llama.cpp (ops/qwen/build-runtime.sh) only when
#     /etc/mag-stack/secondary-model.env says MAG_SECONDARY_MODEL=qwen
#  4. Units: mag-llm.service via install-deepseek.sh --apply (renders the unit
#     with render-llm-unit.sh from /etc/mag-llm/llm.env) only when no unit is
#     installed yet; the NUMA1 unit for the configured secondary model.
# Idempotent: the build scripts return early for an existing build; existing
# units and the /home/chris/.local/bin/llama link are never replaced.
# Never starts, stops or restarts mag-llm, mag-llm-iq3 or mag-llm-qwen. On a
# fresh install mag-model-gate starts mag-llm only after the full SHA-256
# model check has passed (that is the designed first start).
#   sudo bash ops/deepseek/bootstrap-runtimes.sh [--dry-run]
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
DRY=0
[[ ${1:-} == --dry-run ]] && DRY=1
log() { echo "llm-runtime: $*"; }
run() { printf '+'; printf ' %q' "$@"; printf '\n'; (( DRY )) || "$@"; }
as_chris() { runuser -u chris -- env HOME=/home/chris PATH=/usr/local/bin:/usr/bin:/bin "$@"; }
secondary=iq3
if [[ -r /etc/mag-stack/secondary-model.env ]] &&
   grep -Fxq 'MAG_SECONDARY_MODEL=qwen' /etc/mag-stack/secondary-model.env; then
  secondary=qwen
fi
log "secondary NUMA1 model from secondary-model.env: $secondary"

if (( DRY )); then
  echo "+ runuser -u chris -- bash $ROOT/ops/deepseek/build-llama.sh   # ln -s result to /home/chris/.local/bin/llama when absent"
  echo "+ runuser -u chris -- bash $ROOT/ops/deepseek/build-ik.sh"
  [[ $secondary == qwen ]] && echo "+ runuser -u chris -- bash $ROOT/ops/qwen/build-runtime.sh"
  echo "+ bash $ROOT/ops/deepseek/install-deepseek.sh --apply   # only when /etc/systemd/system/mag-llm.service is absent"
  if [[ $secondary == qwen ]]; then
    echo "+ bash $ROOT/ops/qwen/install.sh stage   # only when the upstream model pin exists"
  else
    echo "+ install mag-llm-iq3.service + start-iq3.sh/verify-iq3.sh   # only when absent; enabled, not started"
  fi
  exit 0
fi
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
id chris >/dev/null 2>&1 || { echo 'user chris missing' >&2; exit 1; }
# The build scripts expect to run from a tree chris can read.
as_chris test -r "$ROOT/ops/deepseek/build-llama.sh" ||
  { echo "chris cannot read $ROOT; run bootstrap from a checkout owned by chris" >&2; exit 1; }

llama=$(as_chris bash "$ROOT/ops/deepseek/build-llama.sh" | tail -n 1)
[[ -x $llama ]] || { echo "llama build did not produce a binary ($llama)" >&2; exit 1; }
if [[ -e /home/chris/.local/bin/llama || -L /home/chris/.local/bin/llama ]]; then
  log "leave existing /home/chris/.local/bin/llama -> $(readlink -f /home/chris/.local/bin/llama || true)"
else
  as_chris install -d /home/chris/.local/bin
  as_chris ln -s "$llama" /home/chris/.local/bin/llama
  log "/home/chris/.local/bin/llama -> $llama"
fi

ik=$(as_chris bash "$ROOT/ops/deepseek/build-ik.sh" | tail -n 1)
[[ -x $ik ]] || { echo "ik_llama build did not produce a binary ($ik)" >&2; exit 1; }
want_ik=$(sed -n 's/^IK_BIN=//p' /etc/mag-llm/llm.env 2>/dev/null | tr -d '"' | tail -n 1)
if [[ -n $want_ik && $want_ik != "$ik" ]]; then
  echo "WARN: /etc/mag-llm/llm.env IK_BIN=$want_ik but build-ik.sh produced $ik" >&2
fi

if [[ $secondary == qwen ]]; then
  as_chris bash "$ROOT/ops/qwen/build-runtime.sh"
fi

if [[ -e /etc/systemd/system/mag-llm.service ]]; then
  log 'mag-llm.service already installed; not re-rendered (re-render deliberately with install-deepseek.sh)'
else
  bash "$ROOT/ops/deepseek/install-deepseek.sh" --apply
fi

if [[ $secondary == qwen ]]; then
  if systemctl is-active --quiet mag-llm-qwen.service; then
    log 'mag-llm-qwen active; not restaged'
  elif [[ -s /home/chris/models/qwen3.8-flash-next-uncensored/upstream.sha256 ]]; then
    bash "$ROOT/ops/qwen/install.sh" stage
  else
    echo 'WARN: Qwen selected but /home/chris/models/qwen3.8-flash-next-uncensored/upstream.sha256 is missing.' >&2
    echo '      Download the Qwen GGUF and run ops/qwen/pin-model.py, then: sudo bash ops/qwen/install.sh stage' >&2
  fi
else
  lib=/usr/local/lib/mag-deepseek
  install -d -m 0755 "$lib" /var/lib/mag-llm
  for f in start-iq3.sh verify-iq3.sh; do
    [[ -e $lib/$f ]] || install -m 0755 "$ROOT/ops/deepseek/$f" "$lib/$f"
  done
  install -d -o chris -g chris -m 0700 /var/lib/mag-llm/slots /var/lib/mag-llm/slots/iq3
  if [[ -e /etc/systemd/system/mag-llm-iq3.service ]]; then
    log 'mag-llm-iq3.service already installed; left unchanged'
  else
    install -m 0644 "$ROOT/ops/deepseek/mag-llm-iq3.service" /etc/systemd/system/mag-llm-iq3.service
    systemctl daemon-reload
    # Starts on the next boot only once the IQ3 shards and SHA256SUMS exist.
    systemctl enable mag-llm-iq3.service
    log 'installed and enabled mag-llm-iq3.service (not started)'
  fi
fi
log 'done'
