#!/usr/bin/env bash
# What the room reads aloud with, fetched once (run as the user that owns the
# path ops/tts/install.sh reads, chris on MAG):
#   ~/.cache/mag-stack/tts/supertonic3/   everything the two lock files name
# Read-aloud runs in the browser: Supertonic 3 (four ONNX networks and ten
# voice styles) in onnxruntime-web, on the device's GPU (WebGPU) or in WASM,
# driven by ops/tts/tts-worker.js. Every file is pinned by SHA256: the model
# in models.lock (its folder never changes, so a browser fetches it once),
# the runtime and our worker in assets.lock. Idempotent: a verified folder
# is left alone. The Piper voices of before stay where they are.
#   onnxruntime-web 1.30.0 (MIT)
#   Supertone/supertonic-3 at 3cadd1ee (weights OpenRAIL-M, see its LICENSE;
#   German is one of its 31 languages)
#   bash ops/tts/bootstrap.sh [--verify-only]
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)"
OUT="${MAG_TTS_CACHE:-$HOME/.cache/mag-stack/tts}/supertonic3"
verified() {
  [[ -d $OUT ]] || return 1
  (cd "$OUT" && sha256sum --quiet -c "$HERE/models.lock" >/dev/null 2>&1 && grep -v ' tts-worker.js$' "$HERE/assets.lock" | sha256sum --quiet -c - >/dev/null 2>&1)
}
if verified; then
  echo "model and runtime already fetched and verified in $OUT"
  exit 0
fi
[[ ${1:-} != --verify-only ]] || { echo "the model is missing or does not match ops/tts/models.lock" >&2; exit 1; }
HF=https://huggingface.co/Supertone/supertonic-3/resolve/3cadd1ee6394adea1bd021217a0e650ede09a323
ORT=https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-1.30.0.tgz
ORT_SHA=d2228df7e4616bc3348bf504ee888f3bec43789a273f0a63f3e68d203ce3bf71
mkdir -p "$(dirname "$OUT")"
exec 9>"$(dirname "$OUT")/.download.lock"
flock -n 9 || { echo 'another download of the model is running' >&2; exit 1; }
TMP="$(mktemp -d "$(dirname "$OUT")/.fetch.XXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
mkdir "$TMP/stage"
fetch() { curl --fail --location --retry 3 --silent --show-error --proto '=https' --proto-redir '=https' -o "$2" "$1"; }
while read -r _ file; do
  case $file in
    *.onnx|tts.json|unicode_indexer.json) from=onnx/$file ;;
    [FM][1-5].json) from=voice_styles/$file ;;
    *) from=$file ;;
  esac
  fetch "$HF/$from" "$TMP/stage/$file"
done < "$HERE/models.lock"
fetch "$ORT" "$TMP/ort.tgz"
printf '%s  %s\n' "$ORT_SHA" "$TMP/ort.tgz" | sha256sum --quiet -c -
tar xzf "$TMP/ort.tgz" -C "$TMP" package/dist/ort.webgpu.min.js package/dist/ort-wasm-simd-threaded.asyncify.mjs package/dist/ort-wasm-simd-threaded.asyncify.wasm
mv "$TMP"/package/dist/{ort.webgpu.min.js,ort-wasm-simd-threaded.asyncify.mjs,ort-wasm-simd-threaded.asyncify.wasm} "$TMP/stage/"
# Only a complete, verified folder is ever put in place.
(cd "$TMP/stage" && sha256sum --quiet -c "$HERE/models.lock" && grep -v ' tts-worker.js$' "$HERE/assets.lock" | sha256sum --quiet -c -)
if [[ -d $OUT ]]; then mv "$OUT" "$OUT.$(date -u +%Y%m%dT%H%M%SZ).old"; fi   # nothing fetched before is deleted
mv "$TMP/stage" "$OUT"
echo "model and runtime fetched and verified in $OUT"
