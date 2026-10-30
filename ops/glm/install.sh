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
install -m 0644 "$ROOT/ops/glm/model.sha256" /usr/local/lib/mag-glm/model.sha256
install -m 0755 "$ROOT/ops/glm/verify-model.sh" /usr/local/lib/mag-glm/verify-model.sh
install -m 0755 "$ROOT/ops/glm/start.sh" /usr/local/lib/mag-glm/start.sh
install -m 0644 "$ROOT/ops/glm/q1.env" /etc/mag-glm/q1.env
install -m 0644 "$ROOT/ops/glm/q2.env" /etc/mag-glm/q2.env
# Reviewed template: the embedded one cannot switch thinking off (ops/glm/README.md).
install -m 0644 "$ROOT/ops/glm/chat-template.jinja" /usr/local/lib/mag-glm/chat-template.jinja
bash /usr/local/lib/mag-glm/verify-model.sh
# Fail before NUMA1 cutover if the GGUF is not compatible with the pinned
# GLM5-Next loader. Even with IQ2_XS, size/header checks alone cannot
# detect missing KDA hyperparameters; keep this architecture preflight.
# This reads only the GGUF metadata header and never modifies model weights.
python3 -I "$ROOT/ops/glm/inspect-gguf.py" \
  /home/chris/models/GLM-5.3-Flash-GGUF-IQ2_XS/GLM-5.3-Flash-IQ2_XS-00001-of-00015.gguf \
  --require-kda-head-dim --require-glm-template-family \
  --chat-template-file "$ROOT/ops/glm/chat-template.jinja" --require-thinking-switch
# Read only tensor directories (all 15 shards). A known quantized small
# elementwise GLM weight may be cast to float; unknown quantized operands
# fail closed BEFORE Qwen on NUMA1 is stopped.
for part in /home/chris/models/GLM-5.3-Flash-GGUF-IQ2_XS/GLM-5.3-Flash-IQ2_XS-*-of-00015.gguf; do
  python3 -I "$ROOT/ops/glm/inspect-gguf.py" "$part" \
    --require-elementwise-safe >/dev/null
done
echo 'GLM GGUF elementwise types preflight passed (15 shards)'
install -d -o chris -g chris -m 0700 /var/lib/mag-llm/slots/glm /var/lib/mag-llm/slots/glm-q1
# Supports single GGUF or full split set, with TOFU size + SHA256 pins.
# Verification reads weights; never restarts either model.
install -m 0755 "$ROOT/ops/glm/verify-iq1.sh" /usr/local/lib/mag-glm/verify-iq1.sh
bash /usr/local/lib/mag-glm/verify-iq1.sh
install -m 0644 "$ROOT/ops/glm/mag-llm-glm@.service" /etc/systemd/system/mag-llm-glm@.service
# Drop-ins are instance-local; the shared @.service and each launcher are
# unchanged, so layout.sh does NOT restart a resident model for this staging.
for id in q1 q2; do
  dir="/etc/systemd/system/mag-llm-glm@$id.service.d"
  install -d -m 0755 "$dir"
  install -m 0644 "$ROOT/ops/glm/mag-llm-glm@$id.service.d/10-numa.conf" "$dir/10-numa.conf"
done
mag_daemon_reload
echo 'GLM staged: unit installed, model instances not started'
