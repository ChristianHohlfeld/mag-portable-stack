#!/usr/bin/env bash
# Only run after owner role and exact Tailscale login have been confirmed from
# a SEPARATE authenticated tailnet client (localhost curl is NOT a valid check).
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
[[ $# == 1 && $1 == --owner-verified ]] || {
 echo "Requires --owner-verified after external /api/state role/login check" >&2; exit 2;
}
state=/var/lib/mag-private-cutover
[[ -f $state/pending ]] || { echo "No pending cutover" >&2; exit 1; }
systemctl is-active --quiet mag-private-rollback.timer
systemctl is-active --quiet mag-120.service
nginx -t
grep -F 'proxy_pass http://127.0.0.1:8766/_internal/tailscale/whois;' /etc/nginx/sites-available/mag-room.conf >/dev/null
systemctl stop mag-private-rollback.timer
rm -f "$state/pending"
install -m 0600 /dev/null "$state/confirmed"
systemctl enable --now mag-acme-renew.timer
# Legacy Python whois is no longer on the request path. Remove only AFTER
# external Owner verification, so an older setup is never broken mid-cutover.
systemctl disable --now mag-whois.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/mag-whois.service /usr/local/lib/mag-private/tailscale-whois.py
systemctl daemon-reload
echo "Owner confirmation recorded. Watchdog disarmed; Caddy may now be retired."
