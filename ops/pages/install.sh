#!/usr/bin/env bash
# Configuration only: no new publisher/ACME service or timer; mag-llm untouched.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
here="$(cd "$(dirname "$0")" && pwd)"
[[ -f /etc/mag-pages/pages.env ]] || { echo 'Install pages.env.example first' >&2; exit 1; }
. /etc/mag-pages/pages.env
for val in "$PAGES_PUBLIC_BIND" "$PAGES_PUBLIC_DNS" "$PAGES_TAILNET_BIND"; do
 [[ "$val" =~ ^[0-9.]+$ ]] || exit 1
done
[[ "$PAGES_ZONE" =~ ^[a-z0-9.-]+$ && "$PAGES_PUBLIC_BIND" != "$PAGES_TAILNET_BIND" ]] || exit 1
for bind in "$PAGES_PUBLIC_BIND" "$PAGES_TAILNET_BIND"; do
 ip -4 -o addr show | grep -Fq " $bind/" || { echo "Missing local IP: $bind" >&2; exit 1; }
done
test -s /etc/mag-pages/gitea-token || { echo 'Create read-only Gitea service token first' >&2; exit 1; }
test -s /etc/mag-pages/tls/current/cert.pem
test -s /etc/mag-pages/tls/current/key.pem
command -v nginx >/dev/null
command -v /usr/local/bin/lego >/dev/null
command -v dig >/dev/null
getent passwd magbot >/dev/null
chown root:magbot /etc/mag-pages /etc/mag-pages/gitea-token
chmod 0750 /etc/mag-pages
chmod 0640 /etc/mag-pages/gitea-token
if [[ ! -e /etc/mag-pages/visibility.json ]]; then printf '{}\n' > /etc/mag-pages/visibility.json; fi
chown root:magbot /etc/mag-pages/visibility.json
chmod 0640 /etc/mag-pages/visibility.json
install -d -m 0755 -o magbot -g magbot /var/lib/mag-pages \
 /var/lib/mag-pages/releases /var/lib/mag-pages/public /var/lib/mag-pages/tailnet \
 /var/lib/mag-pages/custom
install -d -m 0755 -o root -g root /var/lib/mag-pages/acme
install -d -m 0755 /usr/local/lib/mag-pages /etc/nginx/conf.d
install -m 0755 "$here/issue-wildcard.sh" "$here/refresh-custom.sh" /usr/local/lib/mag-pages/
backup=/etc/nginx/conf.d/mag-pages.conf.pre-pages
if [[ -e /etc/nginx/conf.d/mag-pages.conf && ! -e "$backup" ]]; then cp -a /etc/nginx/conf.d/mag-pages.conf "$backup"; fi
zone_regex="$(printf %s "$PAGES_ZONE" | sed 's/[.]/\\\\./g')"
sed -e "s|@PUBLIC_BIND@|$PAGES_PUBLIC_BIND|g" \
    -e "s|@TAILNET_BIND@|$PAGES_TAILNET_BIND|g" \
    -e "s|@ZONE_REGEX@|$zone_regex|g" "$here/mag-pages.nginx.in" \
    > /etc/nginx/conf.d/mag-pages.conf
if ! nginx -t; then
 if [[ -f "$backup" ]]; then cp -a "$backup" /etc/nginx/conf.d/mag-pages.conf
 else rm -f /etc/nginx/conf.d/mag-pages.conf; fi
 echo 'nginx -t failed: old config restored and NOT reloaded' >&2; exit 1
fi
systemctl reload nginx.service
# Attach to the EXISTING MAG ACME timer without creating a new timer.
install -d -m 0755 /etc/systemd/system/mag-acme-renew.service.d
printf '[Service]\nExecStartPost=-/usr/local/lib/mag-pages/refresh-custom.sh\n' \
 > /etc/systemd/system/mag-acme-renew.service.d/30-pages.conf
systemctl daemon-reload
echo 'Pages nginx ready. Enable magroom C++ using mag-pages-room.conf when drained.'
