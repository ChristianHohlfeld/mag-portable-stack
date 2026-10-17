#!/usr/bin/env bash
# Disable pages in nginx and magroom; NEVER restart mag-llm.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
rm -f /etc/systemd/system/mag-acme-renew.service.d/30-pages.conf
for stem in mag-pages.conf mag-pages-custom.conf; do
 target="/etc/nginx/conf.d/$stem"
 backup="$target.pre-pages"
 if [[ -f "$backup" ]]; then cp -a "$backup" "$target"; else rm -f "$target"; fi
done
nginx -t
rm -f /etc/systemd/system/mag-120.service.d/30-pages.conf
systemctl daemon-reload
systemctl reload nginx.service
systemctl restart mag-120.service
echo 'Pages disabled; mag-llm untouched.'
