#!/usr/bin/env bash
# Startwerte von mag-llm auf mag einmessen – nur per HTTP gegen den laufenden Server, kein zweiter
# Modellprozess. Jede Variante = Neustart von llama mit anderen Flags (aus dem RAM dank mag-model-pin),
# warten bis bereit, messen. Die beste Kombination landet in /etc/mag-llm/llm.env und bleibt aktiv.
#
#   sudo bash ops/deepseek/tune.sh                 Phase A (Threads, Generierung) + Phase B (Batch, Prompt)
#   sudo bash ops/deepseek/tune.sh --threads-only  nur Phase A
# Phase A: --threads 28 / 24 / 20 / 16          (threads-batch 28, batch = ubatch 1024) -> Generierung
# Phase B: (threads-batch, ubatch) = 28/1024, 56/1024, 28/2048, 28/512  (beste Threads) -> Prompt-Lesen
# Ergebnis: /var/tmp/mag-tune-<zeit>.md. Der Raum zeigt während der Neustarts "Modell lädt".
# Umgebung: TUNE_THREADS="28 24 20 16"  TUNE_PP="28:1024 56:1024 28:2048 28:512"  URL=…  READY_TIMEOUT=1800
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
# shellcheck source=llm-config.sh
source "$SRC/ops/deepseek/llm-config.sh"
UNIT=${UNIT:-/etc/systemd/system/mag-llm.service}
URL=${URL:-http://127.0.0.1:8080/v1/chat/completions}
HEALTH=${HEALTH:-http://127.0.0.1:8090/health}
STATE_URL=${STATE_URL:-http://127.0.0.1:8766/api/state}
READY_TIMEOUT=${READY_TIMEOUT:-1800}
TUNE_THREADS=${TUNE_THREADS:-"28 24 20 16"}
TUNE_PP=${TUNE_PP:-"28:1024 56:1024 28:2048 28:512"}
OUT=/var/tmp/mag-tune-$(date +%Y%m%d-%H%M).md
PHASE_B=1; [[ "${1:-}" == "--threads-only" ]] && PHASE_B=0
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }

health() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$HEALTH" 2>/dev/null; true; }
unlock() { install -d /run/systemd/system/mag-llm.service.d
           printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/mag-llm.service.d/99-tune.conf; systemctl daemon-reload; }
relock() { rm -f /run/systemd/system/mag-llm.service.d/99-tune.conf; systemctl daemon-reload; }
trap relock EXIT

wait_idle() {   # Raum und llama müssen ruhen, sonst verfälscht jede Nutzeranfrage die Messung
  local i busy slots
  for i in $(seq 1 120); do
    busy=$(curl -fsS --max-time 3 "$STATE_URL" 2>/dev/null | python3 -I -c 'import json,sys;print(json.load(sys.stdin).get("busy"))' 2>/dev/null || echo "?")
    slots=$(curl -fsS --max-time 3 "${URL%/v1/chat/completions}/slots" 2>/dev/null || echo "[]")
    if [[ "$busy" != True ]] && python3 -I -c 'import json,sys; s=json.loads(sys.argv[1]); sys.exit(1 if any(x.get("is_processing") for x in s) else 0)' "$slots" 2>/dev/null; then
      return 0
    fi
    (( i == 1 )) && echo "   warte, bis Raum/llama ruhen …" >&2
    sleep 5
  done
  echo "   Raum/llama 10 min beschäftigt – messe trotzdem (Ergebnis ggf. verfälscht)" >&2
}

wait_ready() {
  local deadline=$((SECONDS + READY_TIMEOUT)) r0 r
  r0=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
  while (( SECONDS < deadline )); do
    [[ "$(health)" == 200 ]] && return 0
    r=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
    (( r - r0 >= 2 )) && { echo "   llama stürzt mit diesen Werten ab"; return 1; }
    sleep 5
  done
  echo "   nicht bereit nach ${READY_TIMEOUT}s"; return 1
}

CUR=""   # aktuell laufende Kombination "t:tb:b:ub"
start_cfg() {   # t tb b ub -> 0 wenn bereit
  local key="$1:$2:$3:$4" t0=$SECONDS
  if [[ "$key" == "$CUR" ]]; then return 0; fi
  echo "   Neustart mit threads $1 / threads-batch $2 / batch $3 / ubatch $4 …"
  THREADS=$1 THREADS_BATCH=$2 BATCH=$3 UBATCH=$4 render_unit "$UNIT" || return 1
  unlock; systemctl restart mag-llm.service; relock
  if wait_ready; then CUR=$key; echo "   bereit nach $(( (SECONDS - t0) / 60 )) min $(( (SECONDS - t0) % 60 )) s"; return 0; fi
  CUR=""; return 1
}

measure() {   # $1 = tg|pp -> druckt "pp_t/s tg_t/s"
  wait_idle
  python3 -I - "$URL" "$1" <<'PY'
import json, statistics, sys, time, urllib.request
url, kind = sys.argv[1], sys.argv[2]
W = "Konstanz Bodensee Wohnung Prozess Planung Server Speicher Modell Messung Ergebnis Mieter Vertrag Heizung Wartung Termin Bericht".split()
def call(n_words, max_tokens, salt):
    text = f"Lauf {salt} {time.time_ns()}: " + " ".join(W[(i * 7 + salt) % len(W)] for i in range(n_words)) + "\nFasse kurz zusammen."
    req = {"model": "deepseek-v4-flash-0731", "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens,
           "temperature": 0, "stream": False, "cache_prompt": False, "ignore_eos": True,
           "chat_template_kwargs": {"enable_thinking": False}}
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, json.dumps(req).encode(), {"Content-Type": "application/json"}), timeout=3600))
    t = d.get("timings") or {}
    return t.get("prompt_per_second") or 0.0, t.get("predicted_per_second") or 0.0
if kind == "tg":
    r = [call(30, 128, s) for s in (1, 2)]
else:
    r = [call(1300, 4, 3)]          # ~1.700 Prompt-Tokens: mehrere Mikro-Batches bei ubatch 512/1024
print("%.2f %.2f" % (statistics.median(x[0] for x in r), statistics.median(x[1] for x in r)))
PY
}

llm_config_load
start_t=$THREADS; start_tb=$THREADS_BATCH; start_b=$BATCH; start_ub=$UBATCH
CUR="$THREADS:$THREADS_BATCH:$BATCH:$UBATCH"
grep -qF -- "--threads $THREADS " "$UNIT" || CUR=""      # installierte Unit weicht ab -> erst neu starten
{ echo "# mag-llm Tuning $(date -Is)"; echo; echo "Ausgang: threads $THREADS / threads-batch $THREADS_BATCH / batch $BATCH / ubatch $UBATCH"; echo; } > "$OUT"

echo "== Phase A: Threads für die Generierung"
echo "| threads | Prompt t/s | Generierung t/s |" >> "$OUT"; echo "|---:|---:|---:|" >> "$OUT"
best_t=""; best_tg=0
for t in $TUNE_THREADS; do
  if ! start_cfg "$t" 28 1024 1024; then echo "| $t | Absturz/nicht bereit | – |" >> "$OUT"; continue; fi
  read -r pp tg < <(measure tg)
  echo "   threads $t: Generierung $tg t/s (Prompt $pp t/s)"
  echo "| $t | $pp | $tg |" >> "$OUT"
  # neuer Bestwert nur, wenn mehr als 2 % besser (Rauschen); bei Gleichstand gewinnt der frühere (mehr Threads)
  if python3 -I -c "import sys; sys.exit(0 if float('$tg') > float('$best_tg') * 1.02 else 1)"; then best_t=$t; best_tg=$tg; fi
done
[[ -n "$best_t" ]] || { echo "Keine Variante lief – zurück auf den Ausgang"; start_cfg "$start_t" "$start_tb" "$start_b" "$start_ub" || true; exit 1; }
echo "   -> beste Threads: $best_t ($best_tg t/s)"

best_tb=28; best_ub=1024
if [[ $PHASE_B -eq 1 ]]; then
  echo "== Phase B: threads-batch / ubatch für das Prompt-Lesen (threads $best_t)"
  { echo; echo "| threads-batch | ubatch | Prompt t/s | Generierung t/s |"; echo "|---:|---:|---:|---:|"; } >> "$OUT"
  best_pp=0
  for pair in $TUNE_PP; do
    tb=${pair%%:*}; ub=${pair##*:}; b=$ub   # batch = ubatch: Abbruch spätestens nach einem Mikro-Batch
    if ! start_cfg "$best_t" "$tb" "$b" "$ub"; then echo "| $tb | $ub | Absturz/nicht bereit | – |" >> "$OUT"; continue; fi
    read -r pp tg < <(measure pp)
    echo "   threads-batch $tb, ubatch $ub: Prompt $pp t/s"
    echo "| $tb | $ub | $pp | $tg |" >> "$OUT"
    if python3 -I -c "import sys; sys.exit(0 if float('$pp') > float('$best_pp') * 1.02 else 1)"; then best_tb=$tb; best_ub=$ub; best_pp=$pp; fi
  done
  echo "   -> bestes Prompt-Lesen: threads-batch $best_tb, ubatch $best_ub ($best_pp t/s)"
fi

final_b=$best_ub
echo "== Ergebnis übernehmen: threads $best_t / threads-batch $best_tb / batch $final_b / ubatch $best_ub"
THREADS=$best_t THREADS_BATCH=$best_tb BATCH=$final_b UBATCH=$best_ub
start_cfg "$THREADS" "$THREADS_BATCH" "$BATCH" "$UBATCH" || { echo "Endwerte starten nicht – zurück auf den Ausgang"; start_cfg "$start_t" "$start_tb" "$start_b" "$start_ub" || true; exit 1; }
export THREADS THREADS_BATCH BATCH UBATCH
llm_config_save
{ echo; echo "**Übernommen:** threads $THREADS / threads-batch $THREADS_BATCH / batch $BATCH / ubatch $UBATCH (gespeichert in $MAG_LLM_ENV)"; } >> "$OUT"
echo "Tabelle: $OUT"; cat "$OUT"
