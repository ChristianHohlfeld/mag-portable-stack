#!/bin/bash
# stable-diffusion.cpp for HyperFlow H3 Flash on the MI50: upstream at the
# commit Video Rebirth's patch is made for, with that patch (the two-time
# embedder, low-rank layers, the six-step schedule stored in the GGUF). C++,
# no ComfyUI. Builds on cores 28-55 only, under the CPU lock. One path:
# ggml's HIP backend for gfx906, against the ROCm unpacked by rocm.sh (its
# int8 dp4a matrix kernels, the gfx906 counterpart of the workflow's INT8 path,
# and its fused rope/modulate kernels), with our own attention for gfx906
# (gfx906-attention.patch) -> ~/idol-mi50/h3/sd-cli-hip.
set -e
HERE=$(dirname "$(readlink -f "$0")")
BASE=${MAG_IDOL_BASE:-$HOME/idol-mi50}/h3
COMMIT=3be23788fc0c9f35a2c24b1211caae11d4a54dbc
PATCH_URL=https://raw.githubusercontent.com/Video-Rebirth/hyperflow-h3-flash/0767e9cd7c72bf7aa832b07ebe1cdbd5daa80295/integrations/stable-diffusion.cpp/hyperflow-two-time.patch
PATCH_SHA=607f1a5b43cf8f34c4319c255961b560f1ea740bea4857202b978dcb49d866fd
mkdir -p "$BASE"; cd "$BASE"
exec 9>"$BASE/.build.lock"
flock -n 9 || { echo 'another build is running' >&2; exit 1; }
[ -d src/.git ] || git clone -q https://github.com/leejet/stable-diffusion.cpp src
cd src
git checkout -q -f "$COMMIT"
git submodule update -q --init --recursive ggml thirdparty/libwebm thirdparty/libwebp
curl -fsSL -o ../hyperflow-two-time.patch "$PATCH_URL"
echo "$PATCH_SHA  ../hyperflow-two-time.patch" | sha256sum --quiet -c -
git clean -fdq -e build-hip
git apply ../hyperflow-two-time.patch
# Our own: Sage-style attention for gfx906 (int8 Q*K^T by v_dot4, P*V by
# v_dot2_f32_f16, online softmax in fp32) behind ggml_sage_attn.
git apply "$HERE/gfx906-attention.patch"
CPU="-DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON"
ROCM=$(readlink -f "${MAG_IDOL_BASE:-$HOME/idol-mi50}/rocm-6.3.4/current")
[ -x "$ROCM/lib/llvm/bin/clang++" ] || { echo 'ROCm missing: run rocm.sh first' >&2; exit 2; }
export ROCM_PATH="$ROCM" HIP_PATH="$ROCM" HIP_PLATFORM=amd PATH="$ROCM/bin:$PATH"
cmake -S . -B build-hip -G Ninja -DCMAKE_BUILD_TYPE=Release -DSD_HIPBLAS=ON -DGPU_TARGETS=gfx906 -DAMDGPU_TARGETS=gfx906 \
  -DCMAKE_HIP_COMPILER="$ROCM/lib/llvm/bin/clang++" -DCMAKE_PREFIX_PATH="$ROCM;$ROCM/lib/cmake" -DHIP_PLATFORM=amd \
  -DCMAKE_BUILD_RPATH="$ROCM/lib" -DCMAKE_INSTALL_RPATH="$ROCM/lib" $CPU
flock /tmp/mag-cpu-heavy.lock taskset -c 28-55 cmake --build build-hip -j 28 --target sd-cli
install -m 0755 build-hip/bin/sd-cli "$BASE/sd-cli-hip.part" && mv "$BASE/sd-cli-hip.part" "$BASE/sd-cli-hip"
echo built "$COMMIT" + hyperflow patch for HIP gfx906
