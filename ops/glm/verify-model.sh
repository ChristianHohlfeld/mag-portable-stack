#!/usr/bin/env bash
# GLM-5.3-Flash IQ1_M: all 15 split files present, exact byte sizes as pinned
# in model.sizes (recorded on mag after the download), GGUF header on every
# part, no partial-download leftovers. No upstream SHA256 is pinned yet; a
# truncated or foreign tensor block is still caught by llama.cpp's bounds check
# at load, and the layout transaction then restores the previous NUMA1 model.
set -euo pipefail
DIR=${MAG_GLM_DIR:-/home/chris/models/GLM-5.3-Flash-GGUF-IQ1_M}
SIZES=${MAG_GLM_SIZES:-/usr/local/lib/mag-glm/model.sizes}
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
