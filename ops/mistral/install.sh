#!/usr/bin/env bash
# Stage only after all six shards pass the pinned HF SHA256s; never switch services.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 4; }
[[ ${1:-} == stage ]] || { echo 'usage: sudo bash ops/mistral/install.sh stage' >&2; exit 4; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
REV=$(cat "$ROOT/ops/mistral/runtime.sha")
BIN="/home/chris/.local/mistral/$REV-mag-cpu/bin/llama-server"
[[ $REV =~ ^[a-f0-9]{40}$ && -x "$BIN" ]] || { echo 'build pinned Mistral llama.cpp runtime first' >&2; exit 4; }
[[ $(cat "$(dirname "$BIN")/source-commit") == "$REV" ]] || { echo 'runtime commit mismatch' >&2; exit 4; }
DIR=/home/chris/models/Mistral-Large-4-1T-A52B-UD-IQ1_S
[[ -d "$DIR" && ! -L "$DIR" ]] || { echo 'Mistral download directory missing' >&2; exit 4; }
install -d -m 0755 /etc/mag-mistral /usr/local/lib/mag-mistral
install -m 0644 "$ROOT/ops/mistral/SHA256SUMS" /etc/mag-mistral/SHA256SUMS
if [[ -e "$DIR/SHA256SUMS" ]]; then
  cmp -s "$DIR/SHA256SUMS" "$ROOT/ops/mistral/SHA256SUMS" || { echo 'model-directory SHA256SUMS differs from pinned HF manifest' >&2; exit 4; }
else
  install -m 0644 "$ROOT/ops/mistral/SHA256SUMS" "$DIR/SHA256SUMS"
fi
bash "$ROOT/ops/mistral/verify-model.sh"
install -m 0644 "$ROOT/ops/mistral/runtime.sha" /usr/local/lib/mag-mistral/runtime.sha
install -m 0755 "$ROOT/ops/mistral/verify-model.sh" /usr/local/lib/mag-mistral/verify-model.sh
install -m 0755 "$ROOT/ops/mistral/start.sh" /usr/local/lib/mag-mistral/start.sh
install -d -o chris -g chris -m 0700 /var/lib/mag-llm/slots/mistral
install -m 0644 "$ROOT/ops/mistral/mag-llm-mistral-room.service" /etc/systemd/system/mag-llm-mistral-room.service
systemctl daemon-reload
echo 'STAGED ONLY: Mistral runtime, six-shard SHA256 and disabled unit verified; Qwen and GLM-Q2 remain untouched.'
