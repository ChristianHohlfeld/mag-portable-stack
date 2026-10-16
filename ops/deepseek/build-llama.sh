#!/usr/bin/env bash
# llama.cpp für mag selbst bauen: neuerer Stand (enthält den F16C-Fix #30343 ab b11635), mit den
# mag-Patches aus ops/deepseek/llama-patches/ (AVX2-Kernels für den Q8_0-Repack), mit GCC,
# für genau diese CPU (GGML_NATIVE: Broadwell, AVX2/FMA/F16C/BMI2), mit Symbolen (-g, nicht gestrippt)
# für echte perf-Profile. CPU-only, ein statisches Binary. Läuft NEBEN dem laufenden Modell (nice/ionice);
# installiert nur nach /home/chris/.local/llama/<ref>/ – das laufende Binary bleibt unberührt.
#
#   bash ops/deepseek/build-llama.sh                  als chris; Stand: LLAMA_REF (Standard b11683)
#   LLAMA_REF=b11700 bash ops/deepseek/build-llama.sh
# Ergebnis: /home/chris/.local/llama/<ref>/bin/llama (+ BUILD-INFO.txt). Druckt den Pfad als letzte Zeile.
# Abhängigkeiten (einmal als root): apt-get install -y git cmake ninja-build g++ libssl-dev
set -euo pipefail
REF=${LLAMA_REF:-b11683}
SRC=${LLAMA_SRC:-/home/chris/src/llama.cpp}
PATCHES=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)/llama-patches
phash=$(cat "$PATCHES"/*.patch 2>/dev/null | sha256sum | cut -c1-8)
DEST=/home/chris/.local/llama/$REF-mag-$phash
JOBS=${JOBS:-$(( $(nproc) / 2 ))}          # halbe Kerne: das Modell läuft weiter
[[ $(id -un) == chris ]] || { echo "bitte als chris ausführen"; exit 1; }
for t in git cmake ninja g++; do command -v "$t" >/dev/null || { echo "$t fehlt (sudo apt-get install -y git cmake ninja-build g++ libssl-dev)"; exit 1; }; done
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "ungültige LLAMA_REF"; exit 1; }

if [[ -x "$DEST/bin/llama" ]] && "$DEST/bin/llama" --version >/dev/null 2>&1; then
  echo "schon gebaut: $DEST/bin/llama" >&2; echo "$DEST/bin/llama"; exit 0
fi
install -d "$(dirname "$SRC")"
if [[ ! -d "$SRC/.git" ]]; then
  git clone --filter=blob:none https://github.com/ggml-org/llama.cpp.git "$SRC" >&2
fi
git -C "$SRC" fetch --tags --force origin >&2
git -C "$SRC" checkout --quiet --force "$REF"
git -C "$SRC" checkout --quiet -B "mag-$REF-$phash"
for p in "$PATCHES"/*.patch; do
  [[ -e "$p" ]] || continue
  echo "Patch: $(basename "$p")" >&2
  git -C "$SRC" -c user.name=mag -c user.email=mag@localhost am --3way --quiet "$p" >&2 \
    || { git -C "$SRC" am --abort >/dev/null 2>&1 || true; echo "Patch $(basename "$p") passt nicht auf $REF"; exit 1; }
done
commit=$(git -C "$SRC" rev-parse --short=9 HEAD)
# Fix für die AVX2/F16C-Regression muss enthalten sein (b11635, Commit 685f70894)
git -C "$SRC" merge-base --is-ancestor 685f70894 HEAD \
  || { echo "$REF enthält den F16C-Fix (#30343, b11635) nicht – neueren Stand wählen"; exit 1; }

B=$SRC/build-mag-$REF
rm -rf "$B"
CC=gcc CXX=g++ cmake -S "$SRC" -B "$B" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS=-g -DCMAKE_CXX_FLAGS=-g \
  -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_METAL=OFF \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF >&2
nice -n 19 ionice -c 3 ninja -C "$B" -j "$JOBS" llama-app >&2
bin=$B/bin/llama
"$bin" --version >&2
help=$("$bin" serve --help 2>&1)
for f in --load-mode --no-repack --numa --reasoning-format --chat-template-file --cache-ram --metrics; do
  grep -qE -- "(^|[[:space:],])$f([[:space:],=]|$)" <<<"$help" || { echo "neues Binary kennt $f nicht"; exit 1; }
done
grep -q 'mmap+mlock' <<<"$help" || { echo "neues Binary: --load-mode ohne mmap+mlock"; exit 1; }
# Selbsttest des Q8_0-Repacks auf DIESER CPU: Ergebnis mit Repack == Referenz, sonst wird nicht installiert
g++ -O2 -std=c++17 -fopenmp "$PATCHES/qcheck-q8_0-repack.cpp" -I"$SRC/ggml/include" \
  "$B/ggml/src/libggml.a" "$B/ggml/src/libggml-cpu.a" "$B/ggml/src/libggml-base.a" -lpthread -ldl -o "$B/qcheck" >&2
qres=$(nice -n 19 "$B/qcheck" 4 2>&1 | grep -v '^repack:') || { echo "$qres" >&2; echo "Selbsttest Q8_0-Repack FEHLGESCHLAGEN – nicht installiert"; exit 1; }
echo "$qres" >&2
install -d "$DEST/bin"
install -m 0755 "$bin" "$DEST/bin/llama.new"
mv -f "$DEST/bin/llama.new" "$DEST/bin/llama"
{
  echo "ref=$REF commit=$commit gebaut=$(date -Is) von=$(id -un)"
  gcc --version | head -1
  "$DEST/bin/llama" --version 2>&1 | head -3
  grep -E 'GGML_NATIVE|GGML_AVX2|CMAKE_BUILD_TYPE|CMAKE_C_FLAGS:' "$B/CMakeCache.txt"
  file "$DEST/bin/llama" 2>/dev/null || true
  echo "Patches:"; ls "$PATCHES"/*.patch 2>/dev/null | xargs -r -n1 basename
  echo "Selbsttest Q8_0-Repack:"; echo "$qres"
} > "$DEST/BUILD-INFO.txt"
echo "gebaut: $DEST/bin/llama ($commit, mit Symbolen)" >&2
echo "$DEST/bin/llama"
