#!/usr/bin/env bash
# Runde 3.5 auf mag in Betrieb nehmen (als root, aus dem Repo). Aufgerufen von apply.sh --install.
#
#  0. Vorprüfung – bricht ab, BEVOR etwas verändert wird (Unit-Rendering, Flags, RAM, Freigabe-Stempel)
#  1. Raum-Release (Thinking-Schalter) über ops/deploy.sh – solange das alte Modell noch antwortet
#  2. mag-llm neu: Laden ohne mmap + mlock, Threads/Batch neu, Page-Cache-Pin aus 3.2 entfernt
#     (kompletter Neustart, von Chris freigegeben); warten bis bereit (max. 3 h)
#     -> scheitert der Start, wird automatisch die vorherige Unit (inkl. Pin) wiederhergestellt
#  3. Page-Cache-Kopie der Modelldateien freigeben (keine zweite Kopie im RAM)
#  4. mag-120-Drop-in (inkl. 3.4.1-Fix) installieren, mag-120 neu starten, warten bis Raum + Modell bereit
#  5. http-bench.sh --quick einmal per HTTP gegen den laufenden Server (kein zweiter Prozess)
#
#  6. Vergleich mit der letzten Basis-Messung (/var/tmp/mag-httpbench-*-basis.md), falls vorhanden
#
#   sudo bash ops/deepseek/upgrade-3.5.sh               Inbetriebnahme (Schritte 0-6)
#   sudo bash ops/deepseek/upgrade-3.5.sh --rollback    zurück auf den Stand vor 3.5 (letzte Sicherung:
#                                                       mmap+mlock-Unit und Page-Cache-Pin), Neustart, warten
#
# Umgebung: MAG_UPGRADE_SKIP_DEPLOY=1  Schritt 1 überspringen (Release schon ausgerollt)
#           MAG_UPGRADE_SKIP_BENCH=1   Schritt 5 überspringen
#           LOAD_ARGS="--load-mode X"  Lademodus explizit (sonst aus 'llama serve --help' erkannt)
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
LIB=/usr/local/lib/mag-deepseek
UNIT=/etc/systemd/system/mag-llm.service
PIN=/etc/systemd/system/mag-model-pin.service
DROPIN_DIR=/etc/systemd/system/mag-120.service.d
BACKUP_ROOT=/home/chris/mag-infra/systemd
BACKUP=$BACKUP_ROOT/upgrade-3.5-$(date +%Y%m%d%H%M%S)
LOG=/var/tmp/mag-upgrade-3.5-$(date +%Y%m%d-%H%M).log
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
exec > >(tee -a "$LOG") 2>&1
MODE=${1:-}
say() { printf '\n== %s  [%s]\n' "$1" "$(date +%H:%M:%S)"; }
health() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health 2>/dev/null; true; }
rss_gib() { local p; p=$(systemctl show -p MainPID --value mag-llm.service 2>/dev/null || echo 0)
  [[ -n "$p" && "$p" != 0 && -r /proc/$p/status ]] && awk '/^VmRSS/{printf "%.0f", $2/1048576}' /proc/$p/status || echo 0; }

unlock() {   # RefuseManualStop nur für diesen Vorgang aufheben (Laufzeit-Drop-in, weg spätestens beim Reboot)
  for u in mag-llm mag-model-pin; do
    install -d /run/systemd/system/$u.service.d
    printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/$u.service.d/99-upgrade.conf
  done
  systemctl daemon-reload
}
relock() {
  rm -f /run/systemd/system/mag-llm.service.d/99-upgrade.conf /run/systemd/system/mag-model-pin.service.d/99-upgrade.conf
  systemctl daemon-reload
}

wait_ready() {   # $1 = Sekunden
  local deadline=$((SECONDS + $1)) last=0 code restarts0 restarts
  restarts0=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
  while (( SECONDS < deadline )); do
    code=$(health)
    [[ "$code" == 200 ]] && return 0
    restarts=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
    if (( restarts - restarts0 >= 2 )); then echo "mag-llm ist $((restarts - restarts0))x abgestürzt"; return 1; fi
    if (( SECONDS - last >= 30 )); then
      last=$SECONDS
      printf '   %s  mag-llm=%s  /health=%s  RSS=%s GiB\n' "$(date +%H:%M:%S)" "$(systemctl is-active mag-llm 2>/dev/null)" "$code" "$(rss_gib)"
    fi
    sleep 5
  done
  echo "Zeitlimit erreicht"; return 1
}

rollback() {   # $1 = Exit-Code danach (1 = automatisch nach Fehlschlag, 0 = manuell gewollt)
  local rc=${1:-1}
  say "ROLLBACK: vorherige mag-llm-Unit wiederherstellen ($BACKUP)"
  [[ -f "$BACKUP/mag-llm.service" ]] || { echo "keine Sicherung in $BACKUP"; exit 1; }
  if [[ $rc -ne 0 ]]; then journalctl -u mag-llm -n 40 --no-pager || true; fi
  unlock
  systemctl stop mag-llm.service || true
  cp -a "$BACKUP/mag-llm.service" "$UNIT"
  if [[ -f "$BACKUP/mag-model-pin.service" ]]; then
    cp -a "$BACKUP/mag-model-pin.service" "$PIN"
    systemctl daemon-reload; systemctl enable mag-model-pin.service; systemctl start mag-model-pin.service || true
  fi
  systemctl daemon-reload
  systemctl start mag-llm.service
  relock
  if wait_ready 10800; then echo "Vorheriger Stand läuft wieder. Log: $LOG"; else echo "Auch der vorherige Stand ist nicht bereit – bitte journalctl -u mag-llm prüfen. Log: $LOG"; exit 1; fi
  exit "$rc"
}

compare_with_basis() {   # letzte Basis-Messung vs. eben gemessen (Median je Zeile)
  local basis new
  basis=$(ls -t /var/tmp/mag-httpbench-*-basis.md 2>/dev/null | head -1 || true)
  new=$(ls -t /var/tmp/mag-httpbench-*-3.5.md 2>/dev/null | head -1 || true)
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
  BACKUP=${2:-$(ls -dt "$BACKUP_ROOT"/upgrade-3.5-* 2>/dev/null | head -1 || true)}
  [[ -n "$BACKUP" && -d "$BACKUP" ]] || { echo "keine Sicherung unter $BACKUP_ROOT/upgrade-3.5-*"; exit 1; }
  rollback 0
fi

# ---------------------------------------------------------------- 0. Vorprüfung
say "0. Vorprüfung"
for f in ops/mag-llm.service ops/deepseek/render-llm-unit.sh ops/deepseek/post-load.sh ops/deepseek/warm.sh \
         ops/deepseek/http-bench.sh ops/mag-120.d/20-deepseek-audit.conf ops/check-unit-env.py; do
  [[ -f "$SRC/$f" ]] || { echo "fehlt: $SRC/$f"; exit 1; }
done
[[ -f /var/lib/mag-llm/verified.stamp ]] || { echo "Modell nicht freigegeben (verified.stamp fehlt)"; exit 1; }
[[ -f "$UNIT" ]] || { echo "$UNIT fehlt – zuerst install-deepseek.sh"; exit 1; }
command -v numactl >/dev/null || apt-get install -y --no-install-recommends numactl
python3 -I "$SRC/ops/check-unit-env.py" "$SRC/ops"
tmpunit=$(mktemp /run/mag-llm.service.XXXXXX)
bash "$SRC/ops/deepseek/render-llm-unit.sh" "$SRC/ops/mag-llm.service" "$tmpunit"
if command -v systemd-analyze >/dev/null; then
  systemd-analyze verify "$tmpunit" 2>&1 | grep -vE 'not executable|No such file|Failed to (load|prepare)|mag-audit-nft|Unit .* not found' || true
fi
# RAM: Neuladen braucht ~144 GiB frei. Die alte Instanz hält ihre (Datei-)Seiten bis zum Stopp,
# danach sind sie freigebbarer Cache. Daher: verfügbar + von llama gesperrt >= 160 GiB.
avail=$(awk '/^MemAvailable/{print int($2/1048576)}' /proc/meminfo)
locked=$(awk '/^Mlocked/{print int($2/1048576)}' /proc/meminfo)
echo "   RAM verfügbar ${avail} GiB, gesperrt ${locked} GiB"
(( avail + locked >= ${MAG_UPGRADE_MIN_RAM_GIB:-160} )) || { echo "Zu wenig RAM für das Neuladen"; exit 1; }
install -d -o chris -g chris "$BACKUP"
cp -aL "$UNIT" "$BACKUP/mag-llm.service"
[[ -f "$PIN" ]] && cp -a "$PIN" "$BACKUP/mag-model-pin.service"
cp -a "$DROPIN_DIR/20-deepseek-audit.conf" "$BACKUP/" 2>/dev/null || true
echo "   Sicherung: $BACKUP"

# ---------------------------------------------------------------- 1. Raum-Release
if [[ "${MAG_UPGRADE_SKIP_DEPLOY:-0}" != 1 ]]; then
  say "1. Raum-Release (ops/deploy.sh)"
  bash "$SRC/ops/deploy.sh"
else
  say "1. Raum-Release übersprungen (MAG_UPGRADE_SKIP_DEPLOY=1)"
fi

# ---------------------------------------------------------------- 2. mag-llm neu
say "2. mag-llm: Laden ohne mmap, Pin aus 3.2 entfernen, Neustart"
install -d -m 0755 "$LIB"
install -m 0755 "$SRC/ops/deepseek/post-load.sh" "$LIB/post-load.sh"
install -D -m 0644 "$SRC/ops/warm/needrestart-mag.conf" /etc/needrestart/conf.d/mag-llm.conf
install -D -m 0644 "$SRC/ops/warm/apt-no-auto-reboot.conf" /etc/apt/apt.conf.d/52mag-no-auto-reboot
install -D -m 0644 "$SRC/ops/warm/sysctl-mag-llm.conf" /etc/sysctl.d/60-mag-llm.conf
sysctl -q --load=/etc/sysctl.d/60-mag-llm.conf
unlock
trap 'relock' EXIT
systemctl stop mag-llm.service
if systemctl cat mag-model-pin.service >/dev/null 2>&1; then
  systemctl disable --now mag-model-pin.service || true
  rm -f "$PIN"
fi
install -m 0644 "$tmpunit" "$UNIT"; rm -f "$tmpunit"
systemctl daemon-reload
systemctl reset-failed mag-model-pin.service 2>/dev/null || true
systemctl enable mag-llm.service
t0=$SECONDS
systemctl start mag-llm.service
relock; trap - EXIT
echo "   lädt … (Seiten sind meist noch im Page-Cache, dann geht es deutlich schneller als von der Platte)"
wait_ready 10800 || rollback
printf '   bereit nach %d min %02d s\n' $(((SECONDS - t0) / 60)) $(((SECONDS - t0) % 60))
pid=$(systemctl show -p MainPID --value mag-llm.service)
if grep -q 'DeepSeek-V4-Flash-0731.*\.gguf' "/proc/$pid/maps" 2>/dev/null; then
  echo "WARNUNG: llama nutzt die Dateien weiterhin per mmap – Lademodus prüfen (warm.sh status)"
fi

# ---------------------------------------------------------------- 3. Page-Cache freigeben
say "3. Page-Cache-Kopie freigeben"
bash "$SRC/ops/deepseek/warm.sh" evict-cache

# ---------------------------------------------------------------- 4. Raum
say "4. mag-120: Drop-in (3.4.1-Fix, Thinking-Standard) und Neustart"
install -d "$DROPIN_DIR"
install -m 0644 "$SRC/ops/mag-120.d/20-deepseek-audit.conf" "$DROPIN_DIR/20-deepseek-audit.conf"
rm -f "$DROPIN_DIR/30-thinking.conf"     # manueller Zwischenstand, jetzt im Drop-in
python3 -I "$SRC/ops/check-unit-env.py" "$DROPIN_DIR"
systemctl daemon-reload
since=$(date '+%Y-%m-%d %H:%M:%S')
systemctl restart mag-120.service
for _ in $(seq 1 60); do curl -fsS --max-time 2 http://127.0.0.1:8766/readyz >/dev/null 2>&1 && break; sleep 1; done
j=$(journalctl -u mag-120 --since "$since" --no-pager 2>/dev/null || true)   # erst einsammeln (pipefail/SIGPIPE)
if grep -qi 'invalid environment' <<<"$j"; then
  echo "FEHLER: systemd meldet 'Invalid environment assignment' für mag-120"; exit 1
fi
state=""
for _ in $(seq 1 60); do
  state=$(curl -fsS --max-time 3 http://127.0.0.1:8766/api/model-status 2>/dev/null \
          | python3 -I -c 'import json,sys;print(json.load(sys.stdin).get("state",""))' 2>/dev/null || true)
  [[ "$state" == ready ]] && break; sleep 2
done
echo "   Raum: $(curl -fsS --max-time 3 http://127.0.0.1:8766/readyz 2>/dev/null)"
echo "   Modellstatus im Raum: ${state:-?}"
systemctl show mag-120 -p Environment | tr ' ' '\n' | grep -E 'MAG_ROOM_(SINGLE_BOT|THINKING|MODEL)' | sed 's/^/   /'
[[ "$state" == ready ]] || { echo "FEHLER: Raum sieht das Modell nicht als bereit"; exit 1; }

# ---------------------------------------------------------------- 5. Benchmark
if [[ "${MAG_UPGRADE_SKIP_BENCH:-0}" != 1 ]]; then
  say "5. http-bench --quick (per HTTP gegen den laufenden Server)"
  runuser -u chris -- env LABEL=3.5 bash "$SRC/ops/deepseek/http-bench.sh" --quick \
    || echo "WARNUNG: Benchmark nicht gelaufen (Raum/llama beschäftigt?) – später: bash ops/deepseek/http-bench.sh --quick"
  say "6. Vergleich mit der Basis"
  compare_with_basis
fi

say "Fertig"
bash "$SRC/ops/deepseek/warm.sh" status
echo; echo "Log: $LOG   Sicherung: $BACKUP"
echo "Zurück auf den Stand vor 3.5:  sudo bash $SRC/ops/deepseek/upgrade-3.5.sh --rollback"
