#!/usr/bin/env bash
# Restore the prior private nginx vhost; do not stop nginx or mag-llm.
set -euo pipefail
[[ $EUID -eq 0 ]] || exit 1
backup=/etc/nginx/sites-available/mag-room.conf.pre-whois
[[ -s "$backup" ]] || { echo "No prior nginx config backup. Manual rollback required." >&2; exit 1; }
cp -a "$backup" /etc/nginx/sites-available/mag-room.conf
nginx -t
systemctl reload nginx.service
systemctl disable --now mag-whois.service 2>/dev/null || true
echo "Previous nginx vhost restored. mag-llm untouched."
