#!/usr/bin/env bash
# Build-time vendoring of the browser read-aloud voices (Piper, client side).
# Everything is pinned by SHA-256 and self-hosted under /static/tts-<hash>/;
# no CDN is contacted at runtime. One single-speaker voice per participant,
# chosen per speaker in the room (voice + tempo, /api/tts-voices):
#   Chris (own messages)  de_DE-thorsten-medium  male, CC0
#   Lena                  de_DE-kerstin-low      female, CC0 (studio TTS corpus)
#   Noah                  de_DE-karlsson-low     male, M-AILABS (permissive)
#   selectable            de_DE-ramona-low       female, M-AILABS
# 2026-11-05: the multi-speaker de_DE-mls-medium (audiobook speakers 68/32)
# sounded muffled and uneven next to Thorsten; it is no longer staged. All
# voices are single-speaker recordings with clean German; the worker
# levels every part to the same loudness so the three sound equally present.
# Medium/high tiers of these speakers do not exist; Thorsten high is ~114 MB
# and several times slower on WASM, which delayed the first word.
# Runtime: onnxruntime-web 1.30.0 (MIT) + @diffusionstudio/piper-wasm 1.0.0
# (MIT; espeak-ng data GPL-3.0, phonemization only, shipped unmodified).
#   bash ops/vendor-tts-assets.sh build/tts-assets
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/build/tts-assets}"
if [[ "${MAG_TTS_REFRESH:-0}" != 1 && -f "$OUT/tts-config.json" ]]; then
  dir=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["dir"])' "$OUT/tts-config.json" 2>/dev/null || true)
  # The worker is part of the content hash. A source edit must restage,
  # otherwise a deploy keeps the previous tts-<hash> directory.
  if [[ -n $dir && -s "$OUT/$dir/de_DE-thorsten-medium.onnx" && -s "$OUT/$dir/de_DE-ramona-low.onnx" && -s "$OUT/$dir/de_DE-karlsson-low.onnx" && -s "$OUT/$dir/ort-wasm-simd-threaded.wasm" ]] \
    && cmp -s "$ROOT/mag-room/static/tts-worker.js" "$OUT/$dir/tts-worker.js" \
    && [[ $(cat "$OUT/.vendor-script.sha" 2>/dev/null) == "$(sha256sum < "$0" | cut -c1-64)" ]]; then
    echo "Reusing staged Piper TTS assets ($dir)"; exit 0
  fi
fi
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
  [de_DE-thorsten-medium.onnx]=7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819
  [de_DE-thorsten-medium.onnx.json]=974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85
  [de_DE-kerstin-low.onnx]=d352a7641892cebf2903859af94e9ba81a141110215fe3943bcda7f7da401b7a
  [de_DE-kerstin-low.onnx.json]=56e708556b7b9b7a53c4f8957e021421e69f11a600962bba554cffbe72cf2d47
  [de_DE-karlsson-low.onnx]=f9793bf7e461a472b3916cf260774bfc9812e9eb72601b147ed649d01064e8fe
  [de_DE-karlsson-low.onnx.json]=3b2095adbfeede316f22007a0a09d4bfc25514b7189850740b06800a6cfb9838
  [de_DE-ramona-low.onnx]=a1068592532aad8c49bf3d1a01b8749c11d7dba726689ee28ad64e7b627ae4c5
  [de_DE-ramona-low.onnx.json]=64906ab7a050ac685fa3dcea044baa798f67ed77be164f9c668a69298c6487d2
)
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in "${!URL[@]}"; do
  src="${MAG_TTS_CACHE:-}/$f"
  if [[ -n ${MAG_TTS_CACHE:-} && -s $src ]]; then cp "$src" "$TMP/$f"; else curl -fsSL --retry 3 -o "$TMP/$f" "${URL[$f]}"; fi
  echo "${SHA[$f]}  $TMP/$f" | sha256sum -c --quiet - || { echo "SHA-256 mismatch: $f" >&2; exit 4; }
done
for v in thorsten/medium kerstin/low; do
  curl -fsSL "$HF/$v/MODEL_CARD" | grep -q 'License: CC0' || { echo "license of $v is no longer CC0: check before shipping" >&2; exit 4; }
done
for v in karlsson/low ramona/low; do
  curl -fsSL "$HF/$v/MODEL_CARD" | grep -q 'caito.de/2019/01/03/the-m-ailabs-speech-dataset' || { echo "dataset of $v changed: check its license before shipping" >&2; exit 4; }
done
mkdir -p "$TMP/stage"
tar xzf "$TMP/ort.tgz" -C "$TMP" package/dist/ort.wasm.min.js package/dist/ort-wasm-simd-threaded.wasm package/dist/ort-wasm-simd-threaded.mjs
cp "$TMP"/package/dist/{ort.wasm.min.js,ort-wasm-simd-threaded.wasm,ort-wasm-simd-threaded.mjs} "$TMP/stage/"
rm -rf "$TMP/package"
tar xzf "$TMP/piper.tgz" -C "$TMP"
cp "$TMP"/package/build/piper_phonemize.{js,wasm,data} "$TMP/stage/"
cp "$TMP"/de_DE-*.onnx "$TMP"/de_DE-*.onnx.json "$TMP/stage/"
cp "$ROOT/mag-room/static/tts-worker.js" "$TMP/stage/"
hash=$(cd "$TMP/stage" && sha256sum * | sha256sum | cut -c1-12)
dir="tts-$hash"
mkdir -p "$OUT"; rm -rf "$OUT/$dir"; mv "$TMP/stage" "$OUT/$dir"
cat > "$OUT/tts-config.json" <<JSON
{"dir":"$dir","voices":[
 {"id":"thorsten","label":"Thorsten (männlich)","model":"de_DE-thorsten-medium.onnx","license":"CC0"},
 {"id":"kerstin","label":"Kerstin (weiblich)","model":"de_DE-kerstin-low.onnx","license":"CC0"},
 {"id":"ramona","label":"Ramona (weiblich)","model":"de_DE-ramona-low.onnx","license":"M-AILABS"},
 {"id":"karlsson","label":"Karlsson (männlich)","model":"de_DE-karlsson-low.onnx","license":"M-AILABS"}],
 "defaults":{"user":"thorsten","lena":"kerstin","noah":"karlsson"}}
JSON
# The voice/role table lives in this script: a changed script must restage
# tts-config.json (2026-11-04: #325 roles were skipped by the reuse shortcut).
sha256sum < "$0" | cut -c1-64 > "$OUT/.vendor-script.sha"
echo "Piper TTS assets staged in $OUT/$dir"
