#!/usr/bin/env bash
# mag ik-Prefetch: zurück auf den Stand vor apply.sh (Unit, llm.env, Prefetch-Drop-in, ik-patches/0003).
#
#   sudo bash rollback.sh                    letzte Sicherung (/home/chris/mag-infra/systemd/ik-prefetch-latest)
#   sudo BACKUP=<ordner> bash rollback.sh    bestimmte Sicherung
#
# Das alte Binary wurde nie überschrieben (build-ik.sh baut in einen neuen Ordner), der Neustart lädt aus dem RAM.
# Neu gestartet wird nur, wenn sich Unit, llm.env oder Drop-in tatsächlich geändert hatten.
set -euo pipefail
REPO=${REPO:-/home/chris/mag-stack}
UNIT=/etc/systemd/system/mag-llm.service
PF_DROPIN=/etc/systemd/system/mag-llm.service.d/61-mag-prefetch.conf
PATCH=$REPO/ops/deepseek/ik-patches/0003-iqk-prefetch.patch
BACKUP=${BACKUP:-$(readlink -f /home/chris/mag-infra/systemd/ik-prefetch-latest 2>/dev/null || true)}

[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
[[ -n "$BACKUP" && -f "$BACKUP/mag-llm.service" ]] || { echo "keine Sicherung gefunden (BACKUP=${BACKUP:-leer})"; exit 1; }
# shellcheck disable=SC1091
source "$REPO/ops/deepseek/llm-config.sh"   # setzt MAG_LLM_ENV
health() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health 2>/dev/null; true; }
same() { # $1 Sicherung (darf fehlen = "gab es nicht"), $2 live
  if [[ -f "$1" ]]; then [[ -f "$2" ]] && cmp -s "$1" "$2"; else [[ ! -f "$2" ]]; fi
}
echo "Rollback aus $BACKUP"

changed=0
same "$BACKUP/mag-llm.service"      "$UNIT"         || changed=1
same "$BACKUP/llm.env"              "$MAG_LLM_ENV"  || changed=1
same "$BACKUP/61-mag-prefetch.conf" "$PF_DROPIN"    || changed=1

cp -a "$BACKUP/mag-llm.service" "$UNIT"
if [[ -f "$BACKUP/llm.env" ]]; then cp -a "$BACKUP/llm.env" "$MAG_LLM_ENV"; else rm -f "$MAG_LLM_ENV"; fi
if [[ -f "$BACKUP/61-mag-prefetch.conf" ]]; then cp -a "$BACKUP/61-mag-prefetch.conf" "$PF_DROPIN"; else rm -f "$PF_DROPIN"; fi
# 0003 aus ik-patches nehmen, damit der nächste build-ik.sh-Lauf wieder den 3.8-Stand baut
if [[ -f "$BACKUP/0003-iqk-prefetch.patch.vorher" ]]; then cp -a "$BACKUP/0003-iqk-prefetch.patch.vorher" "$PATCH"; else rm -f "$PATCH"; fi

if (( changed == 0 )); then
  systemctl daemon-reload
  echo "Unit/llm.env/Drop-in waren unverändert – kein Neustart nötig. Patch-Datei entfernt."
  exit 0
fi

install -d /run/systemd/system/mag-llm.service.d
printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/mag-llm.service.d/99-upgrade.conf
systemctl daemon-reload
systemctl restart mag-llm.service
rm -f /run/systemd/system/mag-llm.service.d/99-upgrade.conf
systemctl daemon-reload

deadline=$((SECONDS + 5400)); last=0
r0=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
while (( SECONDS < deadline )); do
  [[ "$(health)" == 200 ]] && { echo "Stand vor apply.sh läuft wieder ($(cat "$BACKUP/ik-bin.vorher" 2>/dev/null || echo '?'))."; exit 0; }
  r=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
  (( r - r0 >= 2 )) && { echo "mag-llm stürzt ab – journalctl -u mag-llm prüfen"; exit 1; }
  if (( SECONDS - last >= 60 )); then last=$SECONDS; echo "   $(date +%H:%M:%S) wartet auf /health"; fi
  sleep 5
done
echo "Zeitlimit – journalctl -u mag-llm prüfen"; exit 1
