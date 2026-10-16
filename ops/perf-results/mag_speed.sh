#!/usr/bin/env bash
# mag_speed.sh — Speed-Runbook für DeepSeek-V4-Flash UD-Q4_K_XL auf mag (2x E5-2680 v4, AVX2, 2 NUMA-Knoten)
#
# Jeder Schritt misst mit llama-bench (-r 5, pp512 + tg128, Knoten 0) und vergleicht mit der Baseline.
# Ergebnis landet in $OUT/results.tsv. Ein Schritt gilt als Gewinn, wenn tg >= +15 % über Baseline liegt.
#
# Reihenfolge:
#   sudo ./mag_speed.sh prep      # Kernel-Basis (Governor, C-States, numa_balancing, THP)
#   ./mag_speed.sh build          # llama.cpp (OpenMP + Threadpool) und ik_llama.cpp bauen
#   ./mag_speed.sh base           # Baseline: aktuelles Setup nachstellen (t16, Knoten 0)
#   ./mag_speed.sh threads        # C: Pinning / OpenMP-Spin / ggml-Threadpool
#   ./mag_speed.sh ik             # A: ik_llama -rtr (Q8_0 -> Q8_0_R8) + -fmoe
#   ./mag_speed.sh types          # Tensor-Typen des Modells anzeigen (vor requant)
#   ./mag_speed.sh requant        # B: dichte Q8_0-Tensoren -> Q6_K, Experten unverändert
#   ./mag_speed.sh ikq            # A+B: requantisiertes Modell auf ik_llama
#   ./mag_speed.sh ppl            # Qualität: Perplexity Original vs. requantisiert
#   ./mag_speed.sh report         # Tabelle + Startbefehl für den Gewinner
#
# Nichts hier ersetzt den laufenden Server. Ali entscheidet anhand von report, was übernommen wird.
set -euo pipefail

# ---------- Konfiguration (anpassen) ----------
M="${M:-/models/DeepSeek-V4-Flash-UD-Q4_K_XL.gguf}"        # Original-Modell (bei Split-GGUF: erste Datei -00001-of-…)
MQ="${MQ:-/models/DeepSeek-V4-Flash-dense-q6k.gguf}"       # Ziel der Requantisierung (~ -15 GB ggü. Original)
NODE="${NODE:-0}"
SRC="${SRC:-$HOME/speed}"                                   # Build-Verzeichnis
OUT="${OUT:-$HOME/speed/results}"
GAIN_MIN="${GAIN_MIN:-15}"
REPS="${REPS:-5}"
mkdir -p "$SRC" "$OUT"
RES="$OUT/results.tsv"
[ -f "$RES" ] || printf "stage\tlabel\tpp512_ts\ttg128_ts\tgain_tg_pct\tcmd\n" > "$RES"

LL="$SRC/llama.cpp"; IK="$SRC/ik_llama.cpp"

# physische Kerne von Knoten $NODE (ohne HT-Geschwister), z. B. "0,1,2,…,13"
pcores() { lscpu -p=CPU,CORE,NODE | grep -v '^#' | awk -F, -v n="$NODE" '$3==n && !seen[$2]++ {print $1}' | paste -sd,; }
allcpus() { lscpu -p=CPU,NODE | grep -v '^#' | awk -F, -v n="$NODE" '$2==n {print $1}' | paste -sd,; }

drop_caches() { sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }

# bench <stage> <label> <binary> [extra llama-bench args…]   (Env-Variablen vorher exportieren)
bench() {
  local stage="$1" label="$2" bin="$3"; shift 3
  local model="${BENCH_MODEL:-$M}" bind="${BIND:-phys}" cpus json
  [ "$bind" = phys ] && cpus="$(pcores)" || cpus="$(allcpus)"
  json="$OUT/${stage}_${label}.json"
  drop_caches
  local cmd=(numactl --physcpubind="$cpus" --membind="$NODE" "$bin" -m "$model" -mmp 0 --numa numactl
             -p 512 -n 128 -r "$REPS" -o json "$@")
  echo ">> $stage/$label: ${ENVSTR:-} ${cmd[*]}"
  "${cmd[@]}" > "$json" 2> "$OUT/${stage}_${label}.log" || { echo "!! fehlgeschlagen, siehe $OUT/${stage}_${label}.log"; return 1; }
  python3 - "$json" "$RES" "$stage" "$label" "${ENVSTR:-} ${cmd[*]}" "$GAIN_MIN" <<'PY'
import json,sys,csv
js,res,stage,label,cmd,gmin=sys.argv[1:7]
rows=json.load(open(js))
pp=next((r["avg_ts"] for r in rows if r.get("n_gen",0)==0),0.0)
tg=next((r["avg_ts"] for r in rows if r.get("n_prompt",0)==0),0.0)
base=None
for r in csv.DictReader(open(res),delimiter="\t"):
    if r["stage"]=="base" and r["label"]=="t16": base=float(r["tg128_ts"])
gain="" if not base else f"{(tg/base-1)*100:+.1f}"
with open(res,"a") as f: f.write(f"{stage}\t{label}\t{pp:.2f}\t{tg:.2f}\t{gain}\t{cmd}\n")
flag=""
if gain: flag="  >= +%s %% -> GEWINN"%gmin if float(gain)>=float(gmin) else "  (unter Schwelle)"
print(f"   pp512 {pp:.2f} t/s | tg128 {tg:.2f} t/s | vs base {gain or 'n/a'} %{flag}")
PY
}

has_flag() { "$1" --help 2>&1 | grep -q -- "$2"; }

case "${1:-}" in

prep)  # ---------- Kernel-Basis (einmal, als root) ----------
  cpupower frequency-set -g performance
  cpupower idle-set -D 0 || true                         # tiefe C-States aus (dauerhaft: intel_idle.max_cstate=1 im Kernel-cmdline)
  echo 0 > /proc/sys/kernel/numa_balancing
  echo always > /sys/kernel/mm/transparent_hugepage/enabled
  echo always > /sys/kernel/mm/transparent_hugepage/defrag
  ulimit -l unlimited || true
  echo "RAM-Takt (2 DIMMs/Kanal taktet oft runter):"; dmidecode -t memory | grep -E "Configured Memory Speed" | sort | uniq -c
  numactl -H | head -4
  ;;

build) # ---------- Builds ----------
  [ -d "$LL" ] || git clone https://github.com/ggml-org/llama.cpp "$LL"
  git -C "$LL" pull --ff-only
  cmake -S "$LL" -B "$LL/build-omp"  -DGGML_NATIVE=ON -DGGML_OPENMP=ON  -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
  cmake -S "$LL" -B "$LL/build-pool" -DGGML_NATIVE=ON -DGGML_OPENMP=OFF -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
  cmake --build "$LL/build-omp"  -j"$(nproc)" --target llama-bench llama-server llama-quantize llama-perplexity
  cmake --build "$LL/build-pool" -j"$(nproc)" --target llama-bench llama-server
  [ -d "$IK" ] || git clone https://github.com/ikawrakow/ik_llama.cpp "$IK"
  git -C "$IK" pull --ff-only
  cmake -S "$IK" -B "$IK/build" -DGGML_NATIVE=ON -DGGML_AVX2=ON -DGGML_IQK_FA_ALL_QUANTS=ON -DCMAKE_BUILD_TYPE=Release
  cmake --build "$IK/build" -j"$(nproc)" --target llama-bench llama-server llama-quantize llama-perplexity
  echo "Commits: llama.cpp $(git -C "$LL" rev-parse --short HEAD), ik_llama $(git -C "$IK" rev-parse --short HEAD)"
  ;;

base)  # ---------- Baseline = heutiges Setup (Knoten 0, t16 inkl. HT) ----------
  # Messkriterium: Referenz für alle weiteren Gains (erwartet ~3,6 t/s tg)
  BIND=all bench base t16 "$LL/build-omp/bin/llama-bench" -t 16
  BIND=phys bench base t14 "$LL/build-omp/bin/llama-bench" -t 14
  ;;

threads) # ---------- C: Barrier-Spin / Pinning ----------
  # Messkriterium: tg >= +15 % ggü. base/t16. Erwartung +5–15 %.
  export OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores GOMP_SPINCOUNT=infinite
  ENVSTR="OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores GOMP_SPINCOUNT=infinite"
  for t in 12 13 14; do BIND=phys bench threads "omp_t$t" "$LL/build-omp/bin/llama-bench" -t "$t"; done
  unset OMP_WAIT_POLICY OMP_PROC_BIND OMP_PLACES GOMP_SPINCOUNT; ENVSTR=""
  # ggml-eigener Threadpool (ohne OpenMP): striktes Pinning + Polling
  POOL="$LL/build-pool/bin/llama-bench"
  if has_flag "$POOL" "--cpu-strict"; then
    for t in 13 14; do BIND=phys bench threads "pool_t$t" "$POOL" -t "$t" --cpu-strict 1 --poll 100 --prio 2; done
  else
    BIND=phys bench threads pool_t14 "$POOL" -t 14
  fi
  ;;

ik)    # ---------- A: ik_llama.cpp, Q8_0 -> Q8_0_R8 beim Laden, fused MoE ----------
  # Messkriterium: tg >= +15 % ggü. base/t16 UND im Log steht ein R8/R4-Repack der Q8_0-Tensoren.
  IKB="$IK/build/bin/llama-bench"
  export OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores
  ENVSTR="OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores"
  BIND=phys bench ik rtr_fmoe_t14 "$IKB" -t 14 -fa 1 -fmoe 1 -rtr 1
  BIND=all  bench ik rtr_fmoe_t16 "$IKB" -t 16 -fa 1 -fmoe 1 -rtr 1
  BIND=phys bench ik fmoe_only_t14 "$IKB" -t 14 -fa 1 -fmoe 1           # Anteil von -rtr isolieren
  echo "Repack-Check (soll q8_0_r8 o. ä. zeigen):"; grep -iE "repack|_r8|_r4" "$OUT/ik_rtr_fmoe_t14.log" | head -5 || echo "!! kein Repack im Log – -rtr greift für dieses Modell nicht"
  grep -qi "unknown model architecture" "$OUT/ik_rtr_fmoe_t14.log" && echo "!! ik_llama kennt DeepSeek-V4 nicht -> Schritt ik entfällt, weiter mit requant + llama.cpp"
  ;;

types) # ---------- Tensor-Typen zählen (vor requant ansehen) ----------
  python3 -m pip show gguf >/dev/null 2>&1 || python3 -m pip install --user "$LL/gguf-py"
  python3 - "$M" <<'PY'
import sys,collections,re
from gguf import GGUFReader
r=GGUFReader(sys.argv[1]); c=collections.Counter(); b=collections.Counter()
for t in r.tensors:
    kind=re.sub(r"blk\.\d+\.","blk.N.",t.name)
    c[(kind,t.tensor_type.name)]+=1; b[t.tensor_type.name]+=int(t.n_bytes)
for (k,ty),n in sorted(c.items()): print(f"{ty:8s} x{n:4d}  {k}")
print("\nGB pro Typ:", {k: round(v/1e9,1) for k,v in b.items()})
PY
  ;;

requant) # ---------- B: dichte Q8_0-Tensoren -> Q6_K, alles andere bleibt Byte für Byte ----------
  # Erzeugt pro Tensor eine Typ-Vorgabe: Q8_0 (nicht-Experten) -> q6_k, alle anderen = bisheriger Typ.
  # Damit werden die MXFP4-Experten nicht angefasst.
  # Messkriterium: Datei ~10–20 GB kleiner; danach Schritt ikq und ppl.
  [ "$(df --output=avail -B1G "$(dirname "$MQ")" | tail -1)" -gt 160 ] || { echo "!! <160 GB frei auf $(dirname "$MQ")"; exit 1; }
  ARGS="$OUT/requant_args.txt"
  python3 - "$M" > "$ARGS" <<'PY'
import sys,re
from gguf import GGUFReader
r=GGUFReader(sys.argv[1]); n=0
for t in r.tensors:
    ty=t.tensor_type.name.lower()
    dense_q8 = ty=="q8_0" and "_exps" not in t.name and t.name not in ("token_embd.weight",)
    new = "q6_k" if dense_q8 else ty
    if dense_q8: n+=1
    print("--tensor-type"); print("^"+re.escape(t.name)+"$="+new)
sys.stderr.write(f"{n} dichte Q8_0-Tensoren -> q6_k\n")
PY
  # Basis-Ftype ist egal, weil jeder Tensor explizit vorgegeben ist; --allow-requantize nötig (Quelle ist quantisiert)
  # Wenn llama-quantize einen Typ (z. B. mxfp4) nicht als --tensor-type akzeptiert: Fehlermeldung -> ik_llama-quantize mit --custom-q nehmen (unten).
  xargs -d '\n' -a "$ARGS" "$LL/build-omp/bin/llama-quantize" --allow-requantize "$M" "$MQ" Q6_K "$(nproc)" \
    2>&1 | tee "$OUT/requant.log" | grep -E "q6_K|q8_0|size|error" | tail -20
  ls -l --block-size=G "$M" "$MQ" 2>/dev/null || true
  # Alternative (ik_llama), falls oben scheitert:
  #   "$IK/build/bin/llama-quantize" --allow-requantize --custom-q "attn_.*=q6_k,ffn_.*_shexp=q6_k" "$M" "$MQ" Q6_K
  ;;

ikq)   # ---------- A+B: requantisiertes Modell ----------
  # Messkriterium: tg >= +15 % ggü. base/t16 (Ziel: ~5 t/s). Falls ik nicht lädt: llama.cpp-Zeile zählt.
  export OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores
  ENVSTR="OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores"
  BENCH_MODEL="$MQ" BIND=phys bench ikq ik_rtr_fmoe_t14 "$IK/build/bin/llama-bench" -t 14 -fa 1 -fmoe 1 -rtr 1 || true
  BENCH_MODEL="$MQ" BIND=phys bench ikq llamacpp_t14   "$LL/build-omp/bin/llama-bench" -t 14
  ;;

ppl)   # ---------- Qualität: Perplexity (Original vs. requant) ----------
  # Messkriterium: PPL(requant) <= PPL(original) * 1.01  (max. +1 %). Sonst requant verwerfen.
  [ -f "$SRC/wiki.test.raw" ] || (cd "$SRC" && bash "$LL/scripts/get-wikitext-2.sh" && mv wikitext-2-raw/wiki.test.raw .)
  for m in "$M" "$MQ"; do
    drop_caches
    numactl --physcpubind="$(pcores)" --membind="$NODE" "$LL/build-omp/bin/llama-perplexity" -m "$m" --no-mmap \
      -t 14 -f "$SRC/wiki.test.raw" --chunks 64 -c 512 2>&1 | tee "$OUT/ppl_$(basename "$m").log" | grep -E "Final estimate"
  done
  ;;

report) # ---------- Auswertung + Startbefehl ----------
  column -t -s $'\t' <(cut -f1-5 "$RES")
  best=$(tail -n +2 "$RES" | sort -t$'\t' -k4,4nr | head -1)
  echo; echo "Bester Lauf: $(echo "$best" | cut -f1,2,4,5)"
  echo "Befehl:     $(echo "$best" | cut -f6)"
  cat <<EOF

Startbefehl llama-server nach Muster des Gewinners (Beispiel ik_llama + requant, Knoten 0):
  OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores \\
  numactl --physcpubind=$(pcores) --membind=$NODE \\
  $IK/build/bin/llama-server -m $MQ --no-mmap --mlock --numa numactl \\
    -t 14 -tb 14 -fa -fmoe -rtr -ub 512 -b 2048 -ctk q8_0 -ctv q8_0 -c 32768 \\
    --host 127.0.0.1 --port 8080
Zweite Instanz auf Knoten 1 (Durchsatz x2 für die Bots): NODE=1, --membind=1, eigener Port.
EOF
  ;;

*) sed -n '2,22p' "$0" ;;
esac
