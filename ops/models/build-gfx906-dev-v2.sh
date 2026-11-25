#!/usr/bin/env bash
# Explicit experimental build. Never called by deploy or the CPU runtime.
set -euo pipefail
if [[ -z ${MAG_BUILD_PINNED:-} ]] && taskset -c "${MAG_BUILD_CPUS:-28-55}" true 2>/dev/null; then
  MAG_BUILD_PINNED=1 exec taskset -c "${MAG_BUILD_CPUS:-28-55}" nice -n 19 bash "$0" "$@"
fi
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
source "$ROOT/ops/host.sh"
[[ $(id -un) == "$MAG_OWNER" ]] || exit 4
REV=36e84ddb7e5792af6994ee79e12452ba4c918e63
URL=https://github.com/AlwaysLearning-dev/gfx906-llama-cpp.git
PATCH="$ROOT/ops/models/gfx906-dev-v2-shutdown.patch"
ROCM=$(readlink -f "${1:?usage: bash build-gfx906-dev-v2.sh /absolute/path/to/isolated/rocm-7.1.1}")
[[ -x $ROCM/lib/llvm/bin/clang++ && -r $ROCM/.info/version ]] || { echo "ROCm compiler/version missing" >&2; exit 4; }
grep -qE '^7\.1\.1([.-]|$)' "$ROCM/.info/version" || { echo "Use isolated ROCm 7.1.1; do not replace MAG ROCm 6.3.4" >&2; exit 4; }
find "$ROCM/lib/rocblas/library" -iname '*gfx906*' -print -quit | grep -q gfx906 || { echo "rocBLAS lacks gfx906" >&2; exit 4; }
export ROCM_PATH="$ROCM" HIP_PATH="$ROCM" HIP_PLATFORM=amd PATH="$ROCM/bin:$PATH"
DIR=$(mag_render "$ROOT/ops/models/models.json" | jq -r '.runtime.dir')
[[ $DIR == "$MAG_OWNER_HOME"/* ]] || exit 4
OUT="$DIR/$REV/hip-gfx906-dev-v2"
mkdir -p "$OUT"
exec 9>"$OUT/.build.lock"
flock 9
# Fresh worktree per build; no checkout or modification of the normal MAG sources.
WORK=$(mktemp -d "$OUT/.build.XXXXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
git init -q "$WORK/src"
git -C "$WORK/src" remote add origin "$URL"
git -C "$WORK/src" fetch -q --depth=1 origin "$REV"
git -C "$WORK/src" checkout -q --detach FETCH_HEAD
[[ $(git -C "$WORK/src" rev-parse HEAD) == "$REV" ]] || exit 4
git -C "$WORK/src" apply --check "$PATCH"
git -C "$WORK/src" apply "$PATCH"
cmake -S "$WORK/src" -B "$WORK/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DGGML_HIP=ON -DAMDGPU_TARGETS=gfx906 -DGPU_TARGETS=gfx906 -DCMAKE_HIP_ARCHITECTURES=gfx906 \
  -DCMAKE_HIP_COMPILER="$ROCM/lib/llvm/bin/clang++" -DCMAKE_PREFIX_PATH="$ROCM;$ROCM/lib/cmake" \
  -DCMAKE_BUILD_RPATH="$ROCM/lib" -DCMAKE_INSTALL_RPATH="$ROCM/lib" \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_HIP_FLAGS=-fPIC -DBUILD_SHARED_LIBS=OFF \
  -DGGML_CUDA_FA_ALL_QUANTS=ON -DGGML_VULKAN=OFF -DGGML_CUDA=OFF \
  -DGGML_CUDA_VEGA_TUNE=ON -DGGML_CUDA_VEGA_TUNE_FATTN=ON -DGGML_CUDA_VEGA_TUNE_MMQ=OFF \
  -DGGML_CUDA_VEGA_TUNE_TOPK=ON -DGGML_CUDA_VEGA_TUNE_GRAPHS=ON \
  -DGGML_HIP_NO_VMM=ON -DGGML_HIP_MMQ_MFMA=ON -DGGML_HIP_GRAPHS=ON \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF
cmake --build "$WORK/build" -j "${MAG_BUILD_JOBS:-8}" --target llama-server llama-bench
"$WORK/build/bin/llama-server" --version
"$WORK/build/bin/llama-server" --help > "$WORK/help"
for flag in --device --split-mode --spec-type --spec-draft-device --spec-draft-override-tensor --pipeline-parallel --cache-reuse --slot-save-path --reasoning-format; do
  grep -Fq -- "$flag" "$WORK/help" || { echo "Missing flag $flag" >&2; exit 4; }
done
mkdir -p "$OUT/bin"
# Stage and publish only the experimental binaries, never the production runtime.
for binary in llama-server llama-bench; do
  install -m 0755 "$WORK/build/bin/$binary" "$OUT/bin/$binary.next"
  mv -f "$OUT/bin/$binary.next" "$OUT/bin/$binary"
done
printf '%s\n' "$REV" > "$OUT/bin/source-commit"
sha256sum < "$PATCH" | cut -d' ' -f1 > "$OUT/bin/shutdown-patch-sha256"
printf '%s\n' "$ROCM" > "$OUT/bin/rocm-path"
echo "Experimental runtime built: $OUT/bin (NOT deployed)"
