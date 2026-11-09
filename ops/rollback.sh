#!/usr/bin/env bash
# Return to the release that was live before RELEASE_ID was deployed:
#   sudo bash ops/rollback.sh RELEASE_ID
# Restores executables and unit files from /var/backups/mag-stack/RELEASE_ID.
# Room data is not rolled back (the copy stays in that directory under room/),
# and no model is touched.
set -euo pipefail
[[ $EUID == 0 && ${1:-} =~ ^[a-zA-Z0-9._-]+$ ]] || { echo 'Usage: sudo ops/rollback.sh RELEASE_ID' >&2; exit 1; }
BACKUP="/var/backups/mag-stack/$1"
UNITS=/etc/systemd/system
[[ -f $BACKUP/previous-release ]] || { echo "no rollback snapshot for $1" >&2; exit 1; }
previous=$(cat "$BACKUP/previous-release")
[[ -x $previous/bin/magroom ]] || { echo "previous release $previous is gone" >&2; exit 1; }
restore() { if [[ -e $BACKUP/$1 ]]; then rm -rf "$2"; cp -a "$BACKUP/$1" "$2"; fi; }
restore mag-120.service $UNITS/mag-120.service
restore mag-120.service.d $UNITS/mag-120.service.d
restore mag-browser-webrtc.service $UNITS/mag-browser-webrtc.service
restore mag-audit-gateway.service $UNITS/mag-audit-gateway.service
restore mag-audit-gateway.bin /opt/mag-audit/bin/mag-audit-gateway
restore magbot /usr/local/bin/magbot
ln -sfn "$previous" /opt/mag-stack/.current-rollback
mv -Tf /opt/mag-stack/.current-rollback /opt/mag-stack/current
systemctl daemon-reload
systemctl restart mag-audit-gateway.service || true
systemctl restart mag-120.service
systemctl restart mag-browser-webrtc.service || true
if [[ -f $BACKUP/nginx.staged || -f $BACKUP/nginx.verified ]] && [[ -x $BACKUP/deploy-nginx.sh ]]; then
  MAG_NGINX_TXN="$BACKUP" bash "$BACKUP/deploy-nginx.sh" rollback
fi
for _ in {1..30}; do
  curl --noproxy '*' -fsS --max-time 2 -o /dev/null http://127.0.0.1:8766/readyz 2>/dev/null && { echo "rolled back to $previous"; exit 0; }
  sleep 1
done
echo 'room did not answer /readyz after rollback' >&2
exit 1
