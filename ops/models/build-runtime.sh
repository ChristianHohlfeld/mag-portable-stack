#!/usr/bin/env bash
# Run as the owner. Build the pinned CPU runtime or, with "hip", the separate
# runtime for the MI50. Reuse a finished build for the same build profile.
set -euo pipefail
# Stay off the model cores 0-27 while the bots are running.
if [[ -z ${MAG_BUILD_PINNED:-} ]] && taskset -c "${MAG_BUILD_CPUS:-28-55}" true 2>/dev/null; then
  MAG_BUILD_PINNED=1 exec taskset -c "${MAG_BUILD_CPUS:-28-55}" nice -n 19 bash "$0" "$@"
fi
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
# shellcheck source=../host.sh
source "$ROOT/ops/host.sh"
[[ $(id -un) == "$MAG_OWNER" ]] || { echo "run as $MAG_OWNER, not $(id -un)" >&2; exit 4; }
variant=${1:-cpu}
flags=(-DGGML_HIP=OFF)
subdir=
build_profile=
case "$variant" in
  cpu) ;;
  hip)
    ROCM=$(readlink -f "$MAG_IDOL_BASE/rocm-6.3.4/current")
    [[ -x $ROCM/lib/llvm/bin/clang++ ]] || { echo 'ROCm 6.3.4 missing (ops/video/h3/rocm.sh)' >&2; exit 4; }
    export ROCM_PATH="$ROCM" HIP_PATH="$ROCM" HIP_PLATFORM=amd PATH="$ROCM/bin:$PATH"
    subdir=/hip-gfx906
    # Keep non-PIC objects and caches from older builds out of this build.
    build_profile=hip-pic
    flags=(-DGGML_HIP=ON -DGPU_TARGETS=gfx906 -DAMDGPU_TARGETS=gfx906 -DCMAKE_HIP_ARCHITECTURES=gfx906
           -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_HIP_FLAGS=-fPIC
           -DCMAKE_HIP_COMPILER="$ROCM/lib/llvm/bin/clang++" -DCMAKE_PREFIX_PATH="$ROCM;$ROCM/lib/cmake"
           -DCMAKE_BUILD_RPATH="$ROCM/lib" -DCMAKE_INSTALL_RPATH="$ROCM/lib") ;;
  *) exit 4 ;;
esac
REV=$(mag_render "$ROOT/ops/models/models.json" | jq -r '.runtime.rev')
DIR=$(mag_render "$ROOT/ops/models/models.json" | jq -r '.runtime.dir')
[[ $REV =~ ^[a-f0-9]{40}$ && $DIR == "$MAG_OWNER_HOME"/* ]] || exit 4
BIN="$DIR/$REV$subdir/bin/llama-server"
BINDIR=$(dirname "$BIN")
if [[ -x $BIN && $(cat "$BINDIR/source-commit" 2>/dev/null || true) == "$REV" ]] &&
   [[ -z $build_profile || $(cat "$BINDIR/build-profile" 2>/dev/null || true) == "$build_profile" ]]; then
  echo "model runtime already built: $BIN"; exit 0
fi
SRC=$MAG_OWNER_HOME/.local/src/mag-coding-llama.cpp
if [[ $variant == hip ]]; then SRC="$SRC-hip"; fi
BUILD="$SRC/build-mag-models"
if [[ -n $build_profile ]]; then BUILD+="-$build_profile-$REV"; fi
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
  -DBUILD_SHARED_LIBS=OFF -DGGML_VULKAN=OFF -DGGML_CUDA=OFF "${flags[@]}"
cmake --build "$BUILD" -j 8 --target llama-server
"$BUILD/bin/llama-server" --version
mkdir -p "$BINDIR"
install -m 0755 "$BUILD/bin/llama-server" "$BIN.next"
mv -f "$BIN.next" "$BIN"
printf '%s\n' "$REV" > "$BINDIR/source-commit"
if [[ -n $build_profile ]]; then printf '%s\n' "$build_profile" > "$BINDIR/build-profile"; fi
echo "Pinned model runtime built: $BIN"
