#!/usr/bin/env bash
# Validate original HF digest, exact GGUF identity, and completed download.
set -euo pipefail
FILE=Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf
DIR=/home/chris/models/qwen3.8-flash-next-uncensored
PIN=/etc/mag-qwen/model.sha256
[[ -f "$DIR/$FILE" && ! -L "$DIR/$FILE" ]] || { echo 'Qwen GGUF missing' >&2; exit 4; }
[[ -f "$PIN" && ! -L "$PIN" ]] || { echo 'independent upstream digest not pinned' >&2; exit 4; }
[[ $(wc -l < "$PIN") == 1 ]] || exit 4
grep -Eq '^[a-f0-9]{64}  Qwen3[.]8-Flash-Next-Uncensored[.]IQ4_XS[.]gguf$' "$PIN" || exit 4
[[ $(stat -c %s "$DIR/$FILE") -ge 90000000000 ]] || { echo 'Qwen GGUF download incomplete' >&2; exit 4; }
[[ $(head -c 4 "$DIR/$FILE") == GGUF ]] || { echo 'bad GGUF header' >&2; exit 4; }
for suffix in .part .aria2 .incomplete .lock; do
  [[ ! -e "$DIR/$FILE$suffix" ]] || { echo "incomplete suffix $suffix" >&2; exit 4; }
done
# The full read takes ~6 min on every boot and leaves ~90 GiB of page cache
# on NUMA1 (the unit binds memory there), which the mlock load then fights.
# Skip it while the file is provably the one root last verified: inode, size,
# mtime and ctime unchanged. ctime cannot be set without root, so a swapped or
# edited file always gets the full check. Only root writes the stamp
# (install.sh, or a manual `sudo verify-model.sh`); delete it to force a check.
STAMP=/etc/mag-qwen/model.verified
identity(){ printf '%s %s\n' "$(sha256sum < "$PIN" | cut -c1-64)" "$(LC_ALL=C stat -c '%d %i %s %.9Y %.9Z' "$DIR/$FILE")"; }
if [[ -f "$STAMP" && ! -L "$STAMP" && $(stat -c %u "$STAMP") == 0 && $(cat "$STAMP") == "$(identity)" ]]; then
  echo 'Qwen GGUF unchanged since root-verified upstream SHA256'
  exit 0
fi
(cd "$DIR" && sha256sum --check --strict --status "$PIN") || { echo 'Qwen upstream SHA256 mismatch' >&2; exit 4; }
if [[ $EUID == 0 ]]; then
  identity > "$STAMP.tmp" && chmod 0644 "$STAMP.tmp" && mv -f "$STAMP.tmp" "$STAMP"
fi
echo 'Qwen GGUF upstream SHA256 verified'
