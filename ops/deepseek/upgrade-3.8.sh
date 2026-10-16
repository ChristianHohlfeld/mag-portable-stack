#!/usr/bin/env bash
# Runde 3.8 auf mag (als root, aus dem Repo). Aufgerufen von apply.sh --install.
# Engine-Wechsel auf ik_llama.cpp + mag-eigene Anpassung an genau diese Hardware (2x E5-2680 v4, AVX2, 2 NUMA-Knoten).
# Gleiches Modell, gleiche GGUF-Dateien, kein Download. Das Modell bleibt die ganze Zeit im RAM (Pin).
#
#  0. Vorprüfung (llama bereit, Pin aktiv, Werkzeuge, Speicher je Knoten) – bricht ab, bevor etwas verändert wird
#  1. Sicherung von Unit und /etc/mag-llm/llm.env
#  2. ik_llama.cpp bauen (build-ik.sh: fester Stand + mag-Patches: prompt_progress, NUMA-lokal), nice/ionice
#  3. A/B per HTTP (http-bench --quick, ohne Cache-Test), jeweils Neustart aus dem RAM:
#       alt             laufender Stand (llama.cpp)
#       ik              ik_llama.cpp, -rtr (Q8_0->Q8_0_R8, MXFP4->MXFP4_R8), Threads wie bisher
#                       + Schnelltest (17+25=42), sonst sofort zurück
#       ik-numa         + NUMA-lokal: Zeilenblöcke jeder Matrix im RAM des Sockels, dessen Threads sie rechnen
#       ik-numa-thp     + Transparent Huge Pages für die Gewichte (2-MiB-Raster)
#       ik-numa-t28     NUMA-lokal mit allen 28 Kernen für die Generierung
#       <beste>+muge    gemergte up/gate-Experten (ein Matmul statt zwei)
#       ik-mmap         ohne -rtr: Gewichte direkt aus dem Page-Cache (eine Kopie weniger)
#  4. Auswahl: Punktzahl 0,5*tg/tg_alt + 0,5*pp/pp_alt. ik nur, wenn >= 5 % besser als alt und die
#     Generierung nicht langsamer; ik-mmap, wenn höchstens 3 % hinter der besten (spart ~144 GiB).
#  5. Go/No-Go: preflight-tests.py --quick gegen die ausgewählte Variante (Thinking, Tool-Calls DSML,
#     Streaming, Audit). NO-GO -> automatisch zurück auf den Stand vor 3.8.
#  6. Profil (perf, mit Symbolen) + Lokalität + Speicher
#
#   sudo bash ops/deepseek/upgrade-3.8.sh               Inbetriebnahme (~1,5–2 h, Raum zwischendurch "Modell lädt")
#   sudo bash ops/deepseek/upgrade-3.8.sh --rollback    Unit und llm.env vor 3.8 wiederherstellen
# Umgebung: IK_REF, MAG_UPGRADE_SKIP_PROFILE=1, MAG_UPGRADE_SKIP_PREFLIGHT=1 (nicht empfohlen),
#           MAG_UPGRADE_VARIANTS="ik ik-numa ..." (Teilmenge; ik ist immer dabei)
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
# shellcheck source=llm-config.sh
source "$SRC/ops/deepseek/llm-config.sh"
UNIT=${MAG_TEST_UNIT:-/etc/systemd/system/mag-llm.service}
BACKUP_ROOT=${BACKUP_ROOT:-/home/chris/mag-infra/systemd}
BACKUP=$BACKUP_ROOT/upgrade-3.8-$(date +%Y%m%d%H%M%S)
TS=$(date +%Y%m%d-%H%M)
LOG=/var/tmp/mag-upgrade-3.8-$TS.log
SUMMARY=/var/tmp/mag-3.8-ergebnis-$TS.md
GW=${GW:-http://127.0.0.1:8080}
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
exec > >(tee -a "$LOG") 2>&1
MODE=${1:-}
say() { printf '\n== %s  [%s]\n' "$1" "$(date +%H:%M:%S)"; }
health() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health 2>/dev/null; true; }
unlock() { install -d /run/systemd/system/mag-llm.service.d
           printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload; }
relock() { rm -f /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload; }
VARIANT_DROPIN=${MAG_TEST_DROPIN:-/etc/systemd/system/mag-llm.service.d/60-mag-variant.conf}   # aus 3.7; ab 3.8 nie gesetzt
# mag-Fix (Ali, 16.10.): seit 9e7bbcc läuft ik per Drop-in 50-ik.conf; der überschreibt ExecStart der gerenderten Unit.
IK_DROPIN=${MAG_TEST_IK_DROPIN:-/etc/systemd/system/mag-llm.service.d/50-ik.conf}
MIN_AVAIL_GIB=${MAG_UPGRADE_MIN_AVAIL_GIB:-40}

wait_ready() {   # $1 = Sekunden
  local deadline=$((SECONDS + $1)) last=0 code r0 r
  r0=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
  while (( SECONDS < deadline )); do
    code=$(health)
    [[ "$code" == 200 ]] && return 0
    r=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
    (( r - r0 >= 2 )) && { echo "   mag-llm ist $((r - r0))x abgestürzt"; return 1; }
    if (( SECONDS - last >= 60 )); then last=$SECONDS; printf '   %s  /health=%s\n' "$(date +%H:%M:%S)" "$code"; fi
    sleep 5
  done
  echo "   Zeitlimit erreicht"; return 1
}

restore() {   # $1 = Exit-Code danach
  local rc=${1:-1}
  say "ROLLBACK: Unit und llm.env aus $BACKUP"
  [[ -f "$BACKUP/mag-llm.service" ]] || { echo "keine Sicherung in $BACKUP"; exit 1; }
  if [[ $rc -ne 0 ]]; then journalctl -u mag-llm -n 40 --no-pager || true; fi
  cp -a "$BACKUP/mag-llm.service" "$UNIT"
  if [[ -f "$BACKUP/60-mag-variant.conf" ]]; then cp -a "$BACKUP/60-mag-variant.conf" "$VARIANT_DROPIN"; else rm -f "$VARIANT_DROPIN"; fi
  if [[ -f "$BACKUP/llm.env" ]]; then cp -a "$BACKUP/llm.env" "$MAG_LLM_ENV"; else rm -f "$MAG_LLM_ENV"; fi
  if [[ -f "$BACKUP/50-ik.conf" ]]; then cp -a "$BACKUP/50-ik.conf" "$IK_DROPIN"; fi
  unlock; systemctl restart mag-llm.service; relock
  if wait_ready 7200; then echo "Stand vor 3.8 läuft wieder. Log: $LOG"; else echo "Stand vor 3.8 nicht bereit – journalctl -u mag-llm prüfen"; exit 1; fi
  exit "$rc"
}

# Variante starten: $1 Name, $2 IK_RTR, $3 IK_ENV, $4 IK_ARGS, $5 THREADS, $6 THREADS_BATCH
start_ik() {
  local name=$1 rtr=$2 ikenv=$3 ikargs=$4 th=$5 tb=$6 t0=$SECONDS since
  echo "   Neustart: $name (rtr=$rtr${ikenv:+, $ikenv}${ikargs:+, $ikargs}, threads $th/$tb)"
  rm -f "$VARIANT_DROPIN" "$IK_DROPIN"
  ENGINE=ik IK_BIN=$IK_NEW IK_RTR=$rtr IK_ENV=${ikenv:--} IK_ARGS=${ikargs:--} NUMA_ARGS=- LLAMA_NUMA=- \
    THREADS=$th THREADS_BATCH=$tb render_unit "$UNIT" || return 1
  since=$(date '+%Y-%m-%d %H:%M:%S')
  unlock; systemctl restart mag-llm.service; relock
  wait_ready 5400 || return 1
  echo "   bereit nach $(( (SECONDS - t0) / 60 )) min $(( (SECONDS - t0) % 60 )) s"
  journalctl -u mag-llm --since "$since" --no-pager -o cat 2>/dev/null | grep -E 'mag_numa:|Repacked|merging up/gate in layer 0' \
    | sed 's/^/   /' | tee "/var/tmp/mag-3.8-load-$name-$TS.txt" || true
}

bench() {   # $1 = Label -> schreibt /var/tmp/mag-httpbench-*-<label>.md
  runuser -u chris -- env LABEL="$1" SKIP_CACHE=1 bash "$SRC/ops/deepseek/http-bench.sh" --quick | tail -6
}
metric() {   # $1 = Label, $2 = Zeile (tg128|pp512), $3 = Spalte (pp|tg) -> Zahl oder 0
  local f; f=$(ls -t /var/tmp/mag-httpbench-*-"$1".md 2>/dev/null | head -1 || true)
  [[ -n "$f" ]] || { echo 0; return; }
  python3 -I -c '
import sys
f, row, col = sys.argv[1], sys.argv[2], sys.argv[3]
for l in open(f, encoding="utf-8"):
    c = [x.strip() for x in l.strip().strip("|").split("|")]
    if len(c) == 7 and c[0] == row:
        print(c[2] if col == "pp" else c[4]); break
else:
    print(0)' "$f" "$2" "$3"
}
sanity() {   # kurzer Korrektheitstest über das Gateway: 17+25 -> 42
  python3 -I - "$GW" <<'PY'
import json, sys, urllib.request
gw = sys.argv[1]
b = {"model": "deepseek-v4-flash-0731", "messages": [{"role": "user", "content": "Antworte nur mit der Zahl: Was ist 17+25?"}],
     "max_tokens": 32, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
r = urllib.request.urlopen(urllib.request.Request(gw + "/v1/chat/completions", json.dumps(b).encode(),
                           {"Content-Type": "application/json", "X-Mag-Bot": "upgrade-3.8"}), timeout=900)
d = json.load(r); c = d["choices"][0]["message"].get("content") or ""
print(f"   Schnelltest: {c.strip()[:60]!r}")
sys.exit(0 if "42" in c else 1)
PY
}
progress_check() {   # Fortschrittsanzeige im Raum: prompt_progress im Stream (ik-Patch 0001)
  python3 -I - "$GW" <<'PY' || true
import json, sys, urllib.request
gw = sys.argv[1]
b = {"model": "deepseek-v4-flash-0731", "messages": [{"role": "user", "content": "Wohnung Konstanz " * 700 + "\nEin Wort."}],
     "max_tokens": 4, "stream": True, "return_progress": True, "cache_prompt": False, "chat_template_kwargs": {"enable_thinking": False}}
r = urllib.request.urlopen(urllib.request.Request(gw + "/v1/chat/completions", json.dumps(b).encode(),
                           {"Content-Type": "application/json", "X-Mag-Bot": "upgrade-3.8"}), timeout=900)
n = 0; last = None
for raw in r:
    l = raw.decode(errors="replace").strip()
    if l.startswith("data:") and "prompt_progress" in l:
        n += 1; last = json.loads(l[5:])["prompt_progress"]
print(f"   Fortschrittsanzeige: {n} prompt_progress-Meldungen, zuletzt {last}" if n else "   WARNUNG: keine prompt_progress-Meldungen")
PY
}

if [[ "$MODE" == --rollback ]]; then
  BACKUP=${2:-$(ls -dt "$BACKUP_ROOT"/upgrade-3.8-* 2>/dev/null | head -1 || true)}
  [[ -n "$BACKUP" && -d "$BACKUP" ]] || { echo "keine Sicherung unter $BACKUP_ROOT/upgrade-3.8-*"; exit 1; }
  restore 0
fi

# ---------------------------------------------------------------- 0. Vorprüfung
say "0. Vorprüfung"
for f in ops/deepseek/build-ik.sh ops/deepseek/render-ik-unit.sh ops/deepseek/http-bench.sh ops/deepseek/llm-config.sh \
         ops/deepseek/preflight-tests.py ops/deepseek/memdiag.sh ops/deepseek/profile.sh; do
  [[ -f "$SRC/$f" ]] || { echo "fehlt: $SRC/$f"; exit 1; }
done
ls "$SRC"/ops/deepseek/ik-patches/*.patch >/dev/null 2>&1 || { echo "fehlt: ops/deepseek/ik-patches/*.patch"; exit 1; }
[[ "$(health)" == 200 ]] || { echo "mag-llm nicht bereit – erst den laufenden Stand in Ordnung bringen"; exit 1; }
systemctl is-active --quiet mag-model-pin.service || { echo "mag-model-pin läuft nicht – Neustarts würden von der Platte laden"; exit 1; }
busy=$(curl -fsS --max-time 3 http://127.0.0.1:8766/api/state 2>/dev/null | python3 -I -c 'import json,sys;print(json.load(sys.stdin).get("busy"))' 2>/dev/null || echo "?")
[[ "$busy" == True ]] && { echo "Raum ist beschäftigt – später erneut"; exit 1; }
pkgs=()
for p in git cmake ninja-build g++ binutils numactl; do dpkg -s "$p" >/dev/null 2>&1 || pkgs+=("$p"); done
dpkg -s "linux-tools-$(uname -r)" >/dev/null 2>&1 || pkgs+=("linux-tools-common" "linux-tools-$(uname -r)")
if (( ${#pkgs[@]} )); then echo "   installiere: ${pkgs[*]}"; apt-get install -y --no-install-recommends "${pkgs[@]}" >/dev/null || true; fi
df -h /home/chris | tail -1 | awk '{print "   Platz in /home/chris: " $4 " frei"}'
# Speicher: ik mit -rtr hält die Gewichte (~144 GiB) im eigenen Speicher, der Pin den Page-Cache.
# Das ist dieselbe Größenordnung wie heute (llama.cpp: Pin + Repack-Puffer). Nach dem Neustart ist der alte
# Prozess-Speicher frei: verfügbar = MemAvailable + anonymer Speicher des laufenden llama.
pid=$(systemctl show -p MainPID --value mag-llm.service)
avail=$(awk '/^MemAvailable:/{print int($2/1048576)}' /proc/meminfo)
anon=$(awk '/^RssAnon:/{print int($2/1048576)}' "/proc/$pid/status" 2>/dev/null || echo 0)
echo "   RAM verfügbar ${avail} GiB + laufender llama anonym ${anon} GiB"
MIN_RAM=${MAG_UPGRADE_MIN_RAM_GIB:-165}
(( avail + anon >= MIN_RAM )) || { echo "zu wenig RAM für -rtr (brauche ~${MIN_RAM} GiB) – nichts verändert"; exit 1; }
NUMA_OK=1
nn=${MAG_TEST_NUMA_NODES:-$(ls -d /sys/devices/system/node/node[0-9]* 2>/dev/null | wc -l)}
if (( nn >= 2 )); then
  while read -r node free; do
    pn=$(numastat -p "$pid" 2>/dev/null | awk -v c=$((node + 2)) '/^Private/{print int($c/1024)}' || echo 0)
    echo "   Knoten $node: frei ${free} GiB + llama ${pn:-0} GiB"
    (( free + ${pn:-0} >= 78 )) || NUMA_OK=0
  done < <(numactl --hardware | awk '/^node [0-9]+ free:/{print $2, int($4/1024)}')
  (( NUMA_OK )) || echo "   WARNUNG: zu wenig frei je Knoten für die NUMA-lokale Hälfte (~72 GiB) – NUMA-Varianten übersprungen"
else
  NUMA_OK=0; echo "   nur ein NUMA-Knoten – NUMA-Varianten übersprungen"
fi

# ---------------------------------------------------------------- 1. Sicherung
say "1. Sicherung"
install -d -o chris -g chris "$BACKUP"
cp -aL "$UNIT" "$BACKUP/mag-llm.service"
[[ -f "$MAG_LLM_ENV" ]] && cp -a "$MAG_LLM_ENV" "$BACKUP/llm.env"
[[ -f "$VARIANT_DROPIN" ]] && cp -a "$VARIANT_DROPIN" "$BACKUP/60-mag-variant.conf"
[[ -f "$IK_DROPIN" ]] && cp -a "$IK_DROPIN" "$BACKUP/50-ik.conf"
echo "   $BACKUP"
# mag-Fix: RAM-Watchdog. Fällt MemAvailable unter MIN_AVAIL_GIB, wird das Upgrade abgebrochen und auf den
# Stand vor 3.8 zurückgesetzt (der Pin bleibt unberührt, kein drop_caches).
( while kill -0 $$ 2>/dev/null; do
    a=$(awk '/^MemAvailable:/{print int($2/1048576)}' /proc/meminfo)
    if (( a < MIN_AVAIL_GIB )); then
      echo "!! WATCHDOG: MemAvailable ${a} GiB < ${MIN_AVAIL_GIB} GiB – Abbruch + Rollback"
      kill -TERM $$ 2>/dev/null; sleep 2
      bash "$SRC/ops/deepseek/upgrade-3.8.sh" --rollback "$BACKUP"; exit 0
    fi
    sleep 5
  done ) &
llm_config_load
BASE_T=$THREADS; BASE_TB=$THREADS_BATCH
echo "   bisher: ENGINE=$ENGINE threads $BASE_T/$BASE_TB batch $BATCH/$UBATCH"

# ---------------------------------------------------------------- 2. Build
say "2. ik_llama.cpp bauen (fester Stand + mag-Patches, GCC, Broadwell-nativ, mit Symbolen)"
IK_NEW=$(runuser -u chris -- env ${IK_REF:+IK_REF=$IK_REF} bash "$SRC/ops/deepseek/build-ik.sh" | tail -1)
[[ -x "$IK_NEW" ]] || { echo "Build fehlgeschlagen – nichts verändert"; exit 1; }
echo "   $IK_NEW"

# ---------------------------------------------------------------- 3. A/B
say "3. A/B per HTTP"
WANT=" ${MAG_UPGRADE_VARIANTS:-ik ik-numa ik-numa-thp ik-numa-t28 muge ik-mmap} "
echo "   Variante alt (läuft bereits)"
bench 3.8-alt
declare -A V_RTR V_ENV V_ARGS V_T V_TB
names=(alt); RUNNING=alt
add_result() { names+=("$1"); V_RTR[$1]=$2; V_ENV[$1]=$3; V_ARGS[$1]=$4; V_T[$1]=$5; V_TB[$1]=$6; }
try() {   # $1 Name, $2 rtr, $3 env, $4 args, $5 t, $6 tb
  if start_ik "$@"; then
    RUNNING=$1
    if [[ $1 == ik ]] && ! sanity; then echo "   ik rechnet falsch – zurück"; restore 1; fi
    bench "3.8-$1"; add_result "$@"
  else
    RUNNING=none; echo "   Variante $1 startet nicht"
    [[ $1 == ik ]] && restore 1
  fi
}
try ik 1 "" "" "$BASE_T" "$BASE_TB"
progress_check
if (( NUMA_OK )); then
  [[ $WANT == *" ik-numa "* ]]     && try ik-numa     1 "MAG_NUMA_LOCAL=$BASE_T" "" "$BASE_T" "$BASE_TB"
  [[ $WANT == *" ik-numa-thp "* ]] && try ik-numa-thp 1 "MAG_NUMA_LOCAL=$BASE_T MAG_THP=1" "" "$BASE_T" "$BASE_TB"
  [[ $WANT == *" ik-numa-t28 "* ]] && try ik-numa-t28 1 "MAG_NUMA_LOCAL=28" "" 28 28
fi
score_of() {   # $1 = Name -> Punktzahl relativ zu alt
  python3 -I -c 'import sys; tg,pp,a,b=map(float,sys.argv[1:]); print(f"{0.5*tg/(a or 1)+0.5*pp/(b or 1):.4f}")' \
    "$(metric "3.8-$1" tg128 tg)" "$(metric "3.8-$1" pp512 pp)" "$(metric 3.8-alt tg128 tg)" "$(metric 3.8-alt pp512 pp)"
}
best_ik() { local n b="" bs=0 s; for n in "${names[@]}"; do [[ $n == alt || $n == *mmap* ]] && continue
            s=$(score_of "$n"); if python3 -I -c 'import sys; sys.exit(0 if float(sys.argv[1])>float(sys.argv[2]) else 1)' "$s" "$bs"; then b=$n; bs=$s; fi; done; echo "$b"; }
if [[ $WANT == *" muge "* ]]; then
  b=$(best_ik)
  [[ -n "$b" ]] && try "$b+muge" 1 "${V_ENV[$b]}" "-muge" "${V_T[$b]}" "${V_TB[$b]}"
fi
[[ $WANT == *" ik-mmap "* ]] && try ik-mmap 0 "" "" "$BASE_T" "$BASE_TB"

# ---------------------------------------------------------------- 4. Auswahl
say "4. Auswahl"
{
  echo "# Runde 3.8 – ik_llama.cpp auf mag, A/B $(date -Is)"; echo
  echo "Binary: $IK_NEW"; echo
  echo "| Variante | tg128 Generierung t/s | pp512 Prompt t/s | Punkte (alt = 1) |"; echo "|---|---:|---:|---:|"
  for n in "${names[@]}"; do echo "| $n | $(metric "3.8-$n" tg128 tg) | $(metric "3.8-$n" pp512 pp) | $(score_of "$n") |"; done
  echo
  for f in /var/tmp/mag-3.8-load-*-"$TS".txt; do [[ -s "$f" ]] && { echo "**${f##*/mag-3.8-load-}**"; sed 's/^ */    /' "$f"; echo; }; done
} > "$SUMMARY"
choice=$(python3 -I - "${names[@]}" <<PY
import sys
tg = {$(for n in "${names[@]}"; do printf '"%s": %s, ' "$n" "$(metric "3.8-$n" tg128 tg)"; done)}
pp = {$(for n in "${names[@]}"; do printf '"%s": %s, ' "$n" "$(metric "3.8-$n" pp512 pp)"; done)}
a_tg, a_pp = tg["alt"] or 1, pp["alt"] or 1
score = {n: 0.5 * tg[n] / a_tg + 0.5 * pp[n] / a_pp for n in tg}
ik = {n: s for n, s in score.items() if n != "alt" and tg[n] >= a_tg}     # Generierung darf nicht langsamer werden
if not ik:
    print("alt"); sys.exit()
best = max(ik, key=ik.get)
if "ik-mmap" in ik and ik["ik-mmap"] >= 0.97 * ik[best]:
    best = "ik-mmap"                                                       # eine Kopie weniger im RAM
print(best if ik[best] >= 1.05 else "alt")
PY
)
echo "   Auswahl: $choice"; sed 's/^/   /' "$SUMMARY"
if [[ "$choice" == alt ]]; then
  { echo; echo "**Übernommen:** alt – ik war nicht mindestens 5 % besser bei gleicher oder besserer Generierung."; } >> "$SUMMARY"
  restore 0
fi
ENGINE=ik IK_BIN=$IK_NEW IK_RTR=${V_RTR[$choice]} IK_ENV=${V_ENV[$choice]:--} IK_ARGS=${V_ARGS[$choice]:--} \
  NUMA_ARGS=- LLAMA_NUMA=- THREADS=${V_T[$choice]} THREADS_BATCH=${V_TB[$choice]} llm_config_load
llm_config_save
if [[ "$choice" != "$RUNNING" ]]; then
  start_ik "$choice (final)" "${V_RTR[$choice]}" "${V_ENV[$choice]}" "${V_ARGS[$choice]}" "${V_T[$choice]}" "${V_TB[$choice]}" || restore 1
fi
{ echo; echo "**Übernommen:** $choice (gespeichert in $MAG_LLM_ENV)"; } >> "$SUMMARY"

# ---------------------------------------------------------------- 5. Go/No-Go
if [[ "${MAG_UPGRADE_SKIP_PREFLIGHT:-0}" != 1 ]]; then
  say "5. Go/No-Go (preflight-tests.py --quick: Thinking, Tool-Calls, Streaming, Audit)"
  if runuser -u chris -- sg mag-audit-readers -c "python3 -I '$SRC/ops/deepseek/preflight-tests.py' --quick" \
       > "/var/tmp/mag-3.8-preflight-$TS.txt" 2>&1; then
    tail -3 "/var/tmp/mag-3.8-preflight-$TS.txt" | sed 's/^/   /'
    { echo; echo "Go/No-Go: GO"; grep -E '^\[' "/var/tmp/mag-3.8-preflight-$TS.txt" | sed 's/^/    /'; } >> "$SUMMARY"
  else
    sed 's/^/   /' "/var/tmp/mag-3.8-preflight-$TS.txt" | tail -20
    { echo; echo "Go/No-Go: NO-GO – zurück auf den Stand vor 3.8"; grep -E '^\[' "/var/tmp/mag-3.8-preflight-$TS.txt" | sed 's/^/    /'; } >> "$SUMMARY"
    restore 1
  fi
fi

# ---------------------------------------------------------------- 6. Profil + Befund
if [[ "${MAG_UPGRADE_SKIP_PROFILE:-0}" != 1 ]]; then
  say "6. perf-Profil (mit Symbolen)"
  bash "$SRC/ops/deepseek/profile.sh" | tail -3 || echo "WARNUNG: Profil nicht erstellt"
fi
bash "$SRC/ops/deepseek/memdiag.sh" "/var/tmp/mag-mem-3.8-nachher-$TS.txt" | sed 's/^/   /' || true
numastat -p "$(systemctl show -p MainPID --value mag-llm.service)" 2>/dev/null | grep -E 'Node|Private|Total' | sed 's/^/   /' || true
say "Fertig"
echo "Ergebnis: $SUMMARY   Log: $LOG   Sicherung: $BACKUP"
echo "Bitte zurückschicken: $SUMMARY, /var/tmp/mag-profile-*.txt (neuestes), /var/tmp/mag-mem-3.8-nachher-$TS.txt"
echo "Zurück auf den Stand vor 3.8:  sudo bash $SRC/ops/deepseek/upgrade-3.8.sh --rollback"
