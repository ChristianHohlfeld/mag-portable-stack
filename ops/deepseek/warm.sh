#!/usr/bin/env bash
# DeepSeek dauerhaft im RAM halten.
#
#   sudo bash ops/deepseek/warm.sh install            Trockenlauf
#   sudo bash ops/deepseek/warm.sh install --apply    einrichten (startet mag-llm NICHT neu)
#   sudo bash ops/deepseek/warm.sh status             liegt das Modell im RAM? gesperrt?
#   sudo bash ops/deepseek/warm.sh restart-llm        gewollter Neustart von llama, Modell bleibt im RAM
#   sudo bash ops/deepseek/warm.sh numa               Verteilung des Modells auf die NUMA-Knoten anzeigen
#   sudo bash ops/deepseek/warm.sh renuma --yes       EINMALIG kalt neu laden, gleichmäßig auf beide Sockel
#                                                     (nur wenn 'numa' fast alles auf einem Knoten zeigt)
#
# Drei Schichten:
#  1. llama sperrt seine Seiten selbst (--load-mode mmap+mlock, LimitMEMLOCK=infinity).
#  2. mag-model-pin.service sperrt die GGUF-Dateien zusätzlich, unabhängig vom llama-Prozess
#     -> ein Absturz/Neustart von llama liest aus dem RAM statt von der Platte.
#  3. Nichts von außen wirft es raus: kein drop_caches mehr, Paket-Updates (needrestart) und
#     unattended-upgrades starten nichts neu, systemd-oomd lässt beide Units in Ruhe,
#     NUMA-Balancing/Zone-Reclaim/Verdichtung verschieben keine Modellseiten.
# Grenze: ein Reboot leert den RAM. Danach liest mag-model-pin das Modell automatisch einmal vor.
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
DIR=/home/chris/models/deepseek-v4
FILES=("$DIR"/DeepSeek-V4-Flash-0731-UD-Q4_K_XL-0000{1..5}-of-00005.gguf)
BACKUP=/home/chris/mag-infra/systemd
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
cmd=${1:-status}; APPLY=0; [[ "${2:-}" == "--apply" ]] && APPLY=1
run() { echo "+ $*"; if [[ $APPLY -eq 1 ]]; then "$@"; fi; }
gib() { awk '{printf "%.1f GiB", $1/1048576}'; }   # kB -> GiB

status() {
  echo "== Units"
  for u in mag-model-pin mag-llm; do
    printf '  %-16s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null || true)"
  done
  echo "== RAM gesamt"
  grep -E '^(MemTotal|MemAvailable|Cached|Mlocked):' /proc/meminfo | while read -r k v _; do printf '  %-14s %s\n' "$k" "$(gib <<<"$v")"; done
  echo "== Gesperrt pro Prozess (VmLck)"
  for u in mag-model-pin mag-llm; do
    pid=$(systemctl show -p MainPID --value "$u" 2>/dev/null || echo 0)
    if [[ -n "$pid" && "$pid" != 0 && -r /proc/$pid/status ]]; then
      printf '  %-16s VmLck %s, RSS %s\n' "$u" \
        "$(awk '/^VmLck/{print $2}' /proc/$pid/status | gib)" "$(awk '/^VmRSS/{print $2}' /proc/$pid/status | gib)"
    else
      printf '  %-16s läuft nicht\n' "$u"
    fi
  done
  echo "== Modelldateien im Page-Cache"
  if command -v vmtouch >/dev/null; then
    vmtouch -m 64G "${FILES[@]}" 2>/dev/null | grep -E 'Files|Resident Pages' | sed 's/^ */  /'
  else
    echo "  (vmtouch fehlt – 'warm.sh install --apply')"
  fi
  echo "== Schutz"
  printf '  numa_balancing=%s zone_reclaim_mode=%s compact_unevictable_allowed=%s\n' \
    "$(cat /proc/sys/kernel/numa_balancing 2>/dev/null)" "$(cat /proc/sys/vm/zone_reclaim_mode)" \
    "$(cat /proc/sys/vm/compact_unevictable_allowed 2>/dev/null)"
  grep -qE '^Exec.*drop_caches' /etc/systemd/system/mag-llm.service 2>/dev/null \
    && echo "  WARNUNG: mag-llm.service leert noch den Page-Cache beim Start (alte Unit)" || echo "  mag-llm.service ohne drop_caches"
  [[ -f /etc/needrestart/conf.d/mag-llm.conf ]] && echo "  needrestart: mag-llm/mag-model-pin ausgenommen" || echo "  needrestart: NICHT ausgenommen"
  [[ -f /etc/apt/apt.conf.d/52mag-no-auto-reboot ]] && echo "  unattended-upgrades: kein Auto-Reboot" || echo "  unattended-upgrades: Auto-Reboot nicht abgeschaltet"
}

numa() {
  command -v numastat >/dev/null || { echo "numastat fehlt (apt install numactl)"; return; }
  for u in mag-model-pin mag-llm; do
    pid=$(systemctl show -p MainPID --value "$u" 2>/dev/null || echo 0)
    [[ -n "$pid" && "$pid" != 0 ]] || { echo "== $u läuft nicht"; continue; }
    echo "== $u (PID $pid), MB pro Knoten"
    numastat -p "$pid" 2>/dev/null | grep -E 'Node|Private|File|Total|^-+' | sed 's/^/  /'
  done
  echo "== Page-Cache der Modelldateien pro Knoten (Summe aller Prozesse)"
  numastat -m 2>/dev/null | grep -E 'Node|FilePages|Mlocked|AnonPages|MemFree' | sed 's/^/  /'
}

renuma() {
  [[ "${2:-}" == "--yes" ]] || { echo "Lädt das Modell EINMAL kalt neu (Dauer = 155 GB von der Platte). Mit '--yes' bestätigen."; exit 1; }
  command -v vmtouch >/dev/null || { echo "vmtouch fehlt – erst 'warm.sh install --apply'"; exit 1; }
  grep -q 'numactl --interleave=all' /etc/systemd/system/mag-llm.service \
    || { echo "mag-llm.service ohne 'numactl --interleave=all' – erst 'warm.sh install --apply'"; exit 1; }
  # RefuseManualStop nur für diesen Vorgang aufheben (Laufzeit-Drop-in, verschwindet spätestens beim Reboot)
  for u in mag-llm mag-model-pin; do
    install -d /run/systemd/system/$u.service.d
    printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/$u.service.d/99-renuma.conf
  done
  trap 'rm -f /run/systemd/system/mag-llm.service.d/99-renuma.conf /run/systemd/system/mag-model-pin.service.d/99-renuma.conf; systemctl daemon-reload' EXIT
  systemctl daemon-reload
  echo "== 1. llama und Pin stoppen"
  systemctl stop mag-llm.service mag-model-pin.service
  echo "== 2. Modellseiten aus dem Page-Cache entfernen"
  vmtouch -q -e -m 64G "${FILES[@]}"
  echo "== 3. Pin liest verschränkt neu (Fortschritt alle 30 s)"
  systemctl start mag-model-pin.service
  for _ in $(seq 1 720); do
    line=$(vmtouch -m 64G "${FILES[@]}" 2>/dev/null | grep 'Resident Pages' || true)
    echo "   $line"
    grep -q ' 100%' <<<"$line" && break
    systemctl is-active --quiet mag-model-pin || { echo "mag-model-pin ist ausgefallen"; journalctl -u mag-model-pin -n 20 --no-pager; exit 1; }
    sleep 30
  done
  echo "== 4. llama starten (lädt aus dem RAM)"
  systemctl start mag-llm.service
  echo "Fertig. Bereit, sobald: curl -s 127.0.0.1:8766/api/model-status -> ready. Danach: warm.sh numa"
}

case "$cmd" in
  numa) numa ;;
  renuma) renuma "$@" ;;
  install)
    for f in "$SRC/ops/mag-model-pin.service" "$SRC/ops/mag-llm.service" "$SRC"/ops/warm/{needrestart-mag.conf,apt-no-auto-reboot.conf,sysctl-mag-llm.conf}; do
      [[ -f "$f" ]] || { echo "fehlt: $f"; exit 1; }
    done
    grep -qE '^Exec.*drop_caches' "$SRC/ops/mag-llm.service" && { echo "Repo-Unit enthält noch drop_caches"; exit 1; }
    echo "== 1. Pakete (vmtouch, numactl)"
    if ! command -v vmtouch >/dev/null || ! command -v numactl >/dev/null; then
      run apt-get install -y --no-install-recommends vmtouch numactl
    fi
    echo "== 2. Units"
    run install -d -o chris -g chris "$BACKUP"
    ts=$(date +%Y%m%d%H%M)
    [[ -e /etc/systemd/system/mag-llm.service ]] && run cp -aL /etc/systemd/system/mag-llm.service "$BACKUP/mag-llm.service.bak-$ts"
    run install -m 0644 "$SRC/ops/mag-llm.service" /etc/systemd/system/mag-llm.service
    run install -m 0644 "$SRC/ops/mag-model-pin.service" /etc/systemd/system/mag-model-pin.service
    echo "== 3. Keine Neustarts/Verdrängung von außen"
    run install -D -m 0644 "$SRC/ops/warm/needrestart-mag.conf" /etc/needrestart/conf.d/mag-llm.conf
    run install -D -m 0644 "$SRC/ops/warm/apt-no-auto-reboot.conf" /etc/apt/apt.conf.d/52mag-no-auto-reboot
    run install -D -m 0644 "$SRC/ops/warm/sysctl-mag-llm.conf" /etc/sysctl.d/60-mag-llm.conf
    run sysctl -q --load=/etc/sysctl.d/60-mag-llm.conf
    echo "== 4. Aktivieren (mag-llm läuft ungestört weiter; die neue Unit gilt ab dem nächsten Start)"
    run systemctl daemon-reload
    run systemctl enable mag-model-pin.service
    if [[ -f /var/lib/mag-llm/verified.stamp ]]; then
      run systemctl start --no-block mag-model-pin.service
    else
      echo "   Modell noch nicht freigegeben – mag-model-pin startet zusammen mit mag-llm nach der Prüfung"
    fi
    [[ $APPLY -eq 0 ]] && { echo "(Trockenlauf – mit 'install --apply' ausführen)"; exit 0; }
    sleep 3; status ;;
  status)
    status ;;
  restart-llm)
    # RefuseManualStop sperrt stop/restart; kill + Restart=always ist der gewollte Weg.
    systemctl is-active --quiet mag-model-pin || { echo "mag-model-pin läuft nicht – Neustart würde von der Platte laden. Abbruch."; exit 1; }
    echo "Beende llama (Modell bleibt über mag-model-pin im RAM), systemd startet es in 10 s neu …"
    systemctl kill -s TERM mag-llm.service
    echo "Verlauf: journalctl -fu mag-llm   |   bereit: curl -s 127.0.0.1:8766/api/model-status" ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
