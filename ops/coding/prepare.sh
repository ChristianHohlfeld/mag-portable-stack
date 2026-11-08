#!/usr/bin/env bash
# Pre-stage both pinned GGUFs and a dedicated CPU runtime BEFORE any live service is stopped.
# Runs as chris under auto-update/apply.sh; old model files and processes remain unchanged.
set -euo pipefail
# All checksum and build CPU work on sibling threads, not active model cores.
if [[ -z ${MAG_PREPARE_PINNED:-} ]] && taskset -c "${MAG_BUILD_CPUS:-28-55}" true 2>/dev/null; then
  MAG_PREPARE_PINNED=1 exec taskset -c "${MAG_BUILD_CPUS:-28-55}" nice -n 19 ionice -c3 bash "$0" "$@"
fi
[[ $(id -un) == chris ]] || { echo 'coding prepare: must run as chris' >&2; exit 4; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
REV=$(tr -d '[:space:]' < "$ROOT/ops/qwen/runtime.sha")
[[ $REV =~ ^[a-f0-9]{40}$ ]] || exit 4
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
# Download and verify in advance, resumably. A hash mismatch never activates a model.
# An identical GGUF already on disk is hard-linked after its SHA256 check.
python3 -I "$ROOT/ops/coding/stage.py" --model general --download
# NUMA1 is node-scoped: until all four DeepSeek shards verify, the current NUMA1 model keeps :8192.
python3 -I "$ROOT/ops/coding/stage.py" --model coder --download ||
  echo 'coding prepare: DeepSeek shards not staged yet; NUMA1 keeps its current model'
# The shared, pinned qwen4exp runtime (also implements qwen3next) is built and
# checksummed by ops/qwen/build-runtime.sh; never build an unpinned binary here.
if [[ ! -x $BIN || $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) != "$REV" ]]; then
  nice -n 19 ionice -c 3 bash "$ROOT/ops/qwen/build-runtime.sh"
fi
[[ -x $BIN && $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) == "$REV" ]] || {
  echo 'coding prepare: pinned qwen4exp runtime missing' >&2; exit 4; }
# NUMA1 Flint: separately pinned upstream runtime (ops/coding/runtime.sha).
nice -n 19 ionice -c 3 bash "$ROOT/ops/coding/build-runtime.sh"
# NUMA0 requires the pinned MI50 Vulkan runtime; never fall back to CPU.
MAG_CODING_GPU=vulkan nice -n 19 ionice -c 3 bash "$ROOT/ops/coding/build-runtime.sh"
CREV=$(tr -d '[:space:]' < "$ROOT/ops/coding/runtime.sha")
VBIN="/home/chris/.local/mag-coding-rt/$CREV-vulkan/bin/llama-server"
[[ -x $VBIN && $(cat "$(dirname "$VBIN")/source-commit" 2>/dev/null || true) == "$CREV" ]] || {
  echo "coding prepare: pinned Vulkan runtime missing" >&2; exit 4; }
VHELP=$("$VBIN" --help 2>&1)
for opt in --device --n-gpu-layers --no-host --ubatch-size --cache-type-k --cache-type-v; do
  grep -Fq -- "$opt" <<<"$VHELP" || { echo "coding prepare: Vulkan runtime lacks $opt" >&2; exit 4; }
done
SRC=/home/chris/.local/src/mag-qwen-llama.cpp
if [[ -d $SRC/.git && $(git -C "$SRC" rev-parse HEAD) == "$REV" ]]; then
  # Both architectures require model-specific graph implementations.
  for arch in QWEN4EXP QWEN3NEXT; do
    grep -Fq "LLM_ARCH_$arch" "$SRC/src/llama-arch.cpp" || exit 4
  done
fi
"$BIN" --version
# Explicitly guard the required server options. Absent options abort staging, not live.
help=$("$BIN" --help 2>&1)
for opt in --slot-save-path --jinja --reasoning-format --load-mode --ctx-checkpoints; do
  grep -Fq -- "$opt" <<<"$help" || { echo "coding prepare: runtime lacks $opt" >&2; exit 4; }
done
echo "coding prepare: both GGUFs and pinned runtime verified; old models untouched"
