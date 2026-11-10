#!/usr/bin/env bash
# Pinned Blender for the scenes of the room (ops/scene), fetched once:
# the official Linux build named in ops/scene/blender.lock (version, SHA256
# and size of its archive), unpacked next to the archive. Run as the user that
# owns the path ops/scene/install.sh reads (chris on MAG):
#   ~/.cache/mag-stack/blender/blender-<version>-linux-x64/
# Idempotent: a verified, unpacked build is left alone. The archive has one
# writer (a lock) and is only ever unpacked complete and verified.
#   bash ops/scene/bootstrap.sh [--verify-only]
set -euo pipefail
SOURCE="$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)"
read -r VERSION SHA256 SIZE < "$SOURCE/ops/scene/blender.lock"
[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && $SHA256 =~ ^[0-9a-f]{64}$ && $SIZE =~ ^[0-9]+$ ]] || { echo 'ops/scene/blender.lock is malformed' >&2; exit 1; }
NAME="blender-$VERSION-linux-x64"
URL="https://download.blender.org/release/Blender${VERSION%.*}/$NAME.tar.xz"
CACHE="${MAG_SCENE_CACHE:-$HOME/.cache/mag-stack}/blender"
ARCHIVE="$CACHE/$NAME.tar.xz"

archive_ok() {
  [[ -f $ARCHIVE && ! -L $ARCHIVE && $(stat -c %s "$ARCHIVE") == "$SIZE" ]] || return 1
  printf '%s  %s\n' "$SHA256" "$ARCHIVE" | sha256sum -c --quiet - >/dev/null 2>&1
}
# Unpacked from the verified archive: the mark carries the archive's checksum.
unpacked_ok() { [[ -x $CACHE/$NAME/blender && "$(cat "$CACHE/$NAME/.mag-verified" 2>/dev/null)" == "$SHA256" ]]; }

if [[ ${1:-} == --verify-only ]]; then
  unpacked_ok || { echo "Blender $VERSION is not unpacked from the verified archive in $CACHE" >&2; exit 1; }
  echo "scenes OK: Blender $VERSION in $CACHE/$NAME"
  exit 0
fi
mkdir -p "$CACHE"
if unpacked_ok; then
  echo "Blender $VERSION already unpacked and verified"
  exit 0
fi
exec 9>"$CACHE/.download.lock"
flock -n 9 || { echo 'another download of Blender is running' >&2; exit 1; }
if ! archive_ok; then
  curl --fail --location --retry 3 --silent --show-error --proto '=https' --proto-redir '=https' -o "$ARCHIVE.part" "$URL"
  [[ $(stat -c %s "$ARCHIVE.part") == "$SIZE" ]] || { echo 'Blender: wrong size' >&2; exit 1; }
  printf '%s  %s\n' "$SHA256" "$ARCHIVE.part" | sha256sum -c --quiet -
  mv -f "$ARCHIVE.part" "$ARCHIVE"
fi
# Unpack beside the old tree and swap, so a half-unpacked build is never used.
fresh="$CACHE/.$NAME.unpack"
rm -rf -- "$fresh"
mkdir "$fresh"
tar -xJf "$ARCHIVE" -C "$fresh" --no-same-owner
[[ -x $fresh/$NAME/blender ]] || { echo 'Blender: archive has no blender binary' >&2; exit 1; }
printf '%s\n' "$SHA256" > "$fresh/$NAME/.mag-verified"
rm -rf -- "${CACHE:?}/$NAME"
mv "$fresh/$NAME" "$CACHE/$NAME"
rmdir "$fresh"
echo "Blender $VERSION unpacked and verified in $CACHE/$NAME"
