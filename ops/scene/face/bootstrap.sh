#!/usr/bin/env bash
# MediaPipe's face landmarker for the faces of the scenes (ops/scene/face.py),
# fetched once: the wheels and the model named in face.lock (SHA256 and URL
# each; MediaPipe and its model are Apache-2.0), checked, and installed for
# the pinned Blender's Python (3.11) into
#   ~/.cache/mag-stack/face/site and ~/.cache/mag-stack/face/face_landmarker.task
# Run as the user that owns that path (chris on MAG); ops/scene/install.sh
# copies a verified set to /opt/mag-scene/face. Without it a face is found
# from the shape of the head alone. Idempotent; one writer (a lock).
#   bash ops/scene/face/bootstrap.sh
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)"
LOCK="$HERE/face.lock"
CACHE="${MAG_SCENE_CACHE:-$HOME/.cache/mag-stack}/face"
want=$(sha256sum "$LOCK" | cut -d' ' -f1)
if [[ "$(cat "$CACHE/.mag-verified" 2>/dev/null)" == "$want" ]]; then
  echo 'face: already installed and verified'
  exit 0
fi
mkdir -p "$CACHE/files"
exec 9>"$CACHE/.download.lock"
flock -n 9 || { echo 'face: another download is running' >&2; exit 1; }
wheels=()
while read -r sum url; do
  [[ $sum =~ ^[0-9a-f]{64}$ && $url == https://* ]] || { echo "face.lock: bad line" >&2; exit 1; }
  file="$CACHE/files/$(basename "$url")"
  if ! printf '%s  %s\n' "$sum" "$file" | sha256sum -c --quiet - >/dev/null 2>&1; then
    curl --fail --location --retry 3 --silent --show-error --proto '=https' --proto-redir '=https' -o "$file.part" "$url"
    printf '%s  %s\n' "$sum" "$file.part" | sha256sum -c --quiet -
    mv -f "$file.part" "$file"
  fi
  [[ $file == *.whl ]] && wheels+=("$file")
done < "$LOCK"
# Installed beside the old set and swapped, so a half-installed set is never used.
fresh="$CACHE/.site.next"
rm -rf -- "$fresh"
python3 -m pip install --quiet --no-deps --no-index --target "$fresh" --python-version 3.11 --only-binary=:all: \
  --platform manylinux2014_x86_64 "${wheels[@]}"
rm -rf -- "$CACHE/site"
mv "$fresh" "$CACHE/site"
cp -f "$CACHE/files/face_landmarker.task" "$CACHE/face_landmarker.task"
printf '%s\n' "$want" > "$CACHE/.mag-verified"
echo "face: installed in $CACHE"
