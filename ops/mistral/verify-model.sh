#!/usr/bin/env bash
# Require the complete six-shard UD-IQ1_S GGUF and its HF-pinned SHA256s.
set -euo pipefail
DIR=/home/chris/models/Mistral-Large-4-1T-A52B-UD-IQ1_S
MANIFEST="$DIR/SHA256SUMS"
PIN=/etc/mag-mistral/SHA256SUMS
fail(){ echo "Mistral model verification: $*" >&2; exit 4; }
[[ -d "$DIR" && ! -L "$DIR" ]] || fail 'model directory missing or symlink'
[[ -f "$MANIFEST" && ! -L "$MANIFEST" && -f "$PIN" && ! -L "$PIN" ]] || fail 'SHA256SUMS missing'
cmp -s "$MANIFEST" "$PIN" || fail 'model-directory SHA256SUMS differs from pinned HF manifest'
[[ $(wc -l < "$PIN") == 6 ]] || fail 'manifest must contain exactly six shards'
while read -r hash file extra; do
  [[ "$hash" =~ ^[a-f0-9]{64}$ && -z ${extra:-} ]] || fail 'invalid manifest row'
  [[ "$file" =~ ^Mistral-Large-4-1T-A52B-UD-IQ1_S-0000[1-6]-of-00006[.]gguf$ ]] || fail "unexpected shard name: $file"
  [[ -f "$DIR/$file" && ! -L "$DIR/$file" ]] || fail "missing or non-regular shard: $file"
  [[ $(stat -c %s "$DIR/$file") -ge 10000000 ]] || fail "shard is incomplete: $file"
done < "$PIN"
for suffix in .part .aria2 .incomplete .lock; do
  if compgen -G "$DIR/*$suffix" >/dev/null; then fail "download still has $suffix files"; fi
done
STAMP=/etc/mag-mistral/model.verified
identity(){
  printf '%s\n' "$(sha256sum < "$PIN" | cut -c1-64)"
  for file in $(awk '{print $2}' "$PIN"); do
    LC_ALL=C stat -c '%d %i %s %y %z' "$DIR/$file"
  done
}
if [[ -f "$STAMP" && ! -L "$STAMP" && $(stat -c %u "$STAMP") == 0 ]] &&
   [[ $(cat "$STAMP") == "$(identity)" ]]; then
  echo 'All six Mistral shards unchanged since root-verified HF SHA256'
  exit 0
fi
(cd "$DIR" && sha256sum --check --strict "$MANIFEST") || fail 'HF SHA256 mismatch / incomplete shard'
if [[ $EUID == 0 ]]; then
  identity > "$STAMP.tmp" && chmod 0644 "$STAMP.tmp" && mv -f "$STAMP.tmp" "$STAMP"
fi
echo 'All six Mistral shards match the Hugging Face SHA256 manifest'
