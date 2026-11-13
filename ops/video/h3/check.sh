#!/bin/bash
# Checks our gfx906 kernels against exact math on the CPU, on small random
# tensors (seconds on the card, no model): builds check/*.cpp against the
# unpacked ROCm and runs each on the MI50 only when Idol's line is idle,
# holding the MI50's one lock. Exits non-zero on the first kernel that is off.
set -e
HERE=$(dirname "$(readlink -f "$0")")
BASE=${MAG_IDOL_BASE:-$HOME/idol-mi50}
ROCM=$(readlink -f "$BASE/rocm-6.3.4/current")
SRC=$BASE/h3/src/src/core/cuda
GGML=$BASE/h3/src/ggml/src/ggml-cuda
OUT=$BASE/h3/check; mkdir -p "$OUT"
for test in "$HERE"/check/*.cpp; do
  name=$(basename "$test" .cpp)
  taskset -c 28-55 "$ROCM/lib/llvm/bin/clang++" -x hip --offload-arch=gfx906 --rocm-path="$ROCM" -O3 -ffast-math -std=c++17 \
    -I "$SRC" -I "$GGML" -o "$OUT/$name" "$test" -L "$ROCM/lib" -lamdhip64 -Wl,-rpath,"$ROCM/lib"
done
# Checks without the card (.cc): built with the host compiler and run here.
for test in "$HERE"/check/*.cc; do
  [ -f "$test" ] || continue
  name=$(basename "$test" .cc)
  g++ -std=c++17 -O1 -I "$BASE/h3/src/src" -I "$BASE/h3/src/ggml/include" -o "$OUT/cpu-$name" "$test"
  echo "== $name (CPU)"
  mkdir -p "$OUT/tmp" && "$OUT/cpu-$name" "$OUT/tmp"
done
curl -s -H 'X-Mag-Idol-UI: 1' http://127.0.0.1:8793/api/idol/health | grep -q '"active": *0' || { echo 'Idol is busy; try later' >&2; exit 3; }
for test in "$OUT"/*; do
  [ -x "$test" ] && [ -f "$test" ] || continue
  case $(basename "$test") in cpu-*) continue;; esac
  echo "== $(basename "$test")"
  sudo -n flock /var/lib/mag-idol/gpu.lock systemd-run --quiet --wait --pipe -p User="$(id -un)" -p Group="$(id -gn)" \
    -p SupplementaryGroups="render video" -p AllowedCPUs=28-55 -p MemoryMax=8G -p RuntimeMaxSec=120 "$test"
done
