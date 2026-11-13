#!/bin/bash
# stable-diffusion.cpp for HyperFlow H3 Flash on the MI50: upstream at the
# commit Video Rebirth's patch is made for, with that patch (the two-time
# embedder, low-rank layers, the six-step schedule stored in the GGUF), built
# for ggml's Vulkan backend (RADV; gfx906 has no bf16, FP8 or the NVIDIA
# kernels the INT8 path needs). C++, no ComfyUI. Builds on cores 28-55 only,
# under the CPU lock; the result is ~/idol-mi50/h3/sd-cli.
set -e
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
git clean -fdq -e build
git apply ../hyperflow-two-time.patch
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DSD_VULKAN=ON -DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON
flock /tmp/mag-cpu-heavy.lock taskset -c 28-55 cmake --build build -j 28 --target sd-cli
install -m 0755 build/bin/sd-cli "$BASE/sd-cli.part" && mv "$BASE/sd-cli.part" "$BASE/sd-cli"
echo built "$COMMIT" + hyperflow patch
