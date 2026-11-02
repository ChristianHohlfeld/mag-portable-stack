#!/usr/bin/env bash
# NUMA-bound production launcher: one dedicated slot/port per bot.
# Flags follow the llama.cpp server README (tools/server/README.md at the
# pinned ops/coding/runtime.sha) and the official model cards:
#  - Laguna S 2.1 (laguna):  https://huggingface.co/poolside/Laguna-S-2.1
#    GGUF https://huggingface.co/ggml-org/Laguna-S-2.1-GGUF ; generation_config:
#    temperature 1.0, top_p 1.0, top_k 20, min_p 0.0
#  - Flint (flint):          https://huggingface.co/RecursiveSI/flint
#    GGUF https://huggingface.co/ggml-org/Flint-GGUF ; card: temperature 1.0,
#    top_p 0.95, top_k 20
# Both use the GGUF's own Jinja chat template (--jinja); reasoning is split
# into reasoning_content (--reasoning-format auto) and never reaches the room text.
# The sampling flags are only server defaults; the room sends the same values.
set -euo pipefail
which=${1:?usage: start-live.sh 0|1}
case "$which" in
  0) node=0; cpus=0-13; port=8191; name=general; ctx=65536
     alias=laguna-s-2.1; file=Laguna-S-2.1-Q4_K_M.gguf
     extra=(--temp 1.0 --top-p 1.0 --top-k 20 --min-p 0.0) ;;
  1) node=1; cpus=14-27; port=8192; name=coder; ctx=65536
     alias=flint; file=Flint-Q8_0.gguf
     extra=(--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0) ;;
  *) echo 'coding: invalid NUMA node' >&2; exit 4 ;;
esac
REV=$(cat /usr/local/lib/mag-coding/runtime.sha)
[[ $REV =~ ^[a-f0-9]{40}$ ]] || exit 4
# The pinned coding runtime (ops/coding/build-runtime.sh) implements both
# `laguna` and `flint`; one binary serves both bots.
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
