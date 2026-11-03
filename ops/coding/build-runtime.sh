#!/usr/bin/env bash
# Run as chris. Dedicated, pinned upstream llama.cpp build for the coding room's
# NUMA1 bot (Flint; Laguna S 2.1 later). The Qwen/GLM runtime of
# ops/qwen/runtime.sha (NUMA0 Qwen3.8) stays untouched, so a rollback to
# Qwen3-Coder-Next keeps its own binary.
#  - `laguna` arch: ggml-org/llama.cpp#25165 (merged 2026-07-22)
#  - `flint`  arch: ggml-org/llama.cpp#30928 (merged 2026-10-21)
# Pin: release b12058 (ops/coding/runtime.sha), which contains both.
set -euo pipefail
[[ "$(id -un)" == chris ]] || { echo 'run as chris, not root' >&2; exit 4; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
REV=$(tr -d '[:space:]' < "$ROOT/ops/coding/runtime.sha")
[[ "$REV" =~ ^[a-f0-9]{40}$ ]] || exit 4
BIN="/home/chris/.local/mag-coding-rt/$REV/bin/llama-server"
if [[ -x $BIN && $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) == "$REV" ]]; then
  echo "coding runtime already built: $BIN"; exit 0
fi
SRC=/home/chris/.local/src/mag-coding-llama.cpp
BUILD="$SRC/build-mag-coding"
if [[ ! -e "$SRC/.git" ]]; then
  [[ ! -e "$SRC" ]] || { echo 'non-git source dir exists' >&2; exit 4; }
  mkdir -p "$(dirname "$SRC")"
  # Reuse the objects of the existing Qwen checkout when present (slow uplink).
  if [[ -d /home/chris/.local/src/mag-qwen-llama.cpp/.git ]]; then
    git clone -q --reference /home/chris/.local/src/mag-qwen-llama.cpp --dissociate \
      https://github.com/ggml-org/llama.cpp.git "$SRC"
  else
    git clone -q https://github.com/ggml-org/llama.cpp.git "$SRC"
  fi
fi
git -C "$SRC" fetch -q --no-tags origin "$REV"
git -C "$SRC" checkout -q --force --detach "$REV"
git -C "$SRC" clean -fdxq -e build-mag-coding
[[ $(git -C "$SRC" rev-parse HEAD) == "$REV" ]] || exit 4
# Same MAG server patch as the Qwen runtime: keep the completed-answer
# checkpoint for SWA models (both Laguna and Flint use sliding-window layers).
git -C "$SRC" apply --check "$ROOT/ops/qwen/end-checkpoint.patch"
git -C "$SRC" apply "$ROOT/ops/qwen/end-checkpoint.patch"
for arch in LAGUNA FLINT; do
  grep -Fq "LLM_ARCH_$arch" "$SRC/src/llama-arch.cpp" || { echo "runtime lacks $arch" >&2; exit 4; }
done
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF
cmake --build "$BUILD" -j 8 --target llama-server
mkdir -p "$(dirname "$BIN")"
install -m 0755 "$BUILD/bin/llama-server" "$BIN.next"
mv -f "$BIN.next" "$BIN"
printf '%s\n' "$REV" > "$(dirname "$BIN")/source-commit"
sha256sum "$ROOT/ops/qwen/end-checkpoint.patch" | cut -d' ' -f1 > "$(dirname "$BIN")/patch-identity"
"$BIN" --version
echo "Pinned coding runtime built: $BIN"
