#!/usr/bin/env bash
# Benchmark-Matrix für DeepSeek-V4-Flash auf mag (CPU): Threads, NUMA-Strategie, Batchgrößen.
# Nutzt dieselbe Modelldatei per mmap -> teilt sich die bereits geladenen Seiten mit mag-llm
# (kein zweites 155-GB-Laden). Konkurriert aber um CPU: nur laufen lassen, wenn der Raum ruht.
#
#   sudo bash ops/deepseek/bench.sh            Standard-Matrix (~30–60 min), Ergebnis als Tabelle
#   sudo bash ops/deepseek/bench.sh --quick    nur Threads + NUMA (~15 min)
# Ausgabe: /var/tmp/mag-bench-<zeit>.md  (bitte zurückschicken)
set -euo pipefail
BIN=/home/chris/.local/bin/llama
MODEL=/home/chris/models/deepseek-v4/DeepSeek-V4-Flash-0731-UD-Q4_K_XL-00001-of-00005.gguf
OUT=/var/tmp/mag-bench-$(date +%Y%m%d-%H%M).md
QUICK=0; [[ "${1:-}" == "--quick" ]] && QUICK=1
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
[[ -r $MODEL ]] || { echo "Modell fehlt: $MODEL"; exit 1; }

# llama-bench als Unterbefehl ("llama bench") oder eigenes Binary
if runuser -u chris -- "$BIN" bench --help >/dev/null 2>&1; then BENCH=("$BIN" bench)
elif [[ -x /home/chris/.local/bin/llama-bench ]]; then BENCH=(/home/chris/.local/bin/llama-bench)
else echo "kein llama bench gefunden ($BIN bench --help / llama-bench)"; exit 1; fi
command -v numactl >/dev/null || apt-get install -y numactl >/dev/null

busy=$(curl -fsS --max-time 3 http://127.0.0.1:8766/api/state 2>/dev/null | python3 -I -c 'import json,sys;print(json.load(sys.stdin).get("busy"))' 2>/dev/null || echo "?")
[[ "$busy" == "True" ]] && { echo "Raum ist gerade beschäftigt – später erneut starten."; exit 1; }

{
  echo "# mag Benchmark $(date -Is)"; echo
  echo '```'; lscpu | grep -E 'Model name|Socket|Core\(s\)|Thread\(s\)|NUMA node'; numactl --hardware | head -3; "${BENCH[@]}" --version 2>&1 | head -2 || true; echo '```'
} > "$OUT"

run() {   # Beschreibung | Präfix (numactl…) | llama-bench-Argumente
  local desc=$1; shift; local pre=$1; shift
  echo "== $desc"
  echo "### $desc" >> "$OUT"
  # shellcheck disable=SC2086
  if ! runuser -u chris -- $pre "${BENCH[@]}" -m "$MODEL" -o md "$@" 2>/dev/null | tee -a "$OUT"; then
    echo "(fehlgeschlagen)" | tee -a "$OUT"
  fi
  echo >> "$OUT"
}

# Generierung (tg) ist speicherbandbreitengebunden, Prompt-Verarbeitung (pp) rechengebunden.
COMMON=(-p 512 -n 64 -r 2 -fa 1)
# 1) Threads (physische Kerne 28, logisch 56) mit dem aktuellen NUMA-Modus
run "Threads, --numa distribute" "" "${COMMON[@]}" --numa distribute -t 14,20,24,28,42,56
# 2) NUMA-Strategien bei 28 Threads
run "NUMA: interleave (numactl --interleave=all)" "numactl --interleave=all" "${COMMON[@]}" --numa numactl -t 28
run "NUMA: nur Sockel 0 (14 Kerne, lokaler Speicher)" "numactl -N 0 -m 0" "${COMMON[@]}" --numa numactl -t 14
run "NUMA: isolate" "" "${COMMON[@]}" --numa isolate -t 14
if [[ $QUICK -eq 0 ]]; then
  # 3) Batchgrößen für die Prompt-Verarbeitung (der Raum schickt lange Prompts)
  run "pp: ubatch/batch" "" -p 2048 -n 0 -r 1 -fa 1 --numa distribute -t 28 -ub 256,512,1024,2048 -b 2048
  # 4) Flash-Attention an/aus
  run "Flash-Attention" "" "${COMMON[@]}" --numa distribute -t 28 -fa 0,1
  # 5) Tiefer Kontext (Raum-Prompts mit Verlauf)
  run "Kontexttiefe 8k" "" -p 512 -n 64 -r 1 -fa 1 --numa distribute -t 28 -d 8192
fi
echo; echo "Ergebnis: $OUT"
