#!/usr/bin/env bash
# Run as chris. Dedicated upstream llama.cpp build: never replace ik_llama/DeepSeek.
set -euo pipefail
[[ "$(id -un)" == chris ]] || { echo 'run as chris, not root' >&2; exit 4; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
REV=$(cat "$ROOT/ops/qwen/runtime.sha")
[[ "$REV" =~ ^[a-f0-9]{40}$ ]] || exit 4
SRC=/home/chris/.local/src/mag-qwen-llama.cpp
BUILD="$SRC/build-mag-qwen"
if [[ ! -e "$SRC/.git" ]]; then
  [[ ! -e "$SRC" ]] || { echo 'non-git source dir exists' >&2; exit 4; }
  mkdir -p "$(dirname "$SRC")"
  git clone https://github.com/ggml-org/llama.cpp.git "$SRC"
fi
git -C "$SRC" fetch --no-tags origin "$REV"
git -C "$SRC" checkout --detach "$REV"
[[ $(git -C "$SRC" rev-parse HEAD) == "$REV" ]] || exit 4
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF
cmake --build "$BUILD" -j 4 --target llama-server
BIN="/home/chris/.local/qwen4exp/$REV/bin/llama-server"
mkdir -p "$(dirname "$BIN")"
install -m 0755 "$BUILD/bin/llama-server" "$BIN"
printf '%s\n' "$REV" > "$(dirname "$BIN")/source-commit"
echo "Qwen runtime built: $BIN"
