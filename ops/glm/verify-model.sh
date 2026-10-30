#!/usr/bin/env bash
# GLM-5.3-Flash IQ2_XS: 15 split files, pinned sizes, GGUF headers,
# no partial downloads. model.sha256 pins HF LFS OIDs; a 92 GB full read
# is opt-in, not repeated on each automatic deploy.
# llama.cpp also checks tensor bounds; load failure restores NUMA1.
set -euo pipefail
DIR=${MAG_GLM_DIR:-/home/chris/models/GLM-5.3-Flash-GGUF-IQ2_XS}
SIZES=${MAG_GLM_SIZES:-/usr/local/lib/mag-glm/model.sizes}
SHA256S=${MAG_GLM_SHA256S:-/usr/local/lib/mag-glm/model.sha256}
fail(){ echo "GLM model: $*" >&2; exit 4; }
[[ -d $DIR && ! -L $DIR ]] || fail "directory missing: $DIR"
[[ -f $SIZES && ! -L $SIZES ]] || fail 'size pin missing'
[[ $(grep -c . "$SIZES") == 15 ]] || fail 'size pin must list 15 parts'
while read -r size name; do
  [[ $size =~ ^[0-9]+$ && $name =~ ^GLM-5[.]3-Flash-IQ1_M-000[0-9]{2}-of-00015[.]gguf$ ]] || fail "bad pin line: $size $name"
  f="$DIR/$name"
  [[ -f $f && ! -L $f ]] || fail "missing part $name"
  [[ $(stat -c %s "$f") == "$size" ]] || fail "size mismatch $name (incomplete download?)"
  [[ $(head -c 4 "$f") == GGUF ]] || fail "bad GGUF header $name"
  for suffix in .part .aria2 .incomplete .lock; do
    [[ ! -e "$f$suffix" ]] || fail "incomplete suffix $name$suffix"
  done
done < "$SIZES"
echo 'GLM GGUF parts verified (15 files, pinned sizes)'
case ${MAG_GLM_VERIFY_SHA256:-0} in
  0) ;;
  1)
    [[ -f $SHA256S && ! -L $SHA256S ]] || fail 'SHA256 pin missing'
    [[ $(grep -c . "$SHA256S") == 15 ]] || fail 'SHA256 pin must list 15 parts'
    cmp -s <(awk '{print $2}' "$SIZES") <(awk '{print $2}' "$SHA256S") || fail 'SHA256 names differ from size pins'
    (cd "$DIR" && sha256sum --check --strict --status "$SHA256S") || fail 'SHA256 mismatch'
    echo 'GLM GGUF SHA256 verified (15 files, HF LFS pins)'
    ;;
  *) fail 'MAG_GLM_VERIFY_SHA256 must be 0 or 1' ;;
esac
