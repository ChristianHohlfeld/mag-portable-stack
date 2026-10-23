#!/usr/bin/env bash
# Pinned speech-to-text for the room: whisper.cpp (tag + commit from
# ops/whisper.lock) and the ggml-large-v3-turbo model (pinned SHA256 below).
# Run as the user that owns the paths deploy.sh reads (chris on MAG):
#   /home/chris/.cache/mag-stack/whisper-build/bin/whisper-cli
#   /home/chris/models/whisper/ggml-large-v3-turbo.bin
# Idempotent: a matching build and a verified model are left alone. A model
# with the wrong hash is never used (download goes to .part, then rename).
#   bash ops/bootstrap-voice.sh [--verify-only]
# Test hosts only (ops/bootstrap.sh --test-stubs): MAG_VOICE_MODEL_STUB=1
# writes a clearly marked placeholder instead of the 1.6 GB model.
set -euo pipefail
SOURCE="$(cd -- "$(dirname -- "$(readlink -f "$0")")/.." && pwd)"
read -r TAG REVISION < "$SOURCE/ops/whisper.lock"
# huggingface.co/ggerganov/whisper.cpp, LFS pointer of ggml-large-v3-turbo.bin
MODEL_NAME=ggml-large-v3-turbo.bin
MODEL_URL=https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODEL_NAME
MODEL_SHA256=1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69
MODEL_SIZE=1624555275
CACHE="${MAG_VOICE_CACHE:-$HOME/.cache/mag-stack}"
MODEL_DIR="${MAG_VOICE_MODELS:-$HOME/models/whisper}"
MODEL="$MODEL_DIR/$MODEL_NAME"
VERIFY_ONLY=0
[[ ${1:-} == --verify-only ]] && VERIFY_ONLY=1
[[ $TAG =~ ^v[0-9.]+$ && $REVISION =~ ^[0-9a-f]{40}$ ]] || { echo 'ops/whisper.lock is malformed' >&2; exit 1; }

model_ok() {
  [[ -f $MODEL && ! -L $MODEL ]] || return 1
  if [[ ${MAG_VOICE_MODEL_STUB:-0} == 1 ]] && grep -qx 'MAG-TEST-STUB whisper model' "$MODEL" 2>/dev/null; then
    return 0
  fi
  [[ $(stat -c %s "$MODEL") == "$MODEL_SIZE" ]] || return 1
  printf '%s  %s\n' "$MODEL_SHA256" "$MODEL" | sha256sum -c --quiet - >/dev/null 2>&1
}
cli_ok() {
  local cli="$CACHE/whisper-build/bin/whisper-cli"
  [[ -x $cli && -d "$CACHE/whisper.cpp/.git" ]] || return 1
  [[ "$(git -C "$CACHE/whisper.cpp" rev-parse HEAD)" == "$REVISION" ]]
}

if (( VERIFY_ONLY )); then
  cli_ok || { echo "whisper-cli missing or not at $REVISION" >&2; exit 1; }
  model_ok || { echo "$MODEL missing or SHA256 mismatch" >&2; exit 1; }
  echo "voice OK: whisper.cpp $TAG ($REVISION), $MODEL_NAME verified"
  exit 0
fi

mkdir -p "$CACHE" "$MODEL_DIR"
if cli_ok; then
  echo "whisper-cli $TAG already built"
else
  if [[ ! -d "$CACHE/whisper.cpp/.git" ]]; then
    git clone --depth 1 --branch "$TAG" https://github.com/ggml-org/whisper.cpp.git "$CACHE/whisper.cpp"
  fi
  [[ "$(git -C "$CACHE/whisper.cpp" rev-parse HEAD)" == "$REVISION" ]] ||
    { echo "Whisper revision mismatch (want $REVISION for $TAG)" >&2; exit 1; }
  cmake -S "$CACHE/whisper.cpp" -B "$CACHE/whisper-build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF -DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF
  cmake --build "$CACHE/whisper-build" --target whisper-cli -j "${MAG_VOICE_JOBS:-8}"
fi

if model_ok; then
  echo "$MODEL_NAME already present and verified"
elif [[ ${MAG_VOICE_MODEL_STUB:-0} == 1 ]]; then
  [[ ! -e $MODEL ]] || { echo "$MODEL exists but is not verified; refusing to replace it with a stub" >&2; exit 1; }
  printf 'MAG-TEST-STUB whisper model\n' > "$MODEL.part"
  mv -f "$MODEL.part" "$MODEL"
  echo "TEST STUB written to $MODEL (speech will not work)"
else
  if [[ -e $MODEL ]]; then
    echo "$MODEL fails the pinned SHA256; downloading a fresh copy" >&2
  fi
  curl -fL --retry 3 --connect-timeout 20 "$MODEL_URL" -o "$MODEL.part"
  printf '%s  %s\n' "$MODEL_SHA256" "$MODEL.part" | sha256sum -c --quiet - ||
    { rm -f "$MODEL.part"; echo "$MODEL_NAME SHA256 mismatch; not installed" >&2; exit 1; }
  mv -f "$MODEL.part" "$MODEL"
  echo "$MODEL_NAME verified ($MODEL_SHA256)"
fi
