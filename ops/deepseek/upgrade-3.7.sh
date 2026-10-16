#!/usr/bin/env bash
# Runde 3.7 auf mag (als root, aus dem Repo). Aufgerufen von apply.sh --install.
#
#  0. Vorprüfung (llama bereit, Pin aktiv, Werkzeuge) – bricht ab, bevor etwas verändert wird
#  1. Befund vorher: Speicher-Aufteilung (memdiag) und Maschinencode der 3.6-Hotspot-Schleife
#     (Adressen aus dem 3.6-Profil; prüft auf das vpinsrw-Muster der F16C-Regression #30342)
#  2. Sicherung von Unit und /etc/mag-llm/llm.env
#  3. llama.cpp selbst bauen (build-llama.sh: b11683 + mag-Patch "AVX2-Kernels für den Q8_0-Repack",
#     GCC, GGML_NATIVE=Broadwell, mit Symbolen, Selbsttest des Patches auf dieser CPU), nice/ionice
#  4. A/B per HTTP (http-bench, ohne Cache-Test), jeweils Neustart aus dem RAM (Pin):
#       alt          = laufender Stand (Release b11630, gestrippt)
#       neu          = eigener Build, gleiche Flags (Q8_0-Gewichte über die neuen AVX2-Repack-Kernels)
#       neu-ohne-q8  = eigener Build, Q8_0-Repack per MAG_NO_Q8_0_REPACK=1 aus (= Upstream-Verhalten) –
#                      trennt den Effekt des Patches vom Effekt des neueren Builds
#       neu+norepack = eigener Build mit --no-repack (keine zweite 138-GiB-Kopie)
#       sockel0      = eigener Build, nur Sockel 0 (14 Kerne), Repack-Puffer lokal (numactl --preferred=0).
#                      Threads 16..28 brachten in 3.6 nichts und die Bandbreite ist nicht ausgelastet –
#                      prüft, ob die Wege zwischen den Sockeln (QPI, entfernter Speicher) bremsen.
#  5. Auswahl: --no-repack, wenn höchstens 2 % langsamer als die beste Variante (spart ~138 GiB);
#     sonst die schnellste. Speichern in llm.env, Neustart falls nötig. Kein Gewinn -> zurück auf alt.
#  6. perf-Profil mit Symbolen (eigener Build) + Befund nachher
#
#   sudo bash ops/deepseek/upgrade-3.7.sh               Inbetriebnahme (~40 min, Raum zwischendurch "Modell lädt")
#   sudo bash ops/deepseek/upgrade-3.7.sh --rollback    Unit und llm.env vor 3.7 wiederherstellen
# Umgebung: LLAMA_REF (Standard b11683), MAG_UPGRADE_SKIP_PROFILE=1, MAG_UPGRADE_KEEP_ALL=1 (alle Varianten messen,
#           auch wenn eine abstürzt)
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
# shellcheck source=llm-config.sh
source "$SRC/ops/deepseek/llm-config.sh"
UNIT=/etc/systemd/system/mag-llm.service
OLD_BIN=/home/chris/.local/bin/llama
BACKUP_ROOT=/home/chris/mag-infra/systemd
BACKUP=$BACKUP_ROOT/upgrade-3.7-$(date +%Y%m%d%H%M%S)
TS=$(date +%Y%m%d-%H%M)
LOG=/var/tmp/mag-upgrade-3.7-$TS.log
SUMMARY=/var/tmp/mag-3.7-ergebnis-$TS.md
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
exec > >(tee -a "$LOG") 2>&1
MODE=${1:-}
say() { printf '\n== %s  [%s]\n' "$1" "$(date +%H:%M:%S)"; }
health() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health 2>/dev/null; true; }
VARIANT_DROPIN=/etc/systemd/system/mag-llm.service.d/60-mag-variant.conf
set_variant_env() {   # $1 = Environment-Zuweisung oder leer
  if [[ -n "$1" ]]; then
    install -d "$(dirname "$VARIANT_DROPIN")"
    printf '# von upgrade-3.7.sh gesetzt
[Service]
Environment=%s
' "$1" > "$VARIANT_DROPIN"
  else
    rm -f "$VARIANT_DROPIN"
  fi
}
unlock() { install -d /run/systemd/system/mag-llm.service.d
           printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload; }
relock() { rm -f /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload; }

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
  if [[ $rc -ne 0 ]]; then journalctl -u mag-llm -n 30 --no-pager || true; fi
  cp -a "$BACKUP/mag-llm.service" "$UNIT"
  rm -f "$VARIANT_DROPIN"
  if [[ -f "$BACKUP/llm.env" ]]; then cp -a "$BACKUP/llm.env" "$MAG_LLM_ENV"; else rm -f "$MAG_LLM_ENV"; fi
  unlock; systemctl restart mag-llm.service; relock
  if wait_ready 7200; then echo "Stand vor 3.7 läuft wieder. Log: $LOG"; else echo "Stand vor 3.7 nicht bereit – journalctl -u mag-llm prüfen"; exit 1; fi
  exit "$rc"
}

# Variante starten: $1 Name, $2 LLAMA_BIN ("" = mitgeliefert), $3 EXTRA_ARGS ("" = keine),
#                   $4 NUMA_ARGS ("" = verschränkt), $5 LLAMA_NUMA ("" = distribute), $6 THREADS, $7 THREADS_BATCH ("" = llm.env)
start_variant() {
  local name=$1 bin=$2 extra=$3 na=${4:-} ln=${5:-} th=${6:-} tb=${7:-} venv=${8:-} t0=$SECONDS
  set_variant_env "$venv"
  echo "   Neustart: $name (Binary ${bin:-$OLD_BIN}${extra:+, $extra}${na:+, numactl $na}${th:+, threads $th/$tb})"
  LLAMA_BIN=${bin:--} EXTRA_ARGS=${extra:--} NUMA_ARGS=${na:--} LLAMA_NUMA=${ln:--} \
    THREADS=${th:-$BASE_T} THREADS_BATCH=${tb:-$BASE_TB} render_unit "$UNIT" || return 1
  unlock; systemctl restart mag-llm.service; relock
  wait_ready 3600 || return 1
  echo "   bereit nach $(( (SECONDS - t0) / 60 )) min $(( (SECONDS - t0) % 60 )) s"
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

if [[ "$MODE" == --rollback ]]; then
  BACKUP=${2:-$(ls -dt "$BACKUP_ROOT"/upgrade-3.7-* 2>/dev/null | head -1 || true)}
  [[ -n "$BACKUP" && -d "$BACKUP" ]] || { echo "keine Sicherung unter $BACKUP_ROOT/upgrade-3.7-*"; exit 1; }
  restore 0
fi

# ---------------------------------------------------------------- 0. Vorprüfung
say "0. Vorprüfung"
for f in ops/deepseek/build-llama.sh ops/deepseek/memdiag.sh ops/deepseek/profile.sh ops/deepseek/http-bench.sh \
         ops/deepseek/render-llm-unit.sh ops/deepseek/llm-config.sh; do
  [[ -f "$SRC/$f" ]] || { echo "fehlt: $SRC/$f"; exit 1; }
done
[[ "$(health)" == 200 ]] || { echo "mag-llm nicht bereit – erst den laufenden Stand in Ordnung bringen"; exit 1; }
systemctl is-active --quiet mag-model-pin.service || { echo "mag-model-pin läuft nicht – Neustarts würden von der Platte laden"; exit 1; }
pkgs=()
for p in git cmake ninja-build g++ libssl-dev binutils; do dpkg -s "$p" >/dev/null 2>&1 || pkgs+=("$p"); done
dpkg -s "linux-tools-$(uname -r)" >/dev/null 2>&1 || pkgs+=("linux-tools-common" "linux-tools-$(uname -r)")
if (( ${#pkgs[@]} )); then echo "   installiere: ${pkgs[*]}"; apt-get install -y --no-install-recommends "${pkgs[@]}" >/dev/null; fi
df -h /home/chris | tail -1 | awk '{print "   Platz in /home/chris: " $4 " frei"}'

# ---------------------------------------------------------------- 1. Befund vorher
say "1. Befund vorher (Speicher, Hotspot-Schleife des 3.6-Binarys)"
bash "$SRC/ops/deepseek/memdiag.sh" "/var/tmp/mag-mem-3.7-vorher-$TS.txt" | sed 's/^/   /'
{
  echo "# 3.6-Hotspot (perf: 0x1111266..0x11112a4, ~55 % aller Samples) im Release-Binary $OLD_BIN"
  objdump -d --no-show-raw-insn --start-address=0x1111230 --stop-address=0x11112d0 "$OLD_BIN" 2>/dev/null | tail -n +7
} > "/var/tmp/mag-hotloop-3.7-$TS.txt" || true
n_ins=$(grep -cE '^\s+[0-9a-f]+:' "/var/tmp/mag-hotloop-3.7-$TS.txt" || true)
n_pinsr=$(grep -cE 'vpinsrw' "/var/tmp/mag-hotloop-3.7-$TS.txt" || true)
n_cvt=$(grep -cE 'vcvtph2ps' "/var/tmp/mag-hotloop-3.7-$TS.txt" || true)
echo "   Hotspot-Schleife: $n_ins Befehle, vpinsrw: $n_pinsr, vcvtph2ps: $n_cvt  (/var/tmp/mag-hotloop-3.7-$TS.txt)"
(( n_pinsr > 0 )) && echo "   -> Muster der F16C-Regression (#30342) im heißesten Code gefunden"

# ---------------------------------------------------------------- 2. Sicherung
say "2. Sicherung"
install -d -o chris -g chris "$BACKUP"
cp -aL "$UNIT" "$BACKUP/mag-llm.service"
[[ -f "$MAG_LLM_ENV" ]] && cp -a "$MAG_LLM_ENV" "$BACKUP/llm.env"
echo "   $BACKUP"

# ---------------------------------------------------------------- 3. Build
say "3. llama.cpp bauen (${LLAMA_REF:-b11683}, GCC, Broadwell-nativ, mit Symbolen)"
NEW_BIN=$(runuser -u chris -- env LLAMA_REF="${LLAMA_REF:-b11683}" bash "$SRC/ops/deepseek/build-llama.sh" | tail -1)
[[ -x "$NEW_BIN" ]] || { echo "Build fehlgeschlagen"; exit 1; }
echo "   $NEW_BIN"

# ---------------------------------------------------------------- 4. A/B
say "4. A/B per HTTP"
llm_config_load
CUR_BIN=$LLAMA_BIN; CUR_EXTRA=$EXTRA_ARGS; BASE_T=$THREADS; BASE_TB=$THREADS_BATCH
echo "   Variante alt (läuft bereits)"
bench 3.7-alt
results=("alt||"); RUNNING=alt
if start_variant neu "$NEW_BIN" ""; then
  RUNNING=neu
  bench 3.7-neu; results+=("neu|$NEW_BIN|")
  bash "$SRC/ops/deepseek/memdiag.sh" "/var/tmp/mag-mem-3.7-neu-$TS.txt" | grep -E 'Befund|anonyme' | sed 's/^/   /'
else
  RUNNING=none
  echo "   Variante neu startet nicht"; [[ "${MAG_UPGRADE_KEEP_ALL:-0}" == 1 ]] || restore 1
fi
if start_variant neu-ohne-q8 "$NEW_BIN" "" "" "" "" "" "MAG_NO_Q8_0_REPACK=1"; then
  RUNNING=noq8
  bench 3.7-noq8; results+=("noq8|$NEW_BIN|")
else
  RUNNING=none
  echo "   Variante neu-ohne-q8 startet nicht"
fi
if start_variant neu+norepack "$NEW_BIN" "--no-repack"; then
  RUNNING=norepack
  bench 3.7-norepack; results+=("norepack|$NEW_BIN|--no-repack")
  bash "$SRC/ops/deepseek/memdiag.sh" "/var/tmp/mag-mem-3.7-norepack-$TS.txt" | grep -E 'Befund|anonyme' | sed 's/^/   /'
else
  RUNNING=none
  echo "   Variante neu+norepack startet nicht"
fi
# Sockel 0: Repack-Puffer (die eigentlich gerechneten Gewichte) entstehen beim Laden auf Knoten 0.
free0=$(numactl --hardware | awk '/^node 0 free:/{print int($4/1024)}')
echo "   Knoten 0 frei: ${free0:-?} GiB (Repack-Puffer ~140 GiB; --preferred weicht bei Bedarf auf Knoten 1 aus)"
if start_variant sockel0 "$NEW_BIN" "" "--cpunodebind=0 --preferred=0" numactl 14 14; then
  RUNNING=sockel0
  bench 3.7-sockel0; results+=("sockel0|$NEW_BIN||--cpunodebind=0 --preferred=0|numactl|14|14")
  numastat -p "$(systemctl show -p MainPID --value mag-llm.service)" 2>/dev/null | grep -E 'Node|Private|Total' | sed 's/^/   /' || true
else
  RUNNING=none
  echo "   Variante sockel0 startet nicht"
fi

# ---------------------------------------------------------------- 5. Auswahl
say "5. Auswahl"
{
  echo "# Runde 3.7 – A/B $(date -Is)"; echo
  echo "| Variante | tg128 Generierung t/s | pp512 Prompt t/s |"; echo "|---|---:|---:|"
} > "$SUMMARY"
best=""; best_score=0; declare -A TG PP
for r in "${results[@]}"; do
  n=${r%%|*}; lab=3.7-$n; [[ $n == alt ]] && lab=3.7-alt
  TG[$n]=$(metric "$lab" tg128 tg); PP[$n]=$(metric "$lab" pp512 pp)
  echo "| $n | ${TG[$n]} | ${PP[$n]} |" >> "$SUMMARY"
done
choice=$(python3 -I - "${results[@]}" <<PY
import sys
tg = {$(for r in "${results[@]}"; do n=${r%%|*}; printf '"%s": %s, ' "$n" "${TG[$n]}"; done)}
pp = {$(for r in "${results[@]}"; do n=${r%%|*}; printf '"%s": %s, ' "$n" "${PP[$n]}"; done)}
base_tg, base_pp = tg["alt"] or 1, pp["alt"] or 1
score = {n: 0.5 * tg[n] / base_tg + 0.5 * pp[n] / base_pp for n in tg}
best = max(score, key=score.get)
# --no-repack bevorzugen, wenn höchstens 2 % schlechter (spart die zweite Kopie im RAM)
if "norepack" in score and tg["norepack"] >= 0.98 * tg[best] and pp["norepack"] >= 0.98 * pp[best]:
    best = "norepack"
print(best)
PY
)
echo "   Auswahl: $choice"
cat "$SUMMARY" | sed 's/^/   /'
sel_na=""; sel_ln=""; sel_t=""; sel_tb=""; sel_env=""
case "$choice" in
  alt)      sel_bin="$CUR_BIN"; sel_extra="$CUR_EXTRA" ;;
  neu)      sel_bin="$NEW_BIN"; sel_extra="" ;;
  noq8)     sel_bin="$NEW_BIN"; sel_extra=""; sel_env="MAG_NO_Q8_0_REPACK=1" ;;
  norepack) sel_bin="$NEW_BIN"; sel_extra="--no-repack" ;;
  sockel0)  sel_bin="$NEW_BIN"; sel_extra=""; sel_na="--cpunodebind=0 --preferred=0"; sel_ln=numactl; sel_t=14; sel_tb=14 ;;
esac
LLAMA_BIN=${sel_bin:--} EXTRA_ARGS=${sel_extra:--} NUMA_ARGS=${sel_na:--} LLAMA_NUMA=${sel_ln:--} \
  THREADS=${sel_t:-$BASE_T} THREADS_BATCH=${sel_tb:-$BASE_TB} llm_config_load
llm_config_save
if [[ "$choice" != "$RUNNING" ]]; then
  start_variant "$choice (final)" "$sel_bin" "$sel_extra" "$sel_na" "$sel_ln" "$sel_t" "$sel_tb" "$sel_env" || restore 1
fi
{ echo; echo "**Übernommen:** $choice (gespeichert in $MAG_LLM_ENV)"; } >> "$SUMMARY"

# ---------------------------------------------------------------- 6. Profil + Befund nachher
if [[ "${MAG_UPGRADE_SKIP_PROFILE:-0}" != 1 ]]; then
  say "6. perf-Profil (mit Symbolen, falls eigener Build aktiv)"
  bash "$SRC/ops/deepseek/profile.sh" | tail -3 || echo "WARNUNG: Profil nicht erstellt"
fi
bash "$SRC/ops/deepseek/memdiag.sh" "/var/tmp/mag-mem-3.7-nachher-$TS.txt" | sed 's/^/   /'
say "Fertig"
echo "Ergebnis: $SUMMARY   Log: $LOG   Sicherung: $BACKUP"
echo "Bitte zurückschicken: $SUMMARY, /var/tmp/mag-profile-*.txt (neuestes), /var/tmp/mag-mem-3.7-*-$TS.txt, /var/tmp/mag-hotloop-3.7-$TS.txt"
echo "Zurück auf den Stand vor 3.7:  sudo bash $SRC/ops/deepseek/upgrade-3.7.sh --rollback"
