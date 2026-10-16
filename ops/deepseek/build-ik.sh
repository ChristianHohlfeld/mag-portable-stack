#!/usr/bin/env bash
# ik_llama.cpp für mag selbst bauen (ab 3.8 die Engine für DeepSeek-V4-Flash).
# Warum ik: eigene CPU-Kernels (iqk) für genau unsere Gewichtstypen, auch auf AVX2 –
#   -rtr sortiert Q8_0 -> Q8_0_R8 und MXFP4 -> MXFP4_R8 um (mainline hat für Q8_0 auf x86 keinen Repack),
#   dazu ~30 DeepSeek-V4-spezifische Optimierungen (ds4_comp, gemergte up/gate, Graph-Reuse).
#
# Fester, geprüfter Stand (IK_REF, Standard unten) + mag-Patches aus ops/deepseek/ik-patches/:
#   0001  prompt_progress im Stream (wie mainline) -> Fortschrittsanzeige im Raum beim Prompt-Lesen
# GCC, Release mit Symbolen (-g, für perf), GGML_NATIVE (Broadwell: AVX2/FMA/F16C/BMI2), nur CPU, statisch.
# Läuft NEBEN dem laufenden Modell (nice/ionice, halbe Kerne); installiert nur nach
# /home/chris/.local/ik/<ref>-mag-<patchhash>/bin/llama-server – das laufende Binary bleibt unberührt.
#
#   bash ops/deepseek/build-ik.sh                 als chris
#   IK_REF=<commit> bash ops/deepseek/build-ik.sh anderer Stand (muss die Patches vertragen)
# Ergebnis: Pfad des Binarys als letzte Zeile (+ BUILD-INFO.txt daneben).
# Abhängigkeiten (einmal als root): apt-get install -y git cmake ninja-build g++
set -euo pipefail
REF=${IK_REF:-8d21687cc7358de10264d4180dba9b863024d5c4}   # ik_llama.cpp main, 16.10.2026 (#2678)
SRC=${IK_SRC:-/home/chris/src/ik_llama.cpp}
URL=${IK_URL:-https://github.com/ikawrakow/ik_llama.cpp.git}
PATCHES=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)/ik-patches
phash=$(cat "$PATCHES"/*.patch 2>/dev/null | sha256sum | cut -c1-8)
DEST=${IK_DEST:-/home/chris/.local/ik/${REF:0:10}-mag-$phash}
JOBS=${JOBS:-$(( $(nproc) / 2 ))}          # halbe Kerne: das Modell läuft weiter
[[ $(id -un) == chris || -n "${IK_ALLOW_USER:-}" ]] || { echo "bitte als chris ausführen"; exit 1; }
for t in git cmake ninja g++; do command -v "$t" >/dev/null || { echo "$t fehlt (sudo apt-get install -y git cmake ninja-build g++)"; exit 1; }; done
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "ungültige IK_REF"; exit 1; }
(( JOBS >= 1 )) || JOBS=1

if [[ -x "$DEST/bin/llama-server" ]] && "$DEST/bin/llama-server" --version >/dev/null 2>&1; then
  echo "schon gebaut: $DEST/bin/llama-server" >&2; echo "$DEST/bin/llama-server"; exit 0
fi
install -d "$(dirname "$SRC")"
if [[ ! -d "$SRC/.git" ]]; then
  git clone --filter=blob:none "$URL" "$SRC" >&2
fi
git -C "$SRC" cat-file -e "$REF^{commit}" 2>/dev/null || git -C "$SRC" fetch --force origin >&2
git -C "$SRC" checkout --quiet --force "$REF"
git -C "$SRC" checkout --quiet -B "mag-${REF:0:10}-$phash"
for p in "$PATCHES"/*.patch; do
  [[ -e "$p" ]] || continue
  echo "Patch: $(basename "$p")" >&2
  git -C "$SRC" -c user.name=mag -c user.email=mag@localhost am --3way --quiet "$p" >&2 \
    || { git -C "$SRC" am --abort >/dev/null 2>&1 || true; echo "Patch $(basename "$p") passt nicht auf $REF"; exit 1; }
done
commit=$(git -C "$SRC" rev-parse --short=10 HEAD)
# DeepSeek V4 muss drin sein
grep -q 'LLM_ARCH_DEEPSEEK4,' "$SRC/src/llama-arch.cpp" || { echo "$REF kennt DeepSeek V4 (deepseek4) nicht"; exit 1; }

B=$SRC/build-mag
rm -rf "$B"
CC=gcc CXX=g++ cmake -S "$SRC" -B "$B" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS=-g -DCMAKE_CXX_FLAGS=-g \
  -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_METAL=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF >&2
nice -n 19 ionice -c 3 ninja -C "$B" -j "$JOBS" llama-server >&2
bin=$B/bin/llama-server
"$bin" --version >&2 || true
help=$("$bin" --help 2>&1 || true)
# alle Flags, die ops/mag-llm-ik.service und upgrade-3.8.sh benutzen
for f in -rtr --mlock --numa --threads --threads-batch --batch-size --ubatch-size --ctx-size --parallel \
         --flash-attn --cache-type-k --cache-type-v --cache-ram --jinja --chat-template-file --reasoning-format \
         --metrics --webui --alias --host --port --model --temp --top-p --min-p -muge; do
  grep -qE -- "(^|[[:space:],])$f([[:space:],=]|$)" <<<"$help" || { echo "neues Binary kennt $f nicht"; exit 1; }
done
# AVX2-Kernels wirklich drin (GGML_NATIVE auf Broadwell)
grep -qE 'AVX2 = 1' <<<"$("$bin" --version 2>&1; "$bin" --help 2>&1 | head -0; "$bin" -m /nonexistent 2>&1 | head -40)" \
  || echo "Hinweis: 'AVX2 = 1' nicht in der Systeminfo gefunden – BUILD-INFO prüfen" >&2
install -d "$DEST/bin"
install -m 0755 "$bin" "$DEST/bin/llama-server.new"
mv -f "$DEST/bin/llama-server.new" "$DEST/bin/llama-server"
{
  echo "ik_llama.cpp ref=$REF commit=$commit gebaut=$(date -Is) von=$(id -un)"
  gcc --version | head -1
  "$DEST/bin/llama-server" --version 2>&1 | head -3
  grep -E 'GGML_NATIVE|GGML_AVX2|GGML_IQK|CMAKE_BUILD_TYPE|CMAKE_C_FLAGS:' "$B/CMakeCache.txt"
  file "$DEST/bin/llama-server" 2>/dev/null || true
  echo "Patches:"; ls "$PATCHES"/*.patch 2>/dev/null | xargs -r -n1 basename
} > "$DEST/BUILD-INFO.txt"
echo "gebaut: $DEST/bin/llama-server ($commit, mit Symbolen)" >&2
echo "$DEST/bin/llama-server"
