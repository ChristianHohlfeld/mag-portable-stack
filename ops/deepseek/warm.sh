#!/usr/bin/env bash
# DeepSeek dauerhaft im RAM halten (ab 3.5: Gewichte im eigenen Prozessspeicher, ohne mmap).
#
#   sudo bash ops/deepseek/warm.sh install            Trockenlauf
#   sudo bash ops/deepseek/warm.sh install --apply    Schutz einrichten + Unit rendern (startet mag-llm NICHT neu)
#   sudo bash ops/deepseek/warm.sh status             Modell im RAM? gesperrt? Page-Cache-Kopie weg?
#   sudo bash ops/deepseek/warm.sh numa               Verteilung auf die NUMA-Knoten
#   sudo bash ops/deepseek/warm.sh evict-cache        Page-Cache-Kopie der Modelldateien freigeben
#   sudo bash ops/deepseek/warm.sh restart-llm        gewollter Neustart (lädt ~144 GiB neu, einige Minuten)
#
# Was das Modell im RAM hält:
#  1. llama lädt die Gewichte in den eigenen Speicher und sperrt ihn (mlock, LimitMEMLOCK=infinity);
#     MemorySwapMax=0 verbietet zusätzlich jedes Auslagern.
#  2. Nichts von außen wirft es raus: Paket-Updates (needrestart) und unattended-upgrades starten
#     nichts neu, systemd-oomd lässt die Unit in Ruhe, OOMScoreAdjust=-900, RefuseManualStop.
#  3. NUMA-Balancing/Zone-Reclaim/Verdichtung verschieben keine Modellseiten (sysctl).
#  4. mag-model-pin (vmtouch -l) hält die GGUF-Dateien zusätzlich gesperrt im Page-Cache. Kostet eine
#     zweite 144-GiB-Kopie (Chris: gewollt), dafür lädt jeder Neustart/Rollback von llama aus dem RAM.
# Grenze: Nach einem Reboot liest der Pin die Dateien einmal von der Platte vor.
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
LIB=/usr/local/lib/mag-deepseek
DIR=/home/chris/models/deepseek-v4
FILES=("$DIR"/DeepSeek-V4-Flash-0731-UD-Q4_K_XL-0000{1..5}-of-00005.gguf)
BACKUP=/home/chris/mag-infra/systemd
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
cmd=${1:-status}; APPLY=0; [[ "${2:-}" == "--apply" ]] && APPLY=1
run() { echo "+ $*"; if [[ $APPLY -eq 1 ]]; then "$@"; fi; }
gib() { awk '{printf "%.1f GiB", $1/1048576}'; }   # kB -> GiB

llm_pid() { systemctl show -p MainPID --value mag-llm.service 2>/dev/null || echo 0; }

page_cache() {   # Anteil der Modelldateien im Page-Cache (mincore, ohne Zusatzpakete)
  python3 -I - "${FILES[@]}" <<'PY'
import ctypes, mmap, os, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
PS = os.sysconf("SC_PAGE_SIZE"); tot = res = 0
for f in sys.argv[1:]:
    try:
        fd = os.open(f, os.O_RDONLY); size = os.fstat(fd).st_size
    except OSError:
        continue
    addr = libc.mmap(None, size, mmap.PROT_READ, mmap.MAP_SHARED, fd, 0)
    n = (size + PS - 1) // PS; vec = ctypes.create_string_buffer(n)
    if addr not in (None, ctypes.c_void_p(-1).value) and libc.mincore(addr, size, vec) == 0:
        res += sum(b & 1 for b in vec.raw) * PS
    libc.munmap(addr, size); os.close(fd); tot += size
print(f"  Modelldateien im Page-Cache: {res/2**30:.1f} von {tot/2**30:.1f} GiB" + ("  (ok: keine zweite Kopie)" if res < 2**30 else ""))
PY
}

status() {
  echo "== mag-llm: $(systemctl is-active mag-llm 2>/dev/null || true)"
  pid=$(llm_pid)
  if [[ -n "$pid" && "$pid" != 0 && -r /proc/$pid/status ]]; then
    printf '  VmLck %s, VmRSS %s (davon Datei-Seiten %s)\n' \
      "$(awk '/^VmLck/{print $2}' /proc/$pid/status | gib)" "$(awk '/^VmRSS/{print $2}' /proc/$pid/status | gib)" \
      "$(awk '/^RssFile/{print $2}' /proc/$pid/status | gib)"
    if grep -q 'DeepSeek-V4-Flash-0731.*\.gguf' "/proc/$pid/maps" 2>/dev/null; then
      echo "  Lademodus: mmap (Gewichte = Page-Cache)"
    else
      echo "  Lademodus: eigener Speicher (kein mmap)"
    fi
    tr '\0' ' ' < "/proc/$pid/cmdline" | grep -oE -- '--(load-mode [^ ]+|no-mmap|mlock|threads(-batch)? [0-9]+|(u)?batch-size [0-9]+)' | sed 's/^/  /'
  fi
  echo "== Bereit"
  printf '  /health: %s\n' "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health; true)"
  echo "== RAM"
  grep -E '^(MemTotal|MemAvailable|Cached|Mlocked|AnonPages):' /proc/meminfo | while read -r k v _; do printf '  %-14s %s\n' "$k" "$(gib <<<"$v")"; done
  page_cache
  echo "== Schutz"
  printf '  numa_balancing=%s zone_reclaim_mode=%s compact_unevictable_allowed=%s\n' \
    "$(cat /proc/sys/kernel/numa_balancing 2>/dev/null)" "$(cat /proc/sys/vm/zone_reclaim_mode)" \
    "$(cat /proc/sys/vm/compact_unevictable_allowed 2>/dev/null)"
  [[ -f /etc/needrestart/conf.d/mag-llm.conf ]] && echo "  needrestart: mag-llm ausgenommen" || echo "  needrestart: NICHT ausgenommen"
  [[ -f /etc/apt/apt.conf.d/52mag-no-auto-reboot ]] && echo "  unattended-upgrades: kein Auto-Reboot" || echo "  unattended-upgrades: Auto-Reboot nicht abgeschaltet"
  echo "== mag-model-pin: $(systemctl is-active mag-model-pin 2>/dev/null || true) ($(systemctl is-enabled mag-model-pin 2>/dev/null || echo fehlt))"
  command -v vmtouch >/dev/null && vmtouch "${FILES[@]}" 2>/dev/null | grep -E 'Files|Resident' | sed 's/^ */  /' || true
}

numa() {
  command -v numastat >/dev/null || { echo "numastat fehlt (apt install numactl)"; return; }
  pid=$(llm_pid)
  [[ -n "$pid" && "$pid" != 0 ]] || { echo "mag-llm läuft nicht"; return; }
  echo "== mag-llm (PID $pid), MB pro Knoten"
  numastat -p "$pid" 2>/dev/null | grep -E 'Node|Private|Heap|Total|^-+' | sed 's/^/  /'
}

evict() {
  if systemctl is-active --quiet mag-model-pin; then
    echo "mag-model-pin aktiv – Page-Cache bleibt absichtlich (Neustarts laden aus dem RAM)"; return
  fi
  pid=$(llm_pid)
  if [[ -n "$pid" && "$pid" != 0 ]] && grep -q 'DeepSeek-V4-Flash-0731.*\.gguf' "/proc/$pid/maps" 2>/dev/null; then
    echo "llama nutzt die Dateien per mmap – Page-Cache wird NICHT freigegeben"; return
  fi
  python3 -I - "${FILES[@]}" <<'PY'
import os, sys
for f in sys.argv[1:]:
    try:
        fd = os.open(f, os.O_RDONLY); os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED); os.close(fd)
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"{f}: {e}")
PY
  page_cache
}

# Richtet den Page-Cache-Pin ein (startet NUR mag-model-pin, nie mag-llm)
install_pin() {
  if [[ -f /etc/systemd/system/mag-model-pin.service.d/90-numa-retired.conf ]]; then
    echo "mag-model-pin retired by node0 isolation: do not reinstall/reenable vmtouch"
    return 0
  fi
  echo "== mag-model-pin einrichten"
  run install -m 0644 "$SRC/ops/mag-model-pin.service" /etc/systemd/system/mag-model-pin.service
  run systemctl daemon-reload
  run systemctl enable mag-model-pin.service
  [[ -f /var/lib/mag-llm/verified.stamp ]] && run systemctl start --no-block mag-model-pin.service || true
}

# (alt, nicht mehr aufgerufen) Entfernt den Page-Cache-Pin aus 3.2 (RefuseManualStop nur für diesen Vorgang per Laufzeit-Drop-in aufheben)
remove_pin() {
  systemctl cat mag-model-pin.service >/dev/null 2>&1 || return 0
  echo "== mag-model-pin (Page-Cache-Pin aus 3.2) entfernen"
  [[ $APPLY -eq 1 ]] || { echo "+ stop/disable/rm mag-model-pin.service"; return 0; }
  install -d -o chris -g chris "$BACKUP"
  cp -a /etc/systemd/system/mag-model-pin.service "$BACKUP/mag-model-pin.service.bak-$(date +%Y%m%d%H%M)" 2>/dev/null || true
  install -d /run/systemd/system/mag-model-pin.service.d
  printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/mag-model-pin.service.d/99-remove.conf
  systemctl daemon-reload
  systemctl disable --now mag-model-pin.service || true
  rm -f /etc/systemd/system/mag-model-pin.service /run/systemd/system/mag-model-pin.service.d/99-remove.conf
  systemctl daemon-reload
  systemctl reset-failed mag-model-pin.service 2>/dev/null || true
}

case "$cmd" in
  status) status ;;
  numa) numa ;;
  evict-cache) evict ;;
  install)
    for f in "$SRC/ops/mag-llm.service" "$SRC"/ops/warm/{needrestart-mag.conf,apt-no-auto-reboot.conf,sysctl-mag-llm.conf} \
             "$SRC/ops/deepseek/render-llm-unit.sh" "$SRC/ops/deepseek/post-load.sh" "$SRC/ops/mag-model-pin.service"; do
      [[ -f "$f" ]] || { echo "fehlt: $f"; exit 1; }
    done
    echo "== 1. Pakete"
    command -v numactl >/dev/null || run apt-get install -y --no-install-recommends numactl
    echo "== 2. Unit rendern und prüfen (Lademodus ohne mmap)"
    bash "$SRC/ops/deepseek/render-llm-unit.sh" "$SRC/ops/mag-llm.service" --check
    echo "== 3. Dateien"
    run install -d -m 0755 "$LIB"
    run install -m 0755 "$SRC/ops/deepseek/post-load.sh" "$LIB/post-load.sh"
    run install -d -o chris -g chris "$BACKUP"
    ts=$(date +%Y%m%d%H%M)
    [[ -e /etc/systemd/system/mag-llm.service ]] && run cp -aL /etc/systemd/system/mag-llm.service "$BACKUP/mag-llm.service.bak-$ts"
    if [[ $APPLY -eq 1 ]]; then bash "$SRC/ops/deepseek/render-llm-unit.sh" "$SRC/ops/mag-llm.service" /etc/systemd/system/mag-llm.service
    else echo "+ render -> /etc/systemd/system/mag-llm.service"; fi
    run install -D -m 0644 "$SRC/ops/warm/needrestart-mag.conf" /etc/needrestart/conf.d/mag-llm.conf
    run install -D -m 0644 "$SRC/ops/warm/apt-no-auto-reboot.conf" /etc/apt/apt.conf.d/52mag-no-auto-reboot
    run install -D -m 0644 "$SRC/ops/warm/sysctl-mag-llm.conf" /etc/sysctl.d/60-mag-llm.conf
    run sysctl -q --load=/etc/sysctl.d/60-mag-llm.conf
    install_pin
    run systemctl daemon-reload
    run systemctl enable mag-llm.service
    echo "(mag-llm läuft unverändert weiter; die neue Unit gilt ab dem nächsten Start)"
    [[ $APPLY -eq 0 ]] && echo "(Trockenlauf – mit 'install --apply' ausführen)"
    exit 0 ;;
  restart-llm)
    echo "Neustart von llama: lädt ~144 GiB neu (einige Minuten). Der Raum zeigt solange 'Modell lädt'."
    systemctl kill -s TERM mag-llm.service      # RefuseManualStop sperrt stop/restart; Restart=always startet neu
    echo "Verlauf: journalctl -fu mag-llm   |   bereit: curl -s 127.0.0.1:8766/api/model-status" ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac
