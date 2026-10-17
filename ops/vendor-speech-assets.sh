#!/usr/bin/env bash
# Build-only/offline-at-runtime Moonshine German Small Streaming assets.
# Requires node/npm, curl, tar, sha256sum, python3; never ships runtime CDN URLs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/build/speech-assets}"
# Reuse a verified local snapshot on ordinary deploys; preparing a release
# must not depend on the network once the pinned assets are staged.
if [[ "${MAG_SPEECH_REFRESH:-0}" != 1 && -f "$OUT/speech-config.json" ]]; then
  if python3 - "$OUT" <<'PY'
import json, pathlib, re, sys
out = pathlib.Path(sys.argv[1])
try:
    cfg = json.loads((out / "speech-config.json").read_text())
    assert cfg["arch"] == 4
    assert all(re.fullmatch(r"speech-[0-9a-f]{12}", cfg[k])
               for k in ("model", "runtime"))
    for f in ("index.js", "stt-worker.js", "moonshine.mjs", "moonshine.wasm"):
        assert (out / cfg["runtime"] / f).stat().st_size > 0
    lengths = {"adapter.ort":2869296,"cross_kv.ort":5358752,
       "decoder_kv.ort":61314512,"encoder.ort":44358376,
       "frontend.model.ort":26776,"frontend.weights.ort":7769280,
       "streaming_config.json":512,"tokenizer.bin":103319}
    for f, size in lengths.items():
        assert (out / cfg["model"] / f).stat().st_size == size
except (OSError, KeyError, ValueError, AssertionError):
    sys.exit(1)
PY
  then
    echo 'Reusing staged Moonshine German WASM/model assets'
    exit 0
  fi
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/runtime" "$TMP/model" "$OUT"
npm pack '@moonshine-ai/moonshine-wasm@0.1.5' --pack-destination "$TMP" --silent >/dev/null
tar -xzf "$TMP/moonshine-ai-moonshine-wasm-0.1.5.tgz" -C "$TMP"
cp -a "$TMP/package/dist/." "$TMP/runtime/"
for f in index.js stt-worker.js moonshine.mjs moonshine.wasm; do
  test -s "$TMP/runtime/$f" || { echo "Missing Moonshine runtime file: $f" >&2; exit 1; }
done
# Pinned quantized 2026-08-24 German checkpoint and upstream published byte sizes.
BASE='https://download.moonshine.ai/model/small-streaming-de/quantized_26_08_24'
FILES=(
  'adapter.ort:2869296'
  'cross_kv.ort:5358752'
  'decoder_kv.ort:61314512'
  'encoder.ort:44358376'
  'frontend.model.ort:26776'
  'frontend.weights.ort:7769280'
  'streaming_config.json:512'
  'tokenizer.bin:103319'
)
for pair in "${FILES[@]}"; do
  name="${pair%%:*}"; expected="${pair#*:}"
  curl --fail --location --retry 3 --retry-delay 2 --silent --show-error \
    "$BASE/$name" --output "$TMP/model/$name"
  actual="$(wc -c < "$TMP/model/$name" | tr -d ' ')"
  test "$actual" = "$expected" || { echo "Invalid model size for $name: $actual != $expected" >&2; exit 1; }
done
hash_dir() {
  (cd "$1"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -c1-12)
}
RUNTIME="speech-$(hash_dir "$TMP/runtime")"
MODEL="speech-$(hash_dir "$TMP/model")"
# Atomic final tree generation. Changing any byte changes the immutable URL.
STAGE="$(mktemp -d "$OUT/.stage.XXXXXXXX")"
trap 'rm -rf "$TMP" "$STAGE"' EXIT
cp -a "$TMP/runtime" "$STAGE/$RUNTIME"
cp -a "$TMP/model" "$STAGE/$MODEL"
python3 - "$RUNTIME" "$MODEL" > "$STAGE/speech-config.json" <<'PY'
import json,sys
print(json.dumps({"runtime":sys.argv[1],"model":sys.argv[2],"arch":4},separators=(',',':')))
PY
cp -a "$STAGE/." "$OUT/"
echo "Moonshine assets prepared: $RUNTIME / $MODEL"
