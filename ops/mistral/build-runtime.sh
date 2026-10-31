#!/usr/bin/env bash
# Build a dedicated upstream llama.cpp CPU runtime; never replace the live Qwen/GLM binaries.
set -euo pipefail
[[ "$(id -un)" == chris ]] || { echo 'run as chris, not root' >&2; exit 4; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
REV=$(cat "$ROOT/ops/mistral/runtime.sha")
[[ "$REV" =~ ^[a-f0-9]{40}$ ]] || exit 4
SRC=/home/chris/.local/src/mag-mistral-llama.cpp
BUILD="$SRC/build-mag-mistral"
if [[ ! -e "$SRC/.git" ]]; then
  [[ ! -e "$SRC" ]] || { echo 'non-git source dir exists' >&2; exit 4; }
  mkdir -p "$(dirname "$SRC")"
  git clone https://github.com/ggml-org/llama.cpp.git "$SRC"
fi
git -C "$SRC" fetch --no-tags origin "$REV"
git -C "$SRC" checkout --force --detach "$REV"
[[ $(git -C "$SRC" rev-parse HEAD) == "$REV" ]] || exit 4
# Fail closed unless the pinned source recognizes the GGUF architecture and has its loader.
python3 "$ROOT/ops/mistral/check-architecture.py" --source "$SRC" --gguf-architecture mistral-large4
cmake -S "$SRC" -B "$BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DGGML_AVX2=ON \
  -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF -DGGML_AVX512_VNNI=OFF -DGGML_CUDA=OFF \
  -DBUILD_SHARED_LIBS=OFF -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON
cmake --build "$BUILD" -j 4 --target llama-server
BIN="/home/chris/.local/mistral/$REV-mag-cpu/bin/llama-server"
mkdir -p "$(dirname "$BIN")"
install -m 0755 "$BUILD/bin/llama-server" "$BIN.next"
mv -f "$BIN.next" "$BIN"
printf '%s\n' "$REV" > "$(dirname "$BIN")/source-commit"
"$BIN" --version
echo "Pinned Mistral Large 4 CPU runtime built: $BIN"
