#!/usr/bin/env bash
# Speech layer of a deploy: builds mag-stt against the pinned recognizer
# (ops/stt/bootstrap.sh), installs it with its model and unit and restarts the
# service only when one of them changed. Optional: without the recognizer the
# room simply shows no microphone.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'root required' >&2; exit 1; }
SOURCE="$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)"
owner=$(stat -c %U "$SOURCE")
home=$(getent passwd "$owner" | cut -d: -f6)
SRC="${MAG_STT_CACHE:-$home/.cache/mag-stack}/transcribe.cpp"
BUILD="${MAG_STT_CACHE:-$home/.cache/mag-stack}/transcribe-build"
MODEL="${MAG_STT_MODELS:-$home/models/stt}/parakeet-primeline-Q8_0.gguf"
INSTALLED=/var/lib/mag-stack/models/parakeet-primeline-Q8_0.gguf
UNIT=/etc/systemd/system/mag-stt.service
as_owner() { if [[ $owner == root ]]; then "$@"; else sudo -u "$owner" -H "$@"; fi; }
read -r _ REVISION < "$SOURCE/ops/stt/transcribe.lock"
if [[ ! -f $BUILD/src/libtranscribe.a || ! -r $MODEL || "$(as_owner git -C "$SRC" rev-parse HEAD 2>/dev/null)" != "$REVISION" ]]; then
  echo 'speech: recognizer or model missing; microphone stays off (run ops/stt/bootstrap.sh)' >&2
  exit 0
fi
as_owner cmake -S "$SOURCE" -B "$SOURCE/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DMAG_TRANSCRIBE_SRC="$SRC" -DMAG_TRANSCRIBE_BUILD="$BUILD" >/dev/null
as_owner ninja -C "$SOURCE/build" -j"${MAG_BUILD_JOBS:-12}" mag-stt

changed=0
install -d -m 0755 /opt/mag-stt/bin
if ! cmp -s "$SOURCE/build/mag-stt" /opt/mag-stt/bin/mag-stt; then
  install -m 0755 "$SOURCE/build/mag-stt" /opt/mag-stt/bin/.mag-stt.next
  mv -f /opt/mag-stt/bin/.mag-stt.next /opt/mag-stt/bin/mag-stt
  changed=1
fi
install -d -o magbot -g magbot -m 0750 /var/lib/mag-stack/models
if [[ ! -f $INSTALLED || $(stat -c %s "$INSTALLED") != $(stat -c %s "$MODEL") ]]; then
  install -o magbot -g magbot -m 0640 "$MODEL" "$INSTALLED.next"
  mv -f "$INSTALLED.next" "$INSTALLED"
  changed=1
fi
if ! cmp -s "$SOURCE/ops/stt/mag-stt.service" "$UNIT"; then
  install -m 0644 "$SOURCE/ops/stt/mag-stt.service" "$UNIT"
  systemctl daemon-reload
  changed=1
fi
systemctl enable mag-stt.service >/dev/null 2>&1 || :
if (( changed )) || ! systemctl is-active --quiet mag-stt.service; then
  systemctl restart mag-stt.service
fi
for _ in $(seq 1 40); do
  if curl --noproxy '*' -fsS --max-time 2 -o /dev/null http://127.0.0.1:8194/health 2>/dev/null; then
    echo 'speech: mag-stt is ready'
    exit 0
  fi
  sleep 0.5
done
echo 'speech: mag-stt does not answer (journalctl -u mag-stt)' >&2
exit 1
