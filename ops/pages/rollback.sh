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
rm -f /etc/systemd/system/mag-acme-renew.service.d/30-pages.conf /etc/sudoers.d/mag-pages /etc/systemd/system/mag-pages-reconcile.service
rm -f /etc/systemd/system/mag-120.service.d/30-pages-public.conf
rm -f /etc/systemd/system/mag-120.service.d/31-pages-public.conf
rm -f /etc/systemd/system/mag-120.service.d/30-pages.conf
systemctl daemon-reload
systemctl restart mag-120.service
# All Pages-owned state and per-custom-domain ACME keys are now unreachable
# from nginx. Purge them; shared wildcard cert and lego account stay.
rm -rf --one-file-system -- /var/lib/mag-pages/releases /var/lib/mag-pages/public /var/lib/mag-pages/tailnet /var/lib/mag-pages/custom /var/lib/mag-pages/portal /var/lib/mag-pages/acme
rm -f /var/lib/mag-pages/sites.json /var/lib/mag-pages/custom-domains.tsv /var/lib/mag-pages/gc.pending /var/lib/mag-pages/gc-retired.json /var/lib/mag-pages/request.json /var/lib/mag-pages/.gc.lock
rm -rf --one-file-system -- /etc/mag-pages/tls/custom
if [[ -f /etc/mag-pages/visibility.json && ! -L /etc/mag-pages/visibility.json ]]; then
  printf '{}\n' > /etc/mag-pages/visibility.json
  chown root:magbot /etc/mag-pages/visibility.json
  chmod 0640 /etc/mag-pages/visibility.json
fi
echo 'Pages disabled, site state/cert accounts removed; shared wildcard retained; mag-llm untouched.'
