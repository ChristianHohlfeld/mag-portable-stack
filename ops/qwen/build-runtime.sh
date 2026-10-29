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
git -C "$SRC" checkout --force --detach "$REV"
[[ $(git -C "$SRC" rev-parse HEAD) == "$REV" ]] || exit 4
git -C "$SRC" apply --check "$ROOT/ops/qwen/end-checkpoint.patch"
git -C "$SRC" apply "$ROOT/ops/qwen/end-checkpoint.patch"
# Support early GLM-5.3 Flash GGUF metadata without changing canonical GLM or Qwen.
git -C "$SRC" apply --check "$ROOT/ops/glm/arch-compat.patch"
git -C "$SRC" apply "$ROOT/ops/glm/arch-compat.patch"
# MAG runtime gates, built by llama.cpp's own test helper so they link exactly
# like llama-server (same chat/PEG parser, same ggml CPU backend).
install -m 0644 "$ROOT/ops/glm/tests/test-elementwise-cast.cpp" "$SRC/tests/mag-glm-elementwise-cast.cpp"
install -m 0644 "$ROOT/ops/glm/tests/test-chat-template.cpp" "$SRC/tests/mag-glm-chat-template.cpp"
printf '%s\n' 'llama_build(mag-glm-elementwise-cast.cpp)' 'llama_build(mag-glm-chat-template.cpp)' >> "$SRC/tests/CMakeLists.txt"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF -DLLAMA_BUILD_TESTS=ON
cmake --build "$BUILD" -j 4 --target llama-server mag-glm-elementwise-cast mag-glm-chat-template
# Gate before publish: the elementwise-weight cast is an exact dequantize, and
# the pinned GLM template switches thinking and parses content/reasoning/tools.
"$BUILD/bin/mag-glm-elementwise-cast"
"$BUILD/bin/mag-glm-chat-template" "$ROOT/ops/glm/chat-template.jinja"
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
mkdir -p "$(dirname "$BIN")"
# Atomic publish after the entire patched runtime has built successfully.
# Resident Qwen remains mapped to its existing executable until the normal switch.
install -m 0755 "$BUILD/bin/llama-server" "$BIN.next"
mv -f "$BIN.next" "$BIN"
printf '%s\n' "$REV" > "$(dirname "$BIN")/source-commit"
patch_id=$(sha256sum "$ROOT/ops/qwen/end-checkpoint.patch" "$ROOT/ops/glm/arch-compat.patch" | sha256sum | cut -d' ' -f1)
printf '%s\n' "$patch_id" > "$(dirname "$BIN")/patch-identity"
echo "Pinned Qwen/GLM runtime built: $BIN (patch $patch_id)"
