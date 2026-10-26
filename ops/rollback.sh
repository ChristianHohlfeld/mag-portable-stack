#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 && ${1:-} =~ ^[a-zA-Z0-9._-]+$ ]] || { echo 'Usage: sudo ops/rollback.sh RELEASE_ID'; exit 1; }
BACKUP="/var/backups/mag-stack/$1"
test -f "$BACKUP/mag-120.service"
systemctl stop mag-120.service
if [[ -f $BACKUP/qwen-layout/helper.sh ]]; then
  bash "$BACKUP/qwen-layout/helper.sh" rollback "$BACKUP"
fi
for config in room-model.env secondary-model.env; do
  [[ ! -f $BACKUP/$config ]] || cp -a "$BACKUP/$config" "/etc/mag-stack/$config"
done
if [[ -d $BACKUP/mag-120.service.d ]]; then
  rm -rf /etc/systemd/system/mag-120.service.d
  cp -a "$BACKUP/mag-120.service.d" /etc/systemd/system/mag-120.service.d
fi
if [[ -f $BACKUP/mag-audit-gateway.service ]]; then
  cp -a "$BACKUP/mag-audit-gateway.service" /etc/systemd/system/mag-audit-gateway.service
fi
if [[ -f $BACKUP/mag-audit-gateway.bin ]]; then
  install -m 0755 "$BACKUP/mag-audit-gateway.bin" /opt/mag-audit/bin/.mag-audit-gateway.restore
  mv -f /opt/mag-audit/bin/.mag-audit-gateway.restore /opt/mag-audit/bin/mag-audit-gateway
fi
cp -a "$BACKUP/mag-120.service" /etc/systemd/system/mag-120.service
cp -a "$BACKUP/magbot" /usr/local/bin/magbot
if [[ -f "$BACKUP/previous-release" ]]; then ln -sfn "$(cat "$BACKUP/previous-release")" /opt/mag-stack/current; fi
systemctl daemon-reload
systemctl restart mag-audit-gateway.service
systemctl restart mag-120.service
if [[ -x /opt/mag-stack/current/bin/mag-browser-webrtc ]]; then
  systemctl restart mag-browser-webrtc.service
else
  systemctl stop mag-browser-webrtc.service >/dev/null 2>&1 || true
fi
# Restore the paired nginx vhost if this release used transactional whois.
if [[ -f "$BACKUP/nginx.staged" || -f "$BACKUP/nginx.verified" ]]; then
  [[ -x "$BACKUP/deploy-nginx.sh" ]] || { echo "Missing ingress rollback helper" >&2; exit 1; }
  MAG_NGINX_TXN="$BACKUP" bash "$BACKUP/deploy-nginx.sh" rollback
fi
for i in {1..30}; do curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:8766/healthz 2>/dev/null && break; sleep 1; done
curl -fsS --max-time 10 http://127.0.0.1:8766/healthz

