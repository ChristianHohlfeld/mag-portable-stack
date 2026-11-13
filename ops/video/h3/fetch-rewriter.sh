#!/bin/bash
# The small model that puts a prompt typed in the page into MiniMax's own form
# for H3 (English directions, the speech verbatim): Qwen3-4B-Instruct-2507
# (Apache-2.0) as GGUF Q4_K_M, at a fixed revision. It runs on the CPU (cores
# 28-55) for a moment inside the video stage, never on Lena's or Noah's model
# servers. One writer (a lock), checked against its SHA256. Deletes nothing.
set -e
OUT=${MAG_IDOL_BASE:-$HOME/idol-mi50}/weights/h3-rewriter
mkdir -p "$OUT"; cd "$OUT"
exec 9>"$OUT/.fetch.lock"
flock -n 9 || { echo 'another fetch is running' >&2; exit 1; }
F=qwen3-4b-instruct-q4_k_m.gguf
SUM=3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597
URL=https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
[ -s "$F" ] || { curl -fsSL --retry 20 --retry-delay 10 -C - -o "$F.part" "$URL" && mv "$F.part" "$F"; }
echo "$SUM  $F" | sha256sum --quiet -c - || { echo "WRONG $F" >&2; exit 2; }
touch .complete
echo done
