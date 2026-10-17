#!/usr/bin/env bash
# Safe single-command Pages rollback: nginx first, then native room toggle.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'sudo required' >&2; exit 1; }
scratch="$(mktemp -d /etc/nginx/conf.d/.mag-pages-rollback.XXXXXXXX)"
trap 'rm -rf "$scratch"' EXIT
for stem in mag-pages.conf mag-pages-custom.conf; do
 target="/etc/nginx/conf.d/$stem"
 [[ -f "$target" ]] && cp -a "$target" "$scratch/$stem"
 backup="$target.pre-pages"
 if [[ -f "$backup" ]]; then cp -a "$backup" "$target"; else rm -f "$target"; fi
done
if ! nginx -t; then
 for stem in mag-pages.conf mag-pages-custom.conf; do
  target="/etc/nginx/conf.d/$stem"
  [[ -f "$scratch/$stem" ]] && cp -a "$scratch/$stem" "$target"
 done
 echo 'nginx -t failed: prior live configuration restored without reload' >&2
 exit 1
fi
systemctl reload nginx.service
rm -f /etc/systemd/system/mag-acme-renew.service.d/30-pages.conf
rm -f /etc/systemd/system/mag-120.service.d/30-pages.conf
systemctl daemon-reload
systemctl restart mag-120.service
echo 'Pages removed from nginx and native MAG room. mag-llm untouched.'
