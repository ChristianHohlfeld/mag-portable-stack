#!/usr/bin/env bash
set -euo pipefail
DIR=${MAG_IQ3_DIR:-/home/chris/models/deepseek-v4-UD-IQ3_XXS}
[[ -d "$DIR" && -f "$DIR/SHA256SUMS" && ! -L "$DIR/SHA256SUMS" ]] || { echo "IQ3: manifest missing" >&2; exit 4; }
cd "$DIR"
shopt -s nullglob
parts=( *.gguf )
(( ${#parts[@]} == 4 )) || { echo "IQ3: exactly four GGUF splits required" >&2; exit 4; }
declare -A seen=()
stem=""
for f in "${parts[@]}"; do
  [[ -f "$f" && ! -L "$f" && "$f" =~ ^(.+)-0000([1-4])-of-00004[.]gguf$ ]] || { echo "IQ3: bad split $f" >&2; exit 4; }
  n=${BASH_REMATCH[2]}; [[ -z "${seen[$n]:-}" ]] || exit 4; seen[$n]=1
  prefix=${BASH_REMATCH[1]}; [[ -z "$stem" || "$stem" == "$prefix" ]] || exit 4; stem=$prefix
  [[ $(stat -c %s "$f") -gt 0 ]] || { echo "IQ3: incomplete $f" >&2; exit 4; }
  for suffix in .part .aria2 .incomplete .lock; do [[ ! -e "$f$suffix" ]] || exit 4; done
done
for n in 1 2 3 4; do [[ -n "${seen[$n]:-}" ]] || exit 4; done
declare -A checks=()
count=0
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^([a-fA-F0-9]{64})[[:space:]]+[*]?([^[:space:]/]+[.]gguf)$ ]] || { echo "IQ3: invalid SHA256SUMS line" >&2; exit 4; }
  f=${BASH_REMATCH[2]}
  [[ -z "${checks[$f]:-}" && -f "$f" && ! -L "$f" ]] || exit 4
  checks[$f]=1
  (( ++count ))
done < SHA256SUMS
(( count == 4 )) || exit 4
for f in "${parts[@]}"; do [[ -n "${checks[$f]:-}" ]] || exit 4; done
# The model-local manifest is insufficient: hashes must equal audited upstream.
PINNED=${MAG_IQ3_PINNED_MANIFEST:-$(dirname "$(readlink -f "$0")")/iq3.sha256}
[[ -f "$PINNED" && ! -L "$PINNED" ]] || { echo "IQ3: pinned manifest missing" >&2;exit 4; }
# Four official names, four known hashes; disallow added lines or renamed parts.
expected=(DeepSeek-V4-Flash-0731-UD-IQ3_XXS-0000{1..4}-of-00004.gguf)
pinned_count=$(wc -l < "$PINNED")
(( pinned_count == 4 )) || exit 4
for n in "${expected[@]}"; do grep -Eq "^[a-f0-9]{64}  $n$" "$PINNED" || { echo "IQ3: expected name missing" >&2;exit 4; };done
# Normalize both tables: pairs must be identical, irrespective of row order.
# Bash awk version is more portable than sed's GNU case conversion here.
normalize(){ awk 'NF==2 && length($1)==64 && $1~/^[a-fA-F0-9]+$/ {sub(/^\*/,"",$2);print tolower($1)"  "$2;next} {exit 4}' "$1" | LC_ALL=C sort; }
[[ "$(normalize "$PINNED")" == "$(normalize SHA256SUMS)" ]] || { echo "IQ3: local manifest differs from pinned hashes" >&2;exit 4; }
sha256sum --check --strict --status "$PINNED" || { echo "IQ3: pinned SHA verification failed" >&2;exit 4; }
printf '%s/%s-00001-of-00004.gguf\n' "$DIR" "$stem"
