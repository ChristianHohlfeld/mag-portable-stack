#!/usr/bin/env bash
# IQ1_M trust-on-first-use size/SHA256 pin; supports a single GGUF or a
# complete numbered split set. Never modifies the weights.
set -euo pipefail
DIR=${MAG_GLM_IQ1_DIR:-/home/chris/models/GLM-5.3-Flash-GGUF-IQ1_M}
PIN=${MAG_GLM_IQ1_PIN_DIR:-/etc/mag-glm}
fail(){ echo "IQ1_M preflight: $*" >&2; exit 4; }
[[ -d $DIR && ! -L $DIR ]] || fail "model directory missing"
install -d -m 0755 "$PIN"
[[ -d $PIN && ! -L $PIN ]] || fail "pin directory invalid"
shopt -s nullglob
files=("$DIR"/*.gguf)
(( ${#files[@]} > 0 )) || fail "missing GGUF"
for part in "${files[@]}"; do
  name=${part##*/}
  [[ $name =~ ^[A-Za-z0-9._-]+[.]gguf$ && -f $part && ! -L $part ]] ||
    fail "invalid filename/symlink: $name"
  [[ $(stat -c %s "$part") -ge 1024 && $(head -c 4 "$part") == GGUF ]] ||
    fail "invalid/incomplete GGUF: $name"
  for ext in .part .aria2 .incomplete; do [[ ! -e $part$ext ]] || fail "partial $name"; done
done
# If a filename declares N shards, require all N in the same model set.
declared=0
for part in "${files[@]}"; do
  name=${part##*/}
  if [[ $name =~ -([0-9]{5})-of-([0-9]{5})[.]gguf$ ]]; then
    shard=$((10#${BASH_REMATCH[1]}))
    total=$((10#${BASH_REMATCH[2]}))
    (( total >= 1 && shard >= 1 && shard <= total )) || fail "bad shard index"
    if (( declared == 0 )); then declared=$total; fi
    (( total == declared )) || fail "mixed shard counts"
  elif (( ${#files[@]} > 1 )); then
    fail "multiple unnumbered GGUF files"
  fi
done
if (( declared > 0 )); then
  (( ${#files[@]} == declared )) || fail "incomplete split set"
  for ((n=1;n<=declared;n++)); do
    printf -v num '%05d' "$n"
    found=0
    for part in "${files[@]}"; do
      [[ ${part##*/} == *-"$num"-of-* ]] && found=1
    done
    (( found )) || fail "missing split shard $num"
  done
fi
sizes="$PIN/iq1.sizes"
hashes="$PIN/iq1.sha256"
[[ ! -L $sizes && ! -L $hashes ]] || fail "symlinked pin"
if [[ ! -e $sizes && ! -e $hashes ]]; then
  # One-time full read. Existing RAM-resident GLMs are never restarted.
  a=$(mktemp "$PIN/.iq1-sizes.XXXXXX")
  b=$(mktemp "$PIN/.iq1-sha.XXXXXX")
  trap 'rm -f -- "$a" "$b"' EXIT
  (cd "$DIR" && stat -c '%s  %n' -- *.gguf) >"$a"
  (cd "$DIR" && sha256sum -- *.gguf) >"$b"
  chmod 0644 "$a" "$b"
  mv "$a" "$sizes"
  mv "$b" "$hashes"
elif [[ ! -s $sizes || ! -s $hashes ]]; then
  fail "partial size/SHA256 pin; refuse auto re-pin"
fi
[[ $(wc -l < "$sizes") -eq ${#files[@]} &&
   $(wc -l < "$hashes") -eq ${#files[@]} ]] || fail "pin count mismatch"
(cd "$DIR" && stat -c '%s  %n' -- *.gguf | cmp -s - "$sizes") ||
  fail "IQ1 size/filename mismatch"
cmp -s <(awk '{print $2}' "$sizes") <(awk '{print $2}' "$hashes") ||
  fail "IQ1 SHA filename mismatch"
case ${MAG_GLM_IQ1_VERIFY_SHA256:-0} in
  0) : ;; # avoid repeatedly reading ~66 GB on every deploy
  1) (cd "$DIR" && sha256sum --check --strict --status "$hashes") ||
       fail "IQ1 SHA256 mismatch" ;;
  *) fail "MAG_GLM_IQ1_VERIFY_SHA256 must be 0 or 1" ;;
esac
echo "IQ1_M preflight OK: ${#files[@]} GGUF(s), pinned size and SHA256"
