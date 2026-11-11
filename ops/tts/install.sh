#!/usr/bin/env bash
# Voice layer of a deploy: puts what ops/tts/bootstrap.sh fetched under
# /opt/mag-tts, in folders named after their content, so browsers keep them
# for good, and says in config.json which folders they are. The model (about
# 200 MB, weights in float16) has a folder of its own: it stays the same when the worker
# changes, so no browser fetches it twice. Nothing runs on the server: the
# room only hands the files out. Optional: without the model "Vorlesen" uses
# the voice of the device.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'root required' >&2; exit 1; }
SOURCE="$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)"
owner=$(stat -c %U "$SOURCE")
home=$(getent passwd "$owner" | cut -d: -f6)
CACHED="${MAG_TTS_CACHE:-$home/.cache/mag-stack/tts}/supertonic3-f16"
LOCK="$SOURCE/ops/tts/assets.lock"
MODELS="$SOURCE/ops/tts/models.lock"
name="v-$(sha256sum < "$LOCK" | cut -c1-12)"
model="m-$(sha256sum < "$MODELS" | cut -c1-12)"
# The runtime (onnxruntime-web, ~27 MB) without our worker: a folder of its own, so a change of the
# worker does not send it to every browser again.
RUNTIME_LOCK=$(mktemp)
trap 'rm -f "$RUNTIME_LOCK"' EXIT
grep -v ' tts-worker.js$' "$LOCK" > "$RUNTIME_LOCK"
runtime="r-$(sha256sum < "$RUNTIME_LOCK" | cut -c1-12)"
if [[ ! -d $CACHED ]] || ! (cd "$CACHED" && sha256sum --quiet -c "$MODELS" >/dev/null 2>&1 && grep -v ' tts-worker.js$' "$LOCK" | sha256sum --quiet -c - >/dev/null 2>&1); then
  if [[ ! -f /opt/mag-tts/$name/tts-worker.js || ! -f /opt/mag-tts/$model/vocoder.f16.data ]]; then
    echo 'voices: model not fetched; "Vorlesen" keeps what it has (run ops/tts/bootstrap.sh)' >&2
    exit 0
  fi
fi
install -d -m 0755 /opt/mag-tts
# Puts the files of a lock into a folder, complete and verified, or not at all.
place() { # folder lock
  [[ -f /opt/mag-tts/$1/.complete ]] && return 0
  rm -rf -- "/opt/mag-tts/.$1.next"
  install -d -m 0755 "/opt/mag-tts/.$1.next"
  while read -r _ file; do
    if [[ $file == tts-worker.js ]]; then from="$SOURCE/ops/tts/$file"; else from="$CACHED/$file"; fi
    [[ -f $from ]] || from="/opt/mag-tts/$name/$file"     # the runtime, from the worker's folder
    install -m 0644 "$from" "/opt/mag-tts/.$1.next/$file"
  done < "$2"
  (cd "/opt/mag-tts/.$1.next" && sha256sum --quiet -c "$2")
  touch "/opt/mag-tts/.$1.next/.complete"
  rm -rf -- "/opt/mag-tts/$1"          # a folder of this name without its mark was never complete
  mv "/opt/mag-tts/.$1.next" "/opt/mag-tts/$1"
}
place "$model" "$MODELS"
place "$name" "$LOCK"
place "$runtime" "$RUNTIME_LOCK"
# Folders of earlier versions stay: voice models are never deleted.
cat > /opt/mag-tts/.config.next <<JSON
{"dir":"$name","models":"$model","runtime":"$runtime","voices":[
 {"id":"f1","label":"Frau 1","model":"F1.json"},{"id":"f2","label":"Frau 2","model":"F2.json"},
 {"id":"f3","label":"Frau 3","model":"F3.json"},{"id":"f4","label":"Frau 4","model":"F4.json"},
 {"id":"f5","label":"Frau 5","model":"F5.json"},{"id":"m1","label":"Mann 1","model":"M1.json"},
 {"id":"m2","label":"Mann 2","model":"M2.json"},{"id":"m3","label":"Mann 3","model":"M3.json"},
 {"id":"m4","label":"Mann 4","model":"M4.json"},{"id":"m5","label":"Mann 5","model":"M5.json"}],
 "defaults":{"user":"m1","lena":"f1","noah":"m2"}}
JSON
chmod 0644 /opt/mag-tts/.config.next
mv -f /opt/mag-tts/.config.next /opt/mag-tts/config.json
echo "voices: installed in /opt/mag-tts/$name (model in $model)"
