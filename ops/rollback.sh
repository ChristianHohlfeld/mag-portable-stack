#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 && ${1:-} =~ ^[a-zA-Z0-9._-]+$ ]] || { echo 'Usage: sudo ops/rollback.sh RELEASE_ID'; exit 1; }
BACKUP="/var/backups/mag-stack/$1"
test -f "$BACKUP/mag-120.service"
systemctl stop mag-120.service
cp -a "$BACKUP/mag-120.service" /etc/systemd/system/mag-120.service
cp -a "$BACKUP/magbot" /usr/local/bin/magbot
if [[ -f "$BACKUP/previous-release" ]]; then ln -sfn "$(cat "$BACKUP/previous-release")" /opt/mag-stack/current; fi
if [[ -f "$BACKUP/frontend.html" ]]; then cp -a "$BACKUP/frontend.html" /var/lib/magbot/room/frontend.html; fi
systemctl daemon-reload
systemctl restart mag-120.service
curl -fsS --max-time 10 http://127.0.0.1:8766/healthz
