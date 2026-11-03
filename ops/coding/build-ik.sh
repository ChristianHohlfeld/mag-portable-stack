#!/usr/bin/env bash
# Build the pinned ik_llama.cpp server for Flint (ik #2725 Flint, #2728 template
# escapes) on the build cores only. Installs to ~/.local/mag-coding-ik/<rev>/bin.
set -euo pipefail
REV=$(grep -o 'IK_REV=[a-f0-9]\{40\}' "$(dirname "$0")/start-live.sh" | cut -d= -f2)
SRC=/home/chris/src/ik_llama.cpp-flint
[[ -d $SRC/.git ]] || git clone -q https://github.com/ikawrakow/ik_llama.cpp.git "$SRC"
git -C "$SRC" fetch -q origin "$REV" 2>/dev/null || git -C "$SRC" fetch -q --unshallow origin || true
git -C "$SRC" checkout -q --detach "$REV"
run=(taskset -c "${MAG_BUILD_CPUS:-28-55}" nice -n 19 ionice -c3)
"${run[@]}" cmake -S "$SRC" -B "$SRC/build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF
"${run[@]}" cmake --build "$SRC/build" -j 8 --target llama-server
OUT=/home/chris/.local/mag-coding-ik/$REV/bin
mkdir -p "$OUT"
install -m 0755 "$SRC/build/bin/llama-server" "$OUT/llama-server"
echo "$REV" > "$OUT/source-commit"
