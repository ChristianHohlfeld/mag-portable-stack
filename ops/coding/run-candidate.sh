#!/usr/bin/env bash
# Optional OFFLINE candidate only. No service mutation, no production port binding.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
name=${1:?usage: run-candidate.sh general|coder}
case "$name" in
  general)
    port=8291
    node=0
    cpus=0-13
    model_id=qwen3.6-35b-a3b
    filename=Qwen3.6-35B-A3B-UD-Q5_K_M.gguf
    live=mag-llm-mistral-room.service
    ;;
  coder)
    port=8292
    node=1
    cpus=14-27
    model_id=qwen3-coder-30b-a3b-instruct
    filename=Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf
    live=mag-llm-glm@q2.service
    ;;
  *) echo 'Choose general or coder' >&2; exit 2 ;;
esac
: "${MAG_CODING_LLAMA_BIN:?Set MAG_CODING_LLAMA_BIN to a tested llama-server with qwen35moe and qwen3moe support}"
[[ -x "$MAG_CODING_LLAMA_BIN" ]] || { echo 'Selected llama-server not executable' >&2; exit 4; }
# Do not co-schedule a CPU benchmark with its production socket.
if systemctl is-active --quiet "$live"; then
  echo "Refusing benchmark: $live is running on NUMA$node; no process was stopped" >&2
  exit 4
fi
# This script must not be run by the root owner of a production service.
[[ "$EUID" -ne 0 ]] || { echo 'Run as regular user, not root' >&2; exit 4; }
python3 -I "$ROOT/stage.py" --model "$name"
model="/home/chris/models/mag-coding/$name/$filename"
[[ -f "$model" && ! -L "$model" ]] || exit 4
mkdir -p "/home/chris/models/mag-coding/slots/$name"
# No global cache eviction or systemd changes; use dedicated ports and slots.
exec numactl --physcpubind="$cpus" --membind="$node" "$MAG_CODING_LLAMA_BIN" \
  --model "$model" --alias "$model_id" --host 127.0.0.1 --port "$port" \
  --ctx-size 65536 --parallel 1 --threads 14 --threads-batch 14 \
  --numa numactl --batch-size 1024 --ubatch-size 256 --jinja \
  --slot-save-path "/home/chris/models/mag-coding/slots/$name" --temp 1.0 --top-p 0.95
