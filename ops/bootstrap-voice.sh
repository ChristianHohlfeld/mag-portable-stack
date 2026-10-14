#!/usr/bin/env bash
set -euo pipefail
SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
read -r TAG REVISION < "$SOURCE/ops/whisper.lock"
CACHE="${MAG_VOICE_CACHE:-$HOME/.cache/mag-stack}"
MODEL_DIR="${MAG_VOICE_MODELS:-$HOME/models/whisper}"
mkdir -p "$CACHE" "$MODEL_DIR"
if [[ ! -d "$CACHE/whisper.cpp/.git" ]]; then git clone --depth 1 --branch "$TAG" https://github.com/ggml-org/whisper.cpp.git "$CACHE/whisper.cpp"; fi
[[ "$(git -C "$CACHE/whisper.cpp" rev-parse HEAD)" == "$REVISION" ]] || { echo 'Whisper revision mismatch' >&2; exit 1; }
cmake -S "$CACHE/whisper.cpp" -B "$CACHE/whisper-build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF -DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF
cmake --build "$CACHE/whisper-build" --target whisper-cli -j 8
if [[ ! -s "$MODEL_DIR/ggml-large-v3-turbo.bin" ]]; then
 curl -fL https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin -o "$MODEL_DIR/ggml-large-v3-turbo.bin.part"
 mv "$MODEL_DIR/ggml-large-v3-turbo.bin.part" "$MODEL_DIR/ggml-large-v3-turbo.bin"
fi
