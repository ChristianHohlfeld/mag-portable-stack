#!/usr/bin/env bash
# One-command rollback: remove MAG Pages vhosts, stop its timers, preserve site files.
set -euo pipefail
test "$EUID" -eq 0 || { echo 'Run as root' >&2; exit 1; }
target=/etc/nginx/conf.d/mag-pages-generated.conf
snapshot=/etc/nginx/conf.d/mag-pages-generated.conf.pre-pages
systemctl disable --now mag-pages-sync.timer mag-pages-cert.timer 2>/dev/null || true
systemctl stop mag-pages-sync.service 2>/dev/null || true
backup="$(mktemp /tmp/mag-pages-nginx.XXXXXXXX)"
if [[ -f "$target" ]]; then cp -a "$target" "$backup"; fi
if [[ -f "$snapshot" ]]; then cp -a "$snapshot" "$target"; else rm -f "$target"; fi
if ! nginx -t; then
    if [[ -s "$backup" ]]; then cp -a "$backup" "$target"; fi
    echo 'nginx -t failed: reverted rollback, no reload performed' >&2
    exit 1
fi
systemctl reload nginx.service
rm -f "$backup"
echo 'MAG Pages disabled. Previous nginx configuration restored; mag-llm untouched.'
