#!/usr/bin/env bash
# Safe one-command rollback: only Pages nginx and native worker.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
scratch="$(mktemp -d /etc/nginx/conf.d/.pages-rollback.XXXXXXXX)"
trap 'rm -rf "$scratch"' EXIT
for stem in mag-pages.conf mag-pages-custom.conf; do
  target="/etc/nginx/conf.d/$stem"
  [[ ! -f "$target" ]] || cp -a "$target" "$scratch/$stem"
  backup="$target.pre-pages"
  if [[ -f "$backup" ]]; then cp -a "$backup" "$target"; else rm -f "$target"; fi
done
if ! nginx -t; then
  for stem in mag-pages.conf mag-pages-custom.conf; do
    target="/etc/nginx/conf.d/$stem"
    if [[ -f "$scratch/$stem" ]]; then cp -a "$scratch/$stem" "$target"; else rm -f "$target"; fi
  done
  echo 'nginx -t failed: previous Pages config restored' >&2
  exit 1
fi
systemctl reload nginx.service
rm -f /etc/systemd/system/mag-acme-renew.service.d/30-pages.conf /etc/sudoers.d/mag-pages
rm -f /etc/systemd/system/mag-120.service.d/30-pages-public.conf
rm -f /etc/systemd/system/mag-120.service.d/31-pages-public.conf
rm -f /etc/systemd/system/mag-120.service.d/30-pages.conf
systemctl daemon-reload
systemctl restart mag-120.service
echo 'Pages disabled, mag-120 restarted; mag-llm untouched.'
