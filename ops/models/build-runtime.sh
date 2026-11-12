#!/usr/bin/env bash
# Run as the owner (ops/host.env, chris on mag). Builds the pinned, CPU-only llama.cpp server that both bots
# use (models.json: runtime.rev, runtime.dir). No GPU backend is compiled in:
# the MI50 belongs to MAG Idol alone. A finished build is never rebuilt.
set -euo pipefail
# Stay off the model cores 0-27 while the bots are running.
if [[ -z ${MAG_BUILD_PINNED:-} ]] && taskset -c "${MAG_BUILD_CPUS:-28-55}" true 2>/dev/null; then
  MAG_BUILD_PINNED=1 exec taskset -c "${MAG_BUILD_CPUS:-28-55}" nice -n 19 bash "$0" "$@"
fi
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
# shellcheck source=../host.sh
source "$ROOT/ops/host.sh"
[[ $(id -un) == "$MAG_OWNER" ]] || { echo "run as $MAG_OWNER, not $(id -un)" >&2; exit 4; }
REV=$(mag_render "$ROOT/ops/models/models.json" | jq -r '.runtime.rev')
DIR=$(mag_render "$ROOT/ops/models/models.json" | jq -r '.runtime.dir')
[[ $REV =~ ^[a-f0-9]{40}$ && $DIR == "$MAG_OWNER_HOME"/* ]] || exit 4
BIN="$DIR/$REV/bin/llama-server"
if [[ -x $BIN && $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) == "$REV" ]]; then
  echo "model runtime already built: $BIN"; exit 0
fi
SRC=$MAG_OWNER_HOME/.local/src/mag-coding-llama.cpp
BUILD="$SRC/build-mag-models"
if [[ ! -e $SRC/.git ]]; then
  mkdir -p "$(dirname "$SRC")"
  git clone -q https://github.com/ggml-org/llama.cpp.git "$SRC"
fi
git -C "$SRC" fetch -q --no-tags origin "$REV"
git -C "$SRC" checkout -q --force --detach "$REV"
[[ $(git -C "$SRC" rev-parse HEAD) == "$REV" ]] || exit 4
for arch in GEMMA4 OPENAI_MOE; do
  grep -Fq "LLM_ARCH_$arch" "$SRC/src/llama-arch.cpp" || { echo "runtime lacks $arch" >&2; exit 4; }
done
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON \
  -DBUILD_SHARED_LIBS=OFF -DGGML_VULKAN=OFF -DGGML_CUDA=OFF -DGGML_HIP=OFF
cmake --build "$BUILD" -j 8 --target llama-server
mkdir -p "$(dirname "$BIN")"
install -m 0755 "$BUILD/bin/llama-server" "$BIN.next"
mv -f "$BIN.next" "$BIN"
printf '%s\n' "$REV" > "$(dirname "$BIN")/source-commit"
"$BIN" --version
echo "Pinned model runtime built: $BIN"
