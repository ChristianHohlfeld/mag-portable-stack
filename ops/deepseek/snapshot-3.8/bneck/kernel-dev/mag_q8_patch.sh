#!/usr/bin/env bash
# mag_q8_patch.sh — AVX2 Q8_0 repack + op profiler for llama.cpp on mag (2x E5-2680 v4)
#
#   ./mag_q8_patch.sh build     # master (base) + master with patch (q8), both native (AVX2/FMA/F16C)
#   ./mag_q8_patch.sh test      # correctness: repack path vs plain path, must print OK
#   ./mag_q8_patch.sh repackcheck  # is the MXFP4/Q8_0 repack actually used for the model? (buffer sizes)
#   ./mag_q8_patch.sh bench     # A/B on node 0: base vs q8, same flags, -r 5
#   ./mag_q8_patch.sh profile   # per-op wall time + barrier wait (GGML_CPU_OP_PROFILE=1)
#
# Merge rule (Ali): test = OK  AND  bench tg(q8) >= +15 % vs tg(base). Otherwise keep base.
set -euo pipefail

M="${M:-/models/DeepSeek-V4-Flash-UD-Q4_K_XL.gguf}"
PATCH="${PATCH:-$HOME/mag-ggml-cpu-q8_0-avx2.patch}"
SRC="${SRC:-$HOME/speed/llama-q8}"
BASE_COMMIT="${BASE_COMMIT:-9a16f1dbe4722a93ab75a2d15e29aa71ef764c7b}"   # master the patch was made and tested on
NODE="${NODE:-0}"
T="${T:-14}"
OUT="${OUT:-$HOME/speed/results}"
mkdir -p "$OUT"

pcores() { lscpu -p=CPU,CORE,NODE | grep -v '^#' | awk -F, -v n="$NODE" '$3==n && !seen[$2]++ {print $1}' | paste -sd,; }
drop()   { sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }

build_tree() { # $1 = dir, $2 = apply patch (0/1)
  local d="$1"
  [ -d "$d" ] || git clone https://github.com/ggml-org/llama.cpp "$d"
  git -C "$d" fetch -q origin "$BASE_COMMIT" && git -C "$d" checkout -q -f "$BASE_COMMIT"
  git -C "$d" clean -qfd tests
  [ "$2" = 1 ] && git -C "$d" apply "$PATCH"
  cmake -S "$d" -B "$d/build" -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DGGML_OPENMP=ON -DLLAMA_CURL=OFF > /dev/null
  cmake --build "$d/build" -j"$(nproc)" --target llama-bench llama-server test-repack-cpu 2>/dev/null \
    || cmake --build "$d/build" -j"$(nproc)" --target llama-bench llama-server
}

case "${1:-}" in
build)
  build_tree "$SRC-base" 0
  build_tree "$SRC" 1
  echo "base: $SRC-base/build/bin   q8: $SRC/build/bin"
  ;;

test)
  # every row must show path=repack for q8_0 and nmse ~1e-13; last line must be OK
  "$SRC/build/bin/test-repack-cpu" -t "$T" -r 20 2>/dev/null | tee "$OUT/repack_test.txt"
  tail -1 "$OUT/repack_test.txt" | grep -q '^OK' && echo "TEST GREEN" || { echo "TEST RED"; exit 1; }
  ;;

repackcheck)
  # loads the model once (llama-bench -n 1) and prints the buffer split. Expected with the patch:
  #   CPU_REPACK model buffer size  ~= all Q8_0 dense + MXFP4 expert tensors   (well over 100 GB)
  #   CPU_Mapped / CPU model buffer = only the rest (embeddings, norms, ...)
  # If CPU_REPACK is missing or tiny on the BASE build, the MXFP4 experts were not repacked on mag either
  # (that is the 25 % ggml_vec_dot_mxfp4_q8_0 in perf) -> check for -nr/--no-repack in the start command.
  for b in "$SRC-base" "$SRC"; do
    echo "== $b"
    numactl --physcpubind="$(pcores)" --membind="$NODE" "$b/build/bin/llama-bench" -m "$M" -mmp 0 -t "$T" -p 0 -n 1 -r 1 -v 2>&1 \
      | grep -E "model buffer size|repack" | sort | uniq -c | head -20
  done
  ;;

bench)
  for b in "$SRC-base" "$SRC"; do
    tag=$(basename "$b")
    drop
    OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores \
    numactl --physcpubind="$(pcores)" --membind="$NODE" "$b/build/bin/llama-bench" -m "$M" -mmp 0 --numa numactl \
      -t "$T" -fa 1 -p 512 -n 128 -r 5 -o json > "$OUT/ab_$tag.json"
  done
  python3 - "$OUT/ab_$(basename "$SRC-base").json" "$OUT/ab_$(basename "$SRC").json" <<'PY'
import json,sys
def get(f):
    r=json.load(open(f)); return (next(x["avg_ts"] for x in r if x["n_gen"]==0), next(x["avg_ts"] for x in r if x["n_prompt"]==0))
b,q=get(sys.argv[1]),get(sys.argv[2])
print(f"base: pp512 {b[0]:.2f}  tg128 {b[1]:.2f} t/s")
print(f"q8  : pp512 {q[0]:.2f}  tg128 {q[1]:.2f} t/s")
g=(q[1]/b[1]-1)*100
print(f"tg gain {g:+.1f} %  pp gain {(q[0]/b[0]-1)*100:+.1f} %  ->  {'MERGE' if g>=15 else 'keep base'}")
PY
  ;;

profile)
  # 128 generated tokens with the op profiler; output sorted by wall time.
  # "barrier ms" = time thread 0 waited for the slowest thread after that op (imbalance + sync).
  drop
  GGML_CPU_OP_PROFILE=1 OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores \
  numactl --physcpubind="$(pcores)" --membind="$NODE" "$SRC/build/bin/llama-bench" -m "$M" -mmp 0 -t "$T" -fa 1 -p 0 -n 128 -r 1 \
    2> "$OUT/op_profile.txt" >/dev/null || true
  sed -n '/ggml-cpu op profile/,$p' "$OUT/op_profile.txt" | head -30
  ;;

*) sed -n '2,11p' "$0" ;;
esac
