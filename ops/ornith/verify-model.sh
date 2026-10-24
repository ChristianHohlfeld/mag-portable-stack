#!/usr/bin/env bash
# Ornith GGUF identity: pinned SHA-256 (repo == /etc/mag-ornith/model.sha256 == bytes).
# Four instances start together: one full read under a lock, then a root stamp
# bound to inode/size/mtime/ctime (same scheme as ops/qwen/verify-model.sh).
set -euo pipefail
FILE=Ornith-1.5-35B-A3B-Q4_K_M.gguf
DIR=/home/chris/models/ornith-1.5-35b-a3b
PIN=/etc/mag-ornith/model.sha256
WANT=12d8d5c01bae7f23ea4822b2f96ba069d531f827d02a57c31002f2f95e72614a
[[ -f "$DIR/$FILE" && ! -L "$DIR/$FILE" ]] || { echo 'Ornith GGUF missing' >&2; exit 4; }
[[ -f "$PIN" && ! -L "$PIN" ]] || { echo 'Ornith digest not pinned' >&2; exit 4; }
[[ $(awk '{print $1; exit}' "$PIN") == "$WANT" ]] || { echo 'Ornith pin differs from repository digest' >&2; exit 4; }
[[ $(head -c 4 "$DIR/$FILE") == GGUF ]] || { echo 'bad GGUF header' >&2; exit 4; }
[[ $(stat -c %s "$DIR/$FILE") -ge 15000000000 ]] || { echo 'Ornith GGUF incomplete' >&2; exit 4; }
STAMP=/etc/mag-ornith/model.verified
identity(){ printf '%s %s\n' "$WANT" "$(LC_ALL=C stat -c '%d %i %s %.9Y %.9Z' "$DIR/$FILE")"; }
stamped(){ [[ -f "$STAMP" && ! -L "$STAMP" && $(stat -c %u "$STAMP") == 0 && $(cat "$STAMP") == "$(identity)" ]]; }
if stamped; then echo 'Ornith GGUF unchanged since root-verified SHA256'; exit 0; fi
exec 9>/run/lock/mag-ornith-verify.lock
flock 9
if stamped; then exit 0; fi
[[ $(sha256sum < "$DIR/$FILE" | cut -c1-64) == "$WANT" ]] || { echo 'Ornith SHA256 mismatch' >&2; exit 4; }
if [[ $EUID == 0 ]]; then
  identity > "$STAMP.tmp" && chmod 0644 "$STAMP.tmp" && mv -f "$STAMP.tmp" "$STAMP"
fi
echo 'Ornith GGUF SHA256 verified'
