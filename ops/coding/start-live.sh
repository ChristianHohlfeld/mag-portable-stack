#!/usr/bin/env bash
# NUMA-bound production launcher: one dedicated slot/port per bot.
set -euo pipefail
which=${1:?usage: start-live.sh 0|1}
case "$which" in
  0) node=0; cpus=0-13; port=8191; name=general
     alias=qwen3.6-35b-a3b; file=Qwen3.6-35B-A3B-UD-Q5_K_M.gguf ;;
  1) node=1; cpus=14-27; port=8192; name=coder
     alias=qwen3-coder-30b-a3b-instruct; file=Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf ;;
  *) echo 'coding: invalid NUMA node' >&2; exit 4 ;;
esac
REV=$(cat /usr/local/lib/mag-coding/runtime.sha)
[[ $REV =~ ^[a-f0-9]{40}$ ]] || exit 4
DIR="/home/chris/.local/mag-coding/$REV/bin"
BIN="$DIR/llama-server"
MODEL="/home/chris/models/mag-coding/$name/$file"
[[ -x $BIN && -f $MODEL && ! -L $MODEL ]] || { echo "coding $node: runtime/GGUF missing" >&2; exit 4; }
[[ $(cat "$DIR/source-commit" 2>/dev/null || true) == "$REV" ]] || exit 4
[[ $(cat "/sys/devices/system/node/node$node/cpulist") == $( [[ $node == 0 ]] && echo '0-13,28-41' || echo '14-27,42-55' ) ]] || {
  echo "coding $node: NUMA topology mismatch" >&2; exit 4;
}
SLOT="/var/lib/mag-llm/slots/coding/$name"
[[ -d $SLOT && -w $SLOT ]] || { echo 'coding: slot directory unavailable' >&2; exit 4; }
exec numactl --physcpubind="$cpus" --membind="$node" "$BIN" \
  --model "$MODEL" --alias "$alias" --host 127.0.0.1 --port "$port" \
  --ctx-size 65536 --parallel 1 --threads 14 --threads-batch 14 \
  --batch-size 512 --ubatch-size 128 --jinja --slot-save-path "$SLOT"
