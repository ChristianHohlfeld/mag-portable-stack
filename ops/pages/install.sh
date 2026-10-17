#!/usr/bin/env bash
# Host-only nginx configuration. Never restart mag-llm or mag-120 here.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Root required' >&2; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
[[ -r /etc/mag-pages/pages.env ]] || { echo 'Configure /etc/mag-pages/pages.env' >&2; exit 1; }
. /etc/mag-pages/pages.env
: "${PAGES_PUBLIC_BIND:=}"
: "${PAGES_PUBLIC_DNS:=}"
: "${PAGES_TAILNET_BIND:=100.123.114.16}"
"$here/render-nginx.sh" >/dev/null # validates both addresses and zone
command -v nginx >/dev/null
command -v jq >/dev/null
command -v dig >/dev/null
command -v /usr/local/bin/lego >/dev/null
getent passwd magbot >/dev/null
systemctl cat mag-acme-renew.service >/dev/null
systemctl is-enabled --quiet mag-acme-renew.timer || { echo 'Enable existing MAG ACME timer first' >&2; exit 1; }
ip -4 -o addr show dev tailscale0 | grep -Fq " $PAGES_TAILNET_BIND/" ||
 { echo 'Tailnet address must be on tailscale0' >&2; exit 1; }
if [[ -n "$PAGES_PUBLIC_BIND" ]]; then
  ip -4 -o addr show | grep -Fq " $PAGES_PUBLIC_BIND/" || { echo 'Public bind must be local interface IP' >&2; exit 1; }
  if ip -4 -o addr show dev tailscale0 | grep -Fq " $PAGES_PUBLIC_BIND/"; then
    echo 'Public bind cannot be on tailscale0' >&2; exit 1
  fi
fi
# Existing PAT: preserve its bytes; enforce least-privilege filesystem permissions.
token=/etc/mag-pages/gitea-token
[[ -s "$token" && ! -L "$token" ]] || { echo 'Existing Gitea read:repository PAT missing' >&2; exit 1; }
chown root:magbot /etc/mag-pages "$token"
chmod 0750 /etc/mag-pages
chmod 0640 "$token"
runuser -u magbot -- test -r "$token" || { echo 'magbot cannot read Pages Gitea PAT' >&2; exit 1; }
[[ -s /etc/mag-pages/tls/current/cert.pem && -s /etc/mag-pages/tls/current/key.pem ]] ||
 { echo 'Issue DNS-01 Pages wildcard first (issue-wildcard.sh run)' >&2; exit 1; }
if [[ ! -e /etc/mag-pages/visibility.json ]]; then printf '{}\n' > /etc/mag-pages/visibility.json; fi
chown root:magbot /etc/mag-pages/visibility.json
chmod 0640 /etc/mag-pages/visibility.json
install -d -m 0755 -o magbot -g magbot /var/lib/mag-pages \
 /var/lib/mag-pages/releases /var/lib/mag-pages/public /var/lib/mag-pages/tailnet /var/lib/mag-pages/custom
install -d -m 0755 -o root -g root /var/lib/mag-pages/acme /usr/local/lib/mag-pages /etc/nginx/conf.d
# Dedicated apex landing is root-owned; never served on a public listener.
install -d -m 0755 -o root -g root /var/lib/mag-pages/portal
install -m 0644 -o root -g root "$here/index.html" /var/lib/mag-pages/portal/index.html
install -m 0755 -o root -g root "$here/issue-wildcard.sh" "$here/refresh-custom.sh" "$here/render-nginx.sh" "$here/pages-gc.sh" "$here/pages-policy.sh" "$here/pages-reconcile.sh" /usr/local/lib/mag-pages/
# Exact fixed systemd start command. It enters a fresh ROOT mount namespace,
# never the room's ProtectSystem=full namespace. No arbitrary args/wildcards.
printf '%s\n' 'magbot ALL=(root) NOPASSWD: /usr/bin/systemctl start mag-pages-reconcile.service' > /etc/sudoers.d/mag-pages
chmod 0440 /etc/sudoers.d/mag-pages
visudo -cf /etc/sudoers.d/mag-pages
# Save every existing Pages vhost for fail-closed recovery.
main_conf=/etc/nginx/conf.d/mag-pages.conf
custom_conf=/etc/nginx/conf.d/mag-pages-custom.conf
snapshot="$(mktemp -d /etc/nginx/.pages-install.XXXXXXXX)"
trap 'rm -rf "$snapshot"' EXIT
[[ ! -f "$main_conf" ]] || cp -a "$main_conf" "$snapshot/main"
[[ ! -f "$custom_conf" ]] || cp -a "$custom_conf" "$snapshot/custom"
restore_nginx() {
  if [[ -f "$snapshot/main" ]]; then cp -a "$snapshot/main" "$main_conf"; else rm -f "$main_conf"; fi
  if [[ -f "$snapshot/custom" ]]; then cp -a "$snapshot/custom" "$custom_conf"; else rm -f "$custom_conf"; fi
}
# Existing rollback keeps the pre-Pages snapshot.
[[ ! -f "$main_conf" || -e "$main_conf.pre-pages" ]] || cp -a "$main_conf" "$main_conf.pre-pages"
"$here/render-nginx.sh" > "$snapshot/rendered"
install -m 0644 "$snapshot/rendered" "$main_conf"
# Disabling public mode MUST also discard previously generated custom public vhosts.
if [[ -z "$PAGES_PUBLIC_BIND" ]]; then rm -f "$custom_conf"; fi
if ! nginx -t; then restore_nginx; echo 'nginx test failed: reverted before reload' >&2; exit 1; fi
if ! systemctl reload nginx.service; then
  restore_nginx
  nginx -t && systemctl reload nginx.service || true
  echo 'nginx reload failed: reverted Pages listeners' >&2; exit 1
fi
# Existing timer only; pages renewal via lego v5 RUN, not a separate service.
install -d -m 0755 /etc/systemd/system/mag-acme-renew.service.d
printf '[Service]\nExecStartPost=-/usr/local/lib/mag-pages/refresh-custom.sh run\nExecStartPost=-/usr/local/lib/mag-pages/pages-reconcile.sh\n' \
 > /etc/systemd/system/mag-acme-renew.service.d/30-pages.conf
# Native publisher enables public links only with an explicitly configured bind.
install -d -m 0755 /etc/systemd/system/mag-120.service.d
if [[ -n "$PAGES_PUBLIC_BIND" ]]; then
  printf '[Service]\nEnvironment=MAG_PAGES_PUBLIC_BIND=%s\n' "$PAGES_PUBLIC_BIND" \
   > /etc/systemd/system/mag-120.service.d/31-pages-public.conf
else
  rm -f /etc/systemd/system/mag-120.service.d/31-pages-public.conf
fi
install -m 0644 -o root -g root "$here/mag-pages-reconcile.service" /etc/systemd/system/mag-pages-reconcile.service
systemctl daemon-reload
echo "MAG Pages nginx ready: Tailnet $PAGES_TAILNET_BIND:443; public listener ${PAGES_PUBLIC_BIND:-disabled}."
echo 'Enable/restart mag-120 when idle to apply native Pages env; no mag-llm restart.'
