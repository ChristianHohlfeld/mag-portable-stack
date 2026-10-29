#!/usr/bin/env bash
# Stage only (called by ops/qwen/layout.sh for layout "glm"): install start
# script, model check and unit. Never starts or stops a model; verifies the
# GGUF parts BEFORE the unit becomes visible, i.e. before NUMA1 is touched.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 4; }
[[ ${1:-} == refresh ]] || { echo 'usage: sudo bash ops/glm/install.sh refresh' >&2; exit 4; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
# shellcheck source=../mag-units.sh
source "$ROOT/ops/mag-units.sh"
REV=$(cat "$ROOT/ops/qwen/runtime.sha")
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
[[ "$REV" =~ ^[a-f0-9]{40}$ && -x "$BIN" ]] || { echo 'build pinned llama.cpp runtime first' >&2; exit 4; }
[[ $(cat "$(dirname "$BIN")/source-commit") == "$REV" ]] || exit 4
install -d -m 0755 /etc/mag-glm /usr/local/lib/mag-glm
install -m 0644 "$ROOT/ops/qwen/runtime.sha" /usr/local/lib/mag-glm/runtime.sha
install -m 0644 "$ROOT/ops/glm/model.sizes" /usr/local/lib/mag-glm/model.sizes
install -m 0755 "$ROOT/ops/glm/verify-model.sh" /usr/local/lib/mag-glm/verify-model.sh
install -m 0755 "$ROOT/ops/glm/start.sh" /usr/local/lib/mag-glm/start.sh
bash /usr/local/lib/mag-glm/verify-model.sh
install -d -o chris -g chris -m 0700 /var/lib/mag-llm/slots/glm
install -m 0644 "$ROOT/ops/glm/mag-llm-glm.service" /etc/systemd/system/mag-llm-glm.service
mag_daemon_reload
echo 'GLM staged: unit installed, NUMA1 untouched'
