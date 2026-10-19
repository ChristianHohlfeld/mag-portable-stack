#!/usr/bin/env bash
# Tailnet-only nginx for *.bot.stardenburg.de. Never restarts mag-llm.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
if [[ $EUID != 0 ]]; then
  echo "bot site install skipped (not root)" >&2
  exit 0
fi
lego_dir="${MAG_BOTS_LEGO_DIR:-/etc/mag-acme/lego-bot}"
if [[ ! -d $lego_dir/certificates ]] || ! compgen -G "$lego_dir/certificates/*.crt" >/dev/null; then
  echo "bot site install skipped (wildcard cert not issued yet)"
  rm -f /var/lib/mag-bots/ready
  exit 0
fi
set +e
"$here/issue-wildcard.sh" renew
tls_rc=$?
set -e
if [[ $tls_rc -ne 0 && $tls_rc -ne 78 ]]; then
  echo "bot wildcard TLS install failed" >&2
  exit 1
fi
if [[ ! -s /etc/mag-bots/tls/current/cert.pem || ! -s /etc/mag-bots/tls/current/key.pem ]]; then
  echo "bot site install skipped (no current wildcard cert)"
  rm -f /var/lib/mag-bots/ready
  exit 0
fi
command -v nginx >/dev/null
repo=$(cd "$here/../.." && pwd)
pub="$repo/build/mag-bots-publish"
[[ -x $pub ]] || { echo "bot site publisher binary missing" >&2; exit 1; }
install -d -m 0755 /usr/local/lib/mag-bots /var/lib/mag-bots/www /etc/nginx/conf.d
install -d -m 0700 /var/lib/mag-bots/tokens
install -m 0755 "$here/site.sh" "$here/issue-wildcard.sh" "$pub" /usr/local/lib/mag-bots/
install -m 0644 "$here/mag-bots-publish.service" /etc/systemd/system/mag-bots-publish.service
conf=/etc/nginx/conf.d/mag-bots.conf
snapshot=$(mktemp)
had_conf=0
if [[ -f $conf ]]; then cp -a "$conf" "$snapshot"; had_conf=1; fi
trap 'rm -f "$snapshot"' EXIT
# Single-quoted replacement keeps the dots escaped in the nginx regex.
sed -e 's|@TAILNET_BIND@|100.123.114.16|g' \
    -e 's|@ZONE_REGEX@|bot\\.stardenburg\\.de|g' \
    "$here/mag-bots.nginx.in" > "$conf.next"
install -m 0644 "$conf.next" "$conf"
rm -f "$conf.next"
restore_conf() {
  if [[ $had_conf == 1 ]]; then cp -a "$snapshot" "$conf"; else rm -f "$conf"; fi
}
if ! nginx -t; then
  restore_conf
  echo "nginx test failed; bot vhost reverted" >&2
  exit 1
fi
if ! systemctl reload nginx.service; then
  restore_conf
  nginx -t && systemctl reload nginx.service || true
  echo "nginx reload failed; bot vhost reverted" >&2
  exit 1
fi
install -d -m 0755 /etc/systemd/system/mag-acme-renew.service.d
printf '%s\n' '[Service]' 'ExecStartPost=-/usr/local/lib/mag-bots/issue-wildcard.sh renew' \
  > /etc/systemd/system/mag-acme-renew.service.d/40-bots.conf
systemctl daemon-reload
# The release ops tree is already current. Open 8091 before the listener starts.
bash "$here/../firewall.sh"
if command -v nft >/dev/null && nft list table inet mag_offline >/dev/null 2>&1; then
  bash "$here/../magbot-netmode" offline \
    || echo "WARN: offline bot firewall not refreshed" >&2
fi
publish_up=0
if ip -4 -o addr show | grep -Fq ' 192.168.122.1/'; then
  if systemctl enable --now mag-bots-publish.service \
     && systemctl is-active --quiet mag-bots-publish.service; then
    publish_up=1
  else
    echo "WARN: bot publish listener not started" >&2
  fi
else
  echo "bot publish listener skipped (192.168.122.1 not local)"
fi
if [[ $publish_up == 1 ]]; then
  printf '1\n' > /var/lib/mag-bots/ready
  chmod 0644 /var/lib/mag-bots/ready
else
  rm -f /var/lib/mag-bots/ready
fi
/usr/local/lib/mag-bots/site.sh backfill || echo "WARN: bot site backfill incomplete" >&2
echo "MAG bot sites ready on the tailnet; mag-llm untouched"
