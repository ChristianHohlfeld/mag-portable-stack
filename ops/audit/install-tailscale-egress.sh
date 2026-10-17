#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
here="$(cd "$(dirname "$0")" && pwd)"
id -u magbot | grep -qx 993
nft list chain inet mag_audit output >/dev/null
install -D -o root -g root -m 0755 "$here/refresh-tailscale-egress.sh" /usr/local/lib/mag-audit/refresh-tailscale-egress.sh
install -D -o root -g root -m 0644 "$here/mag-tailscale-egress.service" /etc/systemd/system/mag-tailscale-egress.service
install -D -o root -g root -m 0644 "$here/mag-tailscale-egress.timer" /etc/systemd/system/mag-tailscale-egress.timer
install -D -o root -g root -m 0644 "$here/40-tailscale-api-egress.conf" /etc/systemd/system/mag-audit-nft.service.d/40-tailscale-api-egress.conf
systemctl daemon-reload
systemctl start mag-tailscale-egress.service
systemctl enable --now mag-tailscale-egress.timer
echo 'Installed narrow Tailscale API egress; no audit gateway or mag-llm restart'
