#!/usr/bin/env bash
# NUMA-bound production launcher: one dedicated slot/port per bot.
# Flags follow the llama.cpp server README (tools/server/README.md at the
# pinned ops/qwen/runtime.sha) and the official model cards:
#  - Qwen3.8-Flash-Next (qwen4exp): https://huggingface.co/Qwen/Qwen3.8-Flash-Next
#  - Flint (flint, NUMA1):    https://huggingface.co/RecursiveSI/flint
#    GGUF https://huggingface.co/ggml-org/Flint-GGUF ; card sampling
#    temperature 1.0, top_p 0.95, top_k 20 (server defaults; the room sends the same).
#    Needs the separately pinned llama.cpp of ops/coding/runtime.sha (flint arch,
#    ggml-org/llama.cpp#30928), installed as /usr/local/lib/mag-coding/runtime-coder.sha.
# Both use the GGUF's own Jinja chat template (--jinja); reasoning is split
# into reasoning_content (--reasoning-format auto) and never reaches the room text.
set -euo pipefail
which=${1:?usage: start-live.sh 0|1}
case "$which" in
  # Owner 2026-11-03 12:08: Flint Q4_K_M on BOTH sockets, one instance per
  # node, never spanning (the NUMA interconnect is the bottleneck). Qwen3.8's
  # GGUF stays on disk. ctx = Flint card's documented 262,144-token window
  # (13 of 49 layers are full attention, 4 KV heads: ~7 GB f16 KV per node).
  0) node=0; cpus=0-13; port=8191; name=general; ctx=262144
     alias=flint-numa0; file=Flint-Q4_K_M.gguf
     extra=(--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0) ;;
  1) node=1; cpus=14-27; port=8192; name=coder; ctx=262144
     alias=flint; file=Flint-Q4_K_M.gguf
     extra=(--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0) ;;
  *) echo 'coding: invalid NUMA node' >&2; exit 4 ;;
esac
# Both nodes: the pinned flint runtime (ops/coding/build-runtime.sh).
# (The qwen4exp runtime of ops/qwen/runtime.sha stays installed for rollback.)
REV=$(cat /usr/local/lib/mag-coding/runtime-coder.sha)
[[ $REV =~ ^[a-f0-9]{40}$ ]] || exit 4
DIR="/home/chris/.local/mag-coding-rt/$REV/bin"
BIN="$DIR/llama-server"
MODEL="/home/chris/models/mag-coding/$name/$file"
[[ -x $BIN && -f $MODEL && ! -L $MODEL ]] || { echo "coding $node: runtime/GGUF missing" >&2; exit 4; }
[[ $(cat "$DIR/source-commit" 2>/dev/null || true) == "$REV" ]] || exit 4
[[ $(cat "/sys/devices/system/node/node$node/cpulist") == $( [[ $node == 0 ]] && echo '0-13,28-41' || echo '14-27,42-55' ) ]] || {
  echo "coding $node: NUMA topology mismatch" >&2; exit 4;
}
# A reboot must never load unchecked GGUF bytes. Hash on the target node so
# the page cache the model is then loaded from is node-local.
numactl --cpunodebind="$node" --membind="$node" python3 -I /usr/local/lib/mag-coding/stage.py --model "$name"
SLOT="/var/lib/mag-llm/slots/coding/$name"
[[ -d $SLOT && -w $SLOT ]] || { echo 'coding: slot directory unavailable' >&2; exit 4; }
exec numactl --physcpubind="$cpus" --membind="$node" "$BIN" \
  --model "$MODEL" --alias "$alias" --host 127.0.0.1 --port "$port" \
  --ctx-size "$ctx" --parallel 1 --threads 14 --threads-batch 14 --numa numactl \
  --load-mode mlock --lazy-mode off "${extra[@]}" \
  --jinja --reasoning-format auto \
  --cache-ram 16384 --ctx-checkpoints 8 --checkpoint-min-step 128 \
  --slots --slot-save-path "$SLOT" --no-ui
