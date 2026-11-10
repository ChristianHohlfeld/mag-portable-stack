#!/usr/bin/env bash
# The voices the room reads aloud with, fetched once (run as the user that
# owns the path ops/tts/install.sh reads, chris on MAG):
#   ~/.cache/mag-stack/tts/voices/   everything ops/tts/assets.lock names
# Read-aloud runs in the browser: Piper VITS voices in onnxruntime-web, with
# espeak-ng phonemes from piper-wasm, driven by ops/tts/tts-worker.js. Every
# file is pinned by SHA256 (assets.lock is the staged result, the table below
# the downloads). Idempotent: a verified folder is left alone.
#   onnxruntime-web 1.30.0 (MIT), @diffusionstudio/piper-wasm 1.0.0 (MIT;
#   espeak-ng data GPL-3.0, phonemes only, shipped unmodified)
#   de_DE-thorsten-medium, de_DE-kerstin-low (CC0)
#   de_DE-karlsson-low, de_DE-ramona-low (M-AILABS, permissive)
#   bash ops/tts/bootstrap.sh [--verify-only]
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)"
OUT="${MAG_TTS_CACHE:-$HOME/.cache/mag-stack/tts}/voices"
verified() { [[ -d $OUT ]] && (cd "$OUT" && sha256sum --quiet -c "$HERE/assets.lock" >/dev/null 2>&1); }
if verified; then
  echo "voices already fetched and verified in $OUT"
  exit 0
fi
[[ ${1:-} != --verify-only ]] || { echo "the voices are missing or do not match ops/tts/assets.lock" >&2; exit 1; }
HF=https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE
declare -A URL=(
  [ort.tgz]=https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-1.30.0.tgz
  [piper.tgz]=https://registry.npmjs.org/@diffusionstudio/piper-wasm/-/piper-wasm-1.0.0.tgz
  [de_DE-thorsten-medium.onnx]=$HF/thorsten/medium/de_DE-thorsten-medium.onnx
  [de_DE-thorsten-medium.onnx.json]=$HF/thorsten/medium/de_DE-thorsten-medium.onnx.json
  [de_DE-kerstin-low.onnx]=$HF/kerstin/low/de_DE-kerstin-low.onnx
  [de_DE-kerstin-low.onnx.json]=$HF/kerstin/low/de_DE-kerstin-low.onnx.json
  [de_DE-karlsson-low.onnx]=$HF/karlsson/low/de_DE-karlsson-low.onnx
  [de_DE-karlsson-low.onnx.json]=$HF/karlsson/low/de_DE-karlsson-low.onnx.json
  [de_DE-ramona-low.onnx]=$HF/ramona/low/de_DE-ramona-low.onnx
  [de_DE-ramona-low.onnx.json]=$HF/ramona/low/de_DE-ramona-low.onnx.json
)
declare -A SHA=(
  [ort.tgz]=d2228df7e4616bc3348bf504ee888f3bec43789a273f0a63f3e68d203ce3bf71
  [piper.tgz]=e5be7f15b3ec42b09fca3616db2ce3e2cf83faacc9c291c400ccf48a9d85c803
)
mkdir -p "$(dirname "$OUT")"
exec 9>"$(dirname "$OUT")/.download.lock"
flock -n 9 || { echo 'another download of the voices is running' >&2; exit 1; }
TMP="$(mktemp -d "$(dirname "$OUT")/.fetch.XXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
mkdir "$TMP/stage"
for f in "${!URL[@]}"; do
  curl --fail --location --retry 3 --silent --show-error --proto '=https' --proto-redir '=https' -o "$TMP/$f" "${URL[$f]}"
  if [[ -n ${SHA[$f]:-} ]]; then printf '%s  %s\n' "${SHA[$f]}" "$TMP/$f" | sha256sum --quiet -c -; else mv "$TMP/$f" "$TMP/stage/$f"; fi
done
tar xzf "$TMP/ort.tgz" -C "$TMP" package/dist/ort.wasm.min.js package/dist/ort-wasm-simd-threaded.wasm package/dist/ort-wasm-simd-threaded.mjs
mv "$TMP"/package/dist/{ort.wasm.min.js,ort-wasm-simd-threaded.wasm,ort-wasm-simd-threaded.mjs} "$TMP/stage/"
rm -rf -- "$TMP/package"
tar xzf "$TMP/piper.tgz" -C "$TMP" package/build/piper_phonemize.js package/build/piper_phonemize.wasm package/build/piper_phonemize.data
mv "$TMP"/package/build/piper_phonemize.{js,wasm,data} "$TMP/stage/"
cp "$HERE/tts-worker.js" "$TMP/stage/"
# Only a complete, verified folder is ever put in place.
(cd "$TMP/stage" && sha256sum --quiet -c "$HERE/assets.lock")
rm -rf -- "$OUT"
mv "$TMP/stage" "$OUT"
echo "voices fetched and verified in $OUT"
