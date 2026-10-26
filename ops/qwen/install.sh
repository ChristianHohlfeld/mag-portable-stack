#!/usr/bin/env bash
# Stage only: do not start/stop any model, no gateway/room restarts, no config flips.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 4; }
[[ ${1:-} == stage || ${1:-} == refresh ]] || { echo 'usage: sudo bash ops/qwen/install.sh stage' >&2; exit 4; }
[[ ${1:-} == refresh ]] || ! systemctl is-active --quiet mag-llm-qwen.service || { echo 'Qwen is already running; refuse restage' >&2; exit 4; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
REV=$(cat "$ROOT/ops/qwen/runtime.sha")
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
[[ "$REV" =~ ^[a-f0-9]{40}$ && -x "$BIN" ]] || { echo 'build pinned Qwen llama.cpp first' >&2; exit 4; }
[[ $(cat "$(dirname "$BIN")/source-commit") == "$REV" ]] || exit 4
PIN=/home/chris/models/qwen3.8-flash-next-uncensored/upstream.sha256
[[ -s "$PIN" && ! -L "$PIN" ]] || { echo 'run pin-model.py to fetch upstream SHA256 first' >&2; exit 4; }
install -d -m 0755 /etc/mag-qwen /usr/local/lib/mag-qwen
install -m 0644 "$PIN" /etc/mag-qwen/model.sha256
install -m 0644 "$ROOT/ops/qwen/runtime.sha" /usr/local/lib/mag-qwen/runtime.sha
install -m 0755 "$ROOT/ops/qwen/verify-model.sh" /usr/local/lib/mag-qwen/verify-model.sh
install -m 0755 "$ROOT/ops/qwen/start.sh" /usr/local/lib/mag-qwen/start.sh
# Verify before making the new systemd unit visible. ~98 GB read, deliberately.
bash /usr/local/lib/mag-qwen/verify-model.sh
install -d -o chris -g chris -m 0700 /var/lib/mag-llm/slots/qwen
install -m 0644 "$ROOT/ops/qwen/mag-llm-qwen.service" /etc/systemd/system/mag-llm-qwen.service
systemctl daemon-reload
echo 'STAGED ONLY: IQ3/Q4 untouched; Qwen disabled, no gateway or room changes.'

