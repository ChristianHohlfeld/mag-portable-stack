#!/usr/bin/env bash
# Emergency/manual or timed nginx rollback. Never restarts mag-llm/room.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
site=/etc/nginx/sites-available/mag-room.conf
backup=$site.pre-whois
enabled=/etc/nginx/sites-enabled/mag-room.conf
state=/var/lib/mag-private-cutover
[[ -s $backup && ! -L $backup ]] || { echo "Original config missing; manual recovery needed" >&2; exit 1; }
original=$(cat "$state/enabled.before" 2>/dev/null || echo ABSENT)
cp -a -- "$backup" "$site.rollback"
mv -f -- "$site.rollback" "$site"
if [[ $original == ABSENT ]]; then rm -f -- "$enabled"
else ln -sfn -- "$original" "$enabled"; fi
nginx -t
systemctl reload nginx.service
rm -f "$state/pending" "$state/confirmed"
systemctl stop mag-private-rollback.timer 2>/dev/null || true
echo "Previous nginx site restored; mag-llm untouched."
