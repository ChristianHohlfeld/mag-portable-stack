#!/usr/bin/env bash
# Rollback des Browser-Passthrough (8889) + Google-Chrome-Headful auf den Stand davor.
# Aufruf: sudo ops/audit/browser-passthrough-rollback.sh <BACKUP_DIR> [agentN]
# BACKUP_DIR wurde beim Deploy angelegt (/var/backups/mag-browser-pt-<ts>).
set -euo pipefail
B="${1:?Backup-Verzeichnis angeben}"; AG="${2:-agent1}"
install -m644 "$B/10-binary.conf" /etc/systemd/system/mag-audit-webproxy.service.d/10-binary.conf
install -m644 "$B/mag-audit.nft" /etc/nftables.d/mag-audit.nft
install -m755 "$B/vm-proxy-setup.sh" /etc/mag-audit/vm/vm-proxy-setup.sh
PREV="$(cat "$B/current.target")"
ln -sfn "$PREV" /opt/mag-stack/current.rb && mv -Tf /opt/mag-stack/current.rb /opt/mag-stack/current
nft -f /etc/nftables.d/mag-audit.nft
bash /opt/mag-stack/current/ops/firewall.sh
systemctl daemon-reload
systemctl restart mag-audit-webproxy mag-120
/usr/local/sbin/magbot-agent-ctl audit-proxy "$AG"   # Policy zurück auf :8888 (MITM)
echo "Rollback fertig -> $PREV (Google Chrome bleibt installiert; Chromium-Pfad wieder aktiv)"
