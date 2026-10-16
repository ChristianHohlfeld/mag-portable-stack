#!/usr/bin/env bash
# Runde 3.6 auf mag in Betrieb nehmen (als root, aus dem Repo). Aufgerufen von apply.sh --install.
#
#  0. Vorprüfung – bricht ab, BEVOR etwas verändert wird: Pin aktiv und 100 % im RAM, Unit mit
#     mmap+mlock für diesen Build renderbar, Freigabe-Stempel
#  1. Sicherung der laufenden Unit, von /etc/mag-llm/llm.env, Gateway und Raum-Drop-in
#  1b. Gateway: bricht die Anfrage an llama ab, sobald der Client abbricht (Neustart mag-audit-gateway)
#  1c. Raum: reasoning_effort nicht mehr senden (Prompt-Cache), neue Nachrichten einreihen statt
#      abbrechen – Release über ops/deploy.sh, Drop-in installieren, mag-120 neu starten
#  2. llama auf mmap+mlock umstellen: llama mappt die Seiten, die mag-model-pin bereits gesperrt hält
#     -> EINE Kopie statt zwei (anon + Pin), voll gesperrt (VmLck ~144 GiB), Neustarts aus dem RAM.
#     Gleiche Threads/Batch wie bisher; nur der Lademodus ändert sich. Rollback, falls nicht bereit.
#  3. tune.sh: Threads (Generierung) und threads-batch/ubatch (Prompt) per HTTP einmessen, Bestes übernehmen
#  4. http-bench --quick (Label 3.6) und Vergleich mit der Basis-Messung
#  5. profile.sh: perf-Profil des laufenden llama während Generierung und Prompt-Lesen
#  6. Status
#
#   sudo bash ops/deepseek/upgrade-3.6.sh               Inbetriebnahme
#   sudo bash ops/deepseek/upgrade-3.6.sh --rollback    zurück auf die gesicherte Unit (Stand vor 3.6)
# Umgebung: MAG_UPGRADE_SKIP_TUNE=1  MAG_UPGRADE_SKIP_BENCH=1  MAG_UPGRADE_SKIP_PROFILE=1
#           MAG_UPGRADE_MIN_LOCK_GIB (Standard 130): Mindestwert für VmLck nach der Umstellung
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
# shellcheck source=llm-config.sh
source "$SRC/ops/deepseek/llm-config.sh"
UNIT=/etc/systemd/system/mag-llm.service
DIR=/home/chris/models/deepseek-v4
FILES=("$DIR"/DeepSeek-V4-Flash-0731-UD-Q4_K_XL-0000{1..5}-of-00005.gguf)
BACKUP_ROOT=/home/chris/mag-infra/systemd
BACKUP=$BACKUP_ROOT/upgrade-3.6-$(date +%Y%m%d%H%M%S)
LOG=/var/tmp/mag-upgrade-3.6-$(date +%Y%m%d-%H%M).log
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
exec > >(tee -a "$LOG") 2>&1
MODE=${1:-}
say() { printf '\n== %s  [%s]\n' "$1" "$(date +%H:%M:%S)"; }
health() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health 2>/dev/null; true; }
llm_pid() { systemctl show -p MainPID --value mag-llm.service 2>/dev/null || echo 0; }
kib_of() { local p; p=$(llm_pid); [[ -n "$p" && "$p" != 0 && -r /proc/$p/status ]] && awk -v k="^$1:" '$0 ~ k {print $2}' /proc/$p/status || echo 0; }
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
    (( r - r0 >= 2 )) && { echo "mag-llm ist $((r - r0))x abgestürzt"; return 1; }
    if (( SECONDS - last >= 30 )); then
      last=$SECONDS
      printf '   %s  /health=%s  VmLck=%s GiB\n' "$(date +%H:%M:%S)" "$code" "$(( $(kib_of VmLck) / 1048576 ))"
    fi
    sleep 5
  done
  echo "Zeitlimit erreicht"; return 1
}

restore() {   # $1 = Exit-Code danach
  local rc=${1:-1}
  say "ROLLBACK: Unit aus $BACKUP wiederherstellen"
  [[ -f "$BACKUP/mag-llm.service" ]] || { echo "keine Sicherung in $BACKUP"; exit 1; }
  if [[ $rc -ne 0 ]]; then journalctl -u mag-llm -n 40 --no-pager || true; fi
  cp -a "$BACKUP/mag-llm.service" "$UNIT"
  if [[ -f "$BACKUP/llm.env" ]]; then cp -a "$BACKUP/llm.env" "$MAG_LLM_ENV"; else rm -f "$MAG_LLM_ENV"; fi
  unlock; systemctl restart mag-llm.service; relock
  if wait_ready 10800; then echo "Stand vor 3.6 läuft wieder. Log: $LOG"; else echo "Auch der Stand vor 3.6 ist nicht bereit – journalctl -u mag-llm prüfen. Log: $LOG"; exit 1; fi
  exit "$rc"
}

compare_with_basis() {
  local basis new
  basis=$(ls -t /var/tmp/mag-httpbench-*-basis.md 2>/dev/null | head -1 || true)
  new=$(ls -t /var/tmp/mag-httpbench-*-3.6.md 2>/dev/null | head -1 || true)
  [[ -n "$basis" && -n "$new" ]] || { echo "   (keine Basis-Messung zum Vergleich)"; return 0; }
  python3 -I -c '
import sys
def rows(f):
    out = {}
    for l in open(f, encoding="utf-8"):
        c = [x.strip() for x in l.strip().strip("|").split("|")]
        if len(c) == 7 and c[0].startswith(("tg", "pp")):
            out[c[0]] = dict(pp=float(c[2]), tg=float(c[4]), ttft=float(c[5]))
    return out
a, b = rows(sys.argv[1]), rows(sys.argv[2])
print("   Basis: " + sys.argv[1])
print("   %-8s %16s %16s %18s" % ("Messung", "pp t/s", "tg t/s", "TTFT s"))
for k in [k for k in a if k in b]:
    d = lambda m: "%.2f -> %.2f" % (a[k][m], b[k][m])
    pct = lambda m: ("%+.0f %%" % ((b[k][m] / a[k][m] - 1) * 100)) if a[k][m] else ""
    print("   %-8s %16s %16s %18s   pp %s, tg %s" % (k, d("pp"), d("tg"), d("ttft"), pct("pp"), pct("tg")))
' "$basis" "$new"
}

if [[ "$MODE" == --rollback ]]; then
  BACKUP=${2:-$(ls -dt "$BACKUP_ROOT"/upgrade-3.6-* 2>/dev/null | head -1 || true)}
  [[ -n "$BACKUP" && -d "$BACKUP" ]] || { echo "keine Sicherung unter $BACKUP_ROOT/upgrade-3.6-*"; exit 1; }
  restore 0
fi

# ---------------------------------------------------------------- 0. Vorprüfung
say "0. Vorprüfung"
for f in ops/audit/mag_audit_gateway.py ops/mag-120.d/20-deepseek-audit.conf ops/check-unit-env.py ops/deepseek/llm-config.sh ops/deepseek/render-llm-unit.sh ops/deepseek/tune.sh ops/deepseek/profile.sh \
         ops/deepseek/http-bench.sh ops/deepseek/warm.sh ops/mag-model-pin.service; do
  [[ -f "$SRC/$f" ]] || { echo "fehlt: $SRC/$f"; exit 1; }
done
[[ -f /var/lib/mag-llm/verified.stamp ]] || { echo "Modell nicht freigegeben (verified.stamp fehlt)"; exit 1; }
[[ -f "$UNIT" ]] || { echo "$UNIT fehlt"; exit 1; }
[[ "$(health)" == 200 ]] || { echo "mag-llm ist nicht bereit – erst den laufenden Stand in Ordnung bringen"; exit 1; }
systemctl is-active --quiet mag-model-pin.service || { echo "mag-model-pin läuft nicht – ohne Pin würde der Neustart von der Platte laden"; exit 1; }
res=$(python3 -I - "${FILES[@]}" <<'PY'
import ctypes, mmap, os, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
PS = os.sysconf("SC_PAGE_SIZE"); tot = res = 0
for f in sys.argv[1:]:
    fd = os.open(f, os.O_RDONLY); size = os.fstat(fd).st_size
    addr = libc.mmap(None, size, mmap.PROT_READ, mmap.MAP_SHARED, fd, 0)
    n = (size + PS - 1) // PS; vec = ctypes.create_string_buffer(n)
    if libc.mincore(addr, size, vec) == 0:
        res += sum(b & 1 for b in vec.raw) * PS
    libc.munmap(addr, size); os.close(fd); tot += size
print(int(res * 100 // tot))
PY
)
echo "   Modelldateien im Page-Cache (Pin): ${res} %"
(( res >= 99 )) || { echo "Pin hat das Modell noch nicht vollständig im RAM – später erneut"; exit 1; }
# Laufende Zahlenwerte übernehmen (nur der Lademodus ändert sich in Schritt 2)
pid=$(llm_pid); cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline")
cur() { sed -nE "s/.*--$1 ([0-9]+) .*/\1/p" <<<"$cmd "; }
THREADS=${THREADS:-$(cur threads)}; THREADS_BATCH=${THREADS_BATCH:-$(cur threads-batch)}
BATCH=${BATCH:-$(cur batch-size)}; UBATCH=${UBATCH:-$(cur ubatch-size)}
LOAD_ARGS="--load-mode mmap+mlock"
export LOAD_ARGS THREADS THREADS_BATCH BATCH UBATCH
render_unit --check
echo "   bisher: $(grep -oE -- '--load-mode [^ ]+|--no-mmap' <<<"$cmd" | head -1), VmLck $(( $(kib_of VmLck) / 1048576 )) GiB, RSS $(( $(kib_of VmRSS) / 1048576 )) GiB"

# ---------------------------------------------------------------- 1. Sicherung
say "1. Sicherung"
install -d -o chris -g chris "$BACKUP"
cp -aL "$UNIT" "$BACKUP/mag-llm.service"
[[ -f "$MAG_LLM_ENV" ]] && cp -a "$MAG_LLM_ENV" "$BACKUP/llm.env"
cp -a /opt/mag-audit/mag_audit_gateway.py "$BACKUP/" 2>/dev/null || true
cp -a /etc/systemd/system/mag-120.service.d/20-deepseek-audit.conf "$BACKUP/" 2>/dev/null || true
echo "   $BACKUP"

# ---------------------------------------------------------------- 1b. Gateway
say "1b. Gateway: Abbruch des Clients stoppt llama"
python3 -I -m py_compile "$SRC/ops/audit/mag_audit_gateway.py"
install -m 0644 "$SRC/ops/audit/mag_audit_gateway.py" /opt/mag-audit/mag_audit_gateway.py
systemctl restart mag-audit-gateway.service     # mag-120 hängt per Requires= daran und startet mit
for _ in $(seq 1 30); do curl -fsS --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS --max-time 3 http://127.0.0.1:8080/health >/dev/null || { echo "FEHLER: Gateway antwortet nicht – alte Fassung zurück"
  cp -a "$BACKUP/mag_audit_gateway.py" /opt/mag-audit/mag_audit_gateway.py; systemctl restart mag-audit-gateway.service; exit 1; }
echo "   Gateway bereit"

# ---------------------------------------------------------------- 1c. Raum
if [[ "${MAG_UPGRADE_SKIP_DEPLOY:-0}" != 1 ]]; then
  say "1c. Raum: Release + Drop-in (kein reasoning_effort, Nachrichten einreihen)"
  install -m 0644 "$SRC/ops/mag-120.d/20-deepseek-audit.conf" /etc/systemd/system/mag-120.service.d/20-deepseek-audit.conf
  python3 -I "$SRC/ops/check-unit-env.py" /etc/systemd/system/mag-120.service.d
  systemctl daemon-reload
  bash "$SRC/ops/deploy.sh"                     # baut nichts neu (apply.sh hat gebaut), startet mag-120 neu
  for _ in $(seq 1 60); do curl -fsS --max-time 2 http://127.0.0.1:8766/readyz >/dev/null 2>&1 && break; sleep 1; done
  env_room=$(systemctl show mag-120 -p Environment)
  if grep -q 'MAG_ROOM_REASONING_EFFORT=' <<<"$env_room"; then echo "WARNUNG: MAG_ROOM_REASONING_EFFORT ist noch gesetzt (anderer Drop-in?)"; fi
  echo "   Raum bereit"
fi

# ---------------------------------------------------------------- 2. mmap+mlock
say "2. llama auf mmap+mlock (eine Kopie, voll gesperrt)"
llm_config_save
render_unit "$UNIT"
t0=$SECONDS
unlock; trap relock EXIT
systemctl restart mag-llm.service
relock; trap - EXIT
wait_ready 10800 || restore 1
printf '   bereit nach %d min %02d s\n' $(((SECONDS - t0) / 60)) $(((SECONDS - t0) % 60))
lck=$(( $(kib_of VmLck) / 1048576 ))
echo "   VmLck ${lck} GiB, AnonPages $(awk '/^AnonPages/{printf "%.0f", $2/1048576}' /proc/meminfo) GiB, MemAvailable $(awk '/^MemAvailable/{printf "%.0f", $2/1048576}' /proc/meminfo) GiB"
if (( lck < ${MAG_UPGRADE_MIN_LOCK_GIB:-130} )); then
  echo "FEHLER: nur ${lck} GiB gesperrt – mmap+mlock greift nicht wie erwartet"; restore 1
fi
pid=$(llm_pid)
grep -q 'DeepSeek-V4-Flash-0731.*\.gguf' "/proc/$pid/maps" || { echo "FEHLER: llama mappt die Modelldateien nicht"; restore 1; }

# ---------------------------------------------------------------- 3. Tuning
if [[ "${MAG_UPGRADE_SKIP_TUNE:-0}" != 1 ]]; then
  say "3. Threads/Batch einmessen (tune.sh, Neustarts aus dem RAM)"
  bash "$SRC/ops/deepseek/tune.sh" || echo "WARNUNG: tune.sh ohne Ergebnis – Werte von Schritt 2 bleiben aktiv"
  [[ "$(health)" == 200 ]] || wait_ready 3600 || restore 1
fi

# ---------------------------------------------------------------- 4. Benchmark
if [[ "${MAG_UPGRADE_SKIP_BENCH:-0}" != 1 ]]; then
  say "4. http-bench --quick (Label 3.6)"
  runuser -u chris -- env LABEL=3.6 bash "$SRC/ops/deepseek/http-bench.sh" --quick || echo "WARNUNG: Benchmark nicht gelaufen"
  compare_with_basis
fi

# ---------------------------------------------------------------- 5. Profil
if [[ "${MAG_UPGRADE_SKIP_PROFILE:-0}" != 1 ]]; then
  say "5. perf-Profil des laufenden llama"
  bash "$SRC/ops/deepseek/profile.sh" || echo "WARNUNG: Profil nicht erstellt"
fi

# ---------------------------------------------------------------- 6. Status
say "6. Status"
st=$(curl -fsS --max-time 3 http://127.0.0.1:8766/api/model-status 2>/dev/null || echo '{}')
echo "   Raum: $st"
bash "$SRC/ops/deepseek/warm.sh" status || true
echo; echo "Log: $LOG   Sicherung: $BACKUP"
echo "Zurück auf den Stand vor 3.6:  sudo bash $SRC/ops/deepseek/upgrade-3.6.sh --rollback"
