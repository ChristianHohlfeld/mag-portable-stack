#!/usr/bin/env bash
# Build the dedicated GLM-Q2 ik_llama.cpp runtime. This script only builds and
# publishes a versioned binary; it never starts, stops, or signals a model.
set -euo pipefail

REF=8d21687cc7358de10264d4180ba9b863024d5c4
URL=${IK_URL:-https://github.com/ikawrakow/ik_llama.cpp.git}
SRC=${IK_GLM_SRC:-/home/chris/src/ik_llama.cpp-glm}
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
PATCH_DIR="$ROOT/ops/glm/ik"
PATCHES=("$PATCH_DIR/0001-glm5next-legacy-6block-hparams.patch")
phash=$(sha256sum "${PATCHES[@]}" | awk '{print $1}' | sha256sum | cut -c1-12)
DEST=${IK_GLM_DEST:-/home/chris/.local/glm-ik/${REF:0:10}-mag-$phash}
BUILD="$SRC/build-mag-glm"
JOBS=${JOBS:-$(( $(nproc) / 2 ))}
(( JOBS > 0 )) || JOBS=1

[[ $(id -un) == chris || ${IK_ALLOW_USER:-} == 1 ]] || {
  echo "run as chris (or set IK_ALLOW_USER=1)" >&2; exit 1;
}
for tool in git cmake ninja gcc g++ sha256sum; do
  command -v "$tool" >/dev/null || { echo "missing build dependency: $tool" >&2; exit 1; }
done

install -d "$(dirname "$SRC")"
if [[ ! -d "$SRC/.git" ]]; then
  git clone --filter=blob:none "$URL" "$SRC" >&2
fi
git -C "$SRC" fetch --force origin "$REF" >&2
git -C "$SRC" cat-file -e "$REF^{commit}"
git -C "$SRC" checkout --quiet --detach "$REF"
for patch in "${PATCHES[@]}"; do
  [[ -f "$patch" ]] || { echo "missing explicit patch: $patch" >&2; exit 1; }
  echo "Applying $(basename "$patch")" >&2
  git -C "$SRC" apply --check "$patch"
  git -C "$SRC" apply "$patch"
done

CC=gcc CXX=g++ cmake -S "$SRC" -B "$BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS=-g -DCMAKE_CXX_FLAGS=-g \
  -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=ON \
  -DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_METAL=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF >&2
nice -n 19 ionice -c 3 ninja -C "$BUILD" -j "$JOBS" llama-server >&2

BIN="$BUILD/bin/llama-server"
[[ -x "$BIN" ]] || { echo "llama-server build output missing" >&2; exit 1; }
install -d "$DEST/bin"
install -m 0755 "$BIN" "$DEST/bin/llama-server.new"
mv -f "$DEST/bin/llama-server.new" "$DEST/bin/llama-server"
printf '%s\n' "$REF" > "$DEST/source-commit"
printf '%s\n' "$phash" > "$DEST/patch-identity"
sha256sum "$DEST/bin/llama-server" > "$DEST/SHA256SUMS"
{
  echo "engine=ik_llama.cpp"
  echo "source_ref=$REF"
  echo "source_commit=$(git -C "$SRC" rev-parse HEAD)"
  echo "patch_sha256=$phash"
  echo "built_at=$(date -Is)"
  echo "builder=$(id -un)"
  gcc --version | head -1
  g++ --version | head -1
  grep -E 'GGML_NATIVE:BOOL|GGML_AVX2|GGML_FMA|CMAKE_BUILD_TYPE:STRING|CMAKE_C_COMPILER:FILEPATH|CMAKE_CXX_COMPILER:FILEPATH' "$BUILD/CMakeCache.txt" || true
  cat "$DEST/SHA256SUMS"
  echo "patches:"
  printf '  %s\n' "${PATCHES[@]}"
} > "$DEST/BUILD-INFO.txt"

echo "$DEST/bin/llama-server"
