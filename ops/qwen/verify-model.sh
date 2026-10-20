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
(cd "$DIR" && sha256sum --check --strict --status "$PIN") || { echo 'Qwen upstream SHA256 mismatch' >&2; exit 4; }
echo 'Qwen GGUF upstream SHA256 verified'
