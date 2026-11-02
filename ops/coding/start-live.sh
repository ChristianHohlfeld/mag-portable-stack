#!/usr/bin/env bash
# NUMA-bound production launcher: one dedicated slot/port per bot.
# Flags follow the llama.cpp server README (tools/server/README.md at the
# pinned ops/qwen/runtime.sha) and the official model cards:
#  - Qwen3.8-Flash-Next (qwen4exp): https://huggingface.co/Qwen/Qwen3.8-Flash-Next
#  - Qwen3-Coder-Next (qwen3next): https://huggingface.co/Qwen/Qwen3-Coder-Next
# Both use the GGUF's own Jinja chat template (--jinja); reasoning is split
# into reasoning_content (--reasoning-format auto) and never reaches the room text.
set -euo pipefail
which=${1:?usage: start-live.sh 0|1}
case "$which" in
  0) node=0; cpus=0-13; port=8191; name=general; ctx=65536
     alias=qwen3.8-flash-next; file=Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf
     # qwen4exp per-layer token embeddings stay in host RAM (as the proven
     # ops/qwen/start.sh launcher measured at 6.9-7.0 tok/s on this host).
     extra=(-ot per_layer_token_embd=CPU) ;;
  1) node=1; cpus=14-27; port=8192; name=coder; ctx=65536
     alias=qwen3-coder-next; file=Qwen3-Coder-Next-UD-Q4_K_XL.gguf
     extra=() ;;
  *) echo 'coding: invalid NUMA node' >&2; exit 4 ;;
esac
REV=$(cat /usr/local/lib/mag-coding/runtime.sha)
[[ $REV =~ ^[a-f0-9]{40}$ ]] || exit 4
# The pinned, already-verified qwen4exp runtime (ops/qwen/build-runtime.sh)
# also implements qwen3next; one binary serves both bots.
DIR="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin"
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
