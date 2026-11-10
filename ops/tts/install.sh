#!/usr/bin/env bash
# Voice layer of a deploy: puts the verified voices (ops/tts/bootstrap.sh)
# under /opt/mag-tts in a folder named after their content, so browsers keep
# them for good, and says in config.json which folder it is. Nothing runs on
# the server: the room only hands the files out. Optional: without the voices
# "Vorlesen" uses the voice of the device.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'root required' >&2; exit 1; }
SOURCE="$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)"
owner=$(stat -c %U "$SOURCE")
home=$(getent passwd "$owner" | cut -d: -f6)
CACHED="${MAG_TTS_CACHE:-$home/.cache/mag-stack/tts}/voices"
LOCK="$SOURCE/ops/tts/assets.lock"
name="v-$(sha256sum < "$LOCK" | cut -c1-12)"
if [[ ! -f /opt/mag-tts/$name/tts-worker.js ]]; then
  if [[ ! -d $CACHED ]] || ! (cd "$CACHED" && sha256sum --quiet -c "$LOCK" >/dev/null 2>&1); then
    echo 'voices: not fetched; "Vorlesen" uses the device voice (run ops/tts/bootstrap.sh)' >&2
    exit 0
  fi
  install -d -m 0755 /opt/mag-tts
  rm -rf -- "/opt/mag-tts/.$name.next"
  install -d -m 0755 "/opt/mag-tts/.$name.next"
  while read -r _ file; do install -m 0644 "$CACHED/$file" "/opt/mag-tts/.$name.next/$file"; done < "$LOCK"
  (cd "/opt/mag-tts/.$name.next" && sha256sum --quiet -c "$LOCK")
  mv "/opt/mag-tts/.$name.next" "/opt/mag-tts/$name"
fi
cat > /opt/mag-tts/.config.next <<JSON
{"dir":"$name","voices":[
 {"id":"thorsten","label":"Thorsten (männlich)","model":"de_DE-thorsten-medium.onnx","license":"CC0"},
 {"id":"kerstin","label":"Kerstin (weiblich)","model":"de_DE-kerstin-low.onnx","license":"CC0"},
 {"id":"ramona","label":"Ramona (weiblich)","model":"de_DE-ramona-low.onnx","license":"M-AILABS"},
 {"id":"karlsson","label":"Karlsson (männlich)","model":"de_DE-karlsson-low.onnx","license":"M-AILABS"}],
 "defaults":{"user":"thorsten","lena":"kerstin","noah":"karlsson"}}
JSON
chmod 0644 /opt/mag-tts/.config.next
mv -f /opt/mag-tts/.config.next /opt/mag-tts/config.json
echo "voices: installed in /opt/mag-tts/$name"
