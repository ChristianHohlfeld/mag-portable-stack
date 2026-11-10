#!/usr/bin/env bash
# Pinned speech-to-text for the room, built and fetched once:
#  - transcribe.cpp (tag + commit from ops/stt/transcribe.lock), CPU only,
#  - the German Parakeet model as GGUF (pinned revision, size and SHA256).
# Run as the user that owns the paths ops/stt/install.sh reads (chris on MAG):
#   ~/.cache/mag-stack/transcribe.cpp, ~/.cache/mag-stack/transcribe-build
#   ~/models/stt/parakeet-primeline-Q8_0.gguf
# Idempotent: a matching build and a verified model are left alone. The model
# has one writer (a lock) and is only ever put in place complete and verified.
#   taskset -c 28-55 bash ops/stt/bootstrap.sh [--verify-only]
set -euo pipefail
SOURCE="$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)"
read -r TAG REVISION < "$SOURCE/ops/stt/transcribe.lock"
[[ $TAG =~ ^v[0-9.]+$ && $REVISION =~ ^[0-9a-f]{40}$ ]] || { echo 'ops/stt/transcribe.lock is malformed' >&2; exit 1; }
# huggingface.co/handy-computer/parakeet-primeline-gguf: primeLine's German
# fine-tune of NVIDIA Parakeet TDT 0.6B v3 (CC-BY-4.0), converted for transcribe.cpp.
MODEL_NAME=parakeet-primeline-Q8_0.gguf
MODEL_URL=https://huggingface.co/handy-computer/parakeet-primeline-gguf/resolve/cf04224dee82c8ad7184e99562680f2926c82bf7/$MODEL_NAME
MODEL_SHA256=dbd1ec2985aea64d16550a0068ea9153c0bc01955bf2a5c18b74ca213546d3cd
MODEL_SIZE=739508640
CACHE="${MAG_STT_CACHE:-$HOME/.cache/mag-stack}"
MODEL_DIR="${MAG_STT_MODELS:-$HOME/models/stt}"
MODEL="$MODEL_DIR/$MODEL_NAME"
SRC="$CACHE/transcribe.cpp"
BUILD="$CACHE/transcribe-build"

model_ok() {
  [[ -f $MODEL && ! -L $MODEL && $(stat -c %s "$MODEL") == "$MODEL_SIZE" ]] || return 1
  printf '%s  %s\n' "$MODEL_SHA256" "$MODEL" | sha256sum -c --quiet - >/dev/null 2>&1
}
build_ok() {
  [[ -d $SRC/.git && -f $BUILD/src/libtranscribe.a && -f $BUILD/ggml/src/libggml-cpu.a ]] || return 1
  [[ "$(git -C "$SRC" rev-parse HEAD)" == "$REVISION" ]]
}
if [[ ${1:-} == --verify-only ]]; then
  build_ok || { echo "transcribe.cpp missing or not at $REVISION" >&2; exit 1; }
  model_ok || { echo "$MODEL missing or SHA256 mismatch" >&2; exit 1; }
  echo "speech OK: transcribe.cpp $TAG ($REVISION), $MODEL_NAME verified"
  exit 0
fi

mkdir -p "$CACHE" "$MODEL_DIR"
if build_ok; then
  echo "transcribe.cpp $TAG already built"
else
  [[ -d $SRC/.git ]] || git clone --depth 1 --branch "$TAG" https://github.com/handy-computer/transcribe.cpp.git "$SRC"
  [[ "$(git -C "$SRC" rev-parse HEAD)" == "$REVISION" ]] || { echo "transcribe.cpp revision mismatch (want $REVISION for $TAG)" >&2; exit 1; }
  # CPU only: the MI50 belongs to MAG Idol. No GPU backend is ever built in.
  cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DTRANSCRIBE_BUILD_TESTS=OFF -DTRANSCRIBE_BUILD_EXAMPLES=OFF \
    -DTRANSCRIBE_VULKAN=OFF -DTRANSCRIBE_CUDA=OFF -DTRANSCRIBE_HIP=OFF -DTRANSCRIBE_USE_SYSTEM_BLAS=OFF
  cmake --build "$BUILD" -j "${MAG_STT_JOBS:-12}"
fi
if model_ok; then
  echo "$MODEL_NAME already verified"
else
  exec 9>"$MODEL_DIR/.download.lock"
  flock -n 9 || { echo 'another download of the speech model is running' >&2; exit 1; }
  curl --fail --location --retry 3 --silent --show-error --proto '=https' --proto-redir '=https' -o "$MODEL.part" "$MODEL_URL"
  [[ $(stat -c %s "$MODEL.part") == "$MODEL_SIZE" ]] || { echo 'speech model: wrong size' >&2; exit 1; }
  printf '%s  %s\n' "$MODEL_SHA256" "$MODEL.part" | sha256sum -c --quiet -
  mv -f "$MODEL.part" "$MODEL"
fi
echo "speech ready: transcribe.cpp $TAG, $MODEL"
