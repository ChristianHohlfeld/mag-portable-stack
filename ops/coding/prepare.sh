#!/usr/bin/env bash
# Pre-stage both pinned GGUFs and a dedicated CPU runtime BEFORE any live service is stopped.
# Runs as chris under auto-update/apply.sh; old model files and processes remain unchanged.
set -euo pipefail
[[ $(id -un) == chris ]] || { echo 'coding prepare: must run as chris' >&2; exit 4; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
REV=$(tr -d '[:space:]' < "$ROOT/ops/mistral/runtime.sha")
[[ $REV =~ ^[a-f0-9]{40}$ ]] || exit 4
BIN="/home/chris/.local/mag-coding/$REV/bin/llama-server"
SRC=/home/chris/.local/src/mag-coding-llama.cpp
# Download and verify in advance, resumably. A hash mismatch never activates a model.
python3 -I "$ROOT/ops/coding/stage.py" --model both --download
if [[ -x $BIN && $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) == "$REV" ]]; then
  "$BIN" --version
  exit 0
fi
if [[ ! -d $SRC/.git ]]; then
  [[ ! -e $SRC ]] || { echo 'coding prepare: non-git source directory' >&2; exit 4; }
  mkdir -p "$(dirname "$SRC")"
  git clone --filter=blob:none https://github.com/ggml-org/llama.cpp.git "$SRC"
fi
git -C "$SRC" fetch --no-tags origin "$REV"
git -C "$SRC" checkout --force --detach "$REV"
[[ $(git -C "$SRC" rev-parse HEAD) == "$REV" ]] || exit 4
# Both qwen35moe and qwen3moe require model-specific graph implementations.
for arch in QWEN35MOE QWEN3MOE; do
  grep -Fq "LLM_ARCH_$arch" "$SRC/src/llama-arch.cpp" || exit 4
  grep -Fq "LLM_ARCH_$arch" "$SRC/src/llama-model.cpp" || exit 4
  grep -Fq "LLM_ARCH_$arch" "$SRC/src/llama-model-graph.cpp" || exit 4
done
cmake -S "$SRC" -B "$SRC/build-mag-coding" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DGGML_AVX2=ON \
  -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF -DGGML_AVX512_VNNI=OFF \
  -DGGML_CUDA=OFF -DBUILD_SHARED_LIBS=OFF -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON
cmake --build "$SRC/build-mag-coding" --target llama-server -j 4
mkdir -p "$(dirname "$BIN")"
install -m 0755 "$SRC/build-mag-coding/bin/llama-server" "$BIN.next"
mv -f "$BIN.next" "$BIN"
printf '%s\n' "$REV" > "$(dirname "$BIN")/source-commit"
"$BIN" --version
# Explicitly guard the required server options. Absent options abort staging, not live.
"$BIN" --help 2>&1 | grep -Fq -- '--slot-save-path'
"$BIN" --help 2>&1 | grep -Fq -- '--jinja'
echo "coding prepare: both GGUFs and pinned runtime verified; old models untouched"
