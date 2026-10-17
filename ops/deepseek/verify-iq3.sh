#!/usr/bin/env bash
set -euo pipefail
DIR=${MAG_IQ3_DIR:-/home/chris/models/deepseek-v4-UD-IQ3_XXS}
[[ -d "$DIR" && -f "$DIR/SHA256SUMS" && ! -L "$DIR/SHA256SUMS" ]] || { echo "IQ3: manifest missing" >&2; exit 4; }
cd "$DIR"
if [[ "${MAG_IQ3_REQUIRE_EXCLUSIVE:-0}" == 1 ]] && systemctl is-active --quiet mag-llm.service;then echo "IQ3: Q4 is still resident" >&2;exit 4;fi
MAG_IQ3_MIN_PART_BYTES=${MAG_IQ3_MIN_PART_BYTES:-1000000000}
[[ "$MAG_IQ3_MIN_PART_BYTES" =~ ^[0-9]+$ ]] || exit 4
shopt -s nullglob
parts=( *.gguf )
(( ${#parts[@]} == 4 )) || { echo "IQ3: exactly four GGUF splits required" >&2; exit 4; }
declare -A seen=()
stem=""
for f in "${parts[@]}"; do
  [[ -f "$f" && ! -L "$f" && "$f" =~ ^(.+)-0000([1-4])-of-00004[.]gguf$ ]] || { echo "IQ3: bad split $f" >&2; exit 4; }
  n=${BASH_REMATCH[2]}; [[ -z "${seen[$n]:-}" ]] || exit 4; seen[$n]=1
  prefix=${BASH_REMATCH[1]}; [[ -z "$stem" || "$stem" == "$prefix" ]] || exit 4; stem=$prefix
  [[ $(stat -c %s "$f") -gt "$MAG_IQ3_MIN_PART_BYTES" ]] || { echo "IQ3: incomplete $f" >&2; exit 4; }
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
sha256sum --check --strict --status SHA256SUMS || { echo "IQ3: SHA failed" >&2; exit 4; }
printf '%s/%s-00001-of-00004.gguf\n' "$DIR" "$stem"
