#!/usr/bin/env bash
# Tailnet-only nginx for *.bot.stardenburg.de. Never restarts mag-llm.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
test_mode=${MAG_BOTS_TESTING:-0}
if [[ $test_mode != 1 && $EUID != 0 ]]; then
  echo "bot site install skipped (not root)" >&2
  exit 0
fi
lego_dir="${MAG_BOTS_LEGO_DIR:-/etc/mag-acme/lego-bot}"
tls_dir="${MAG_BOTS_TLS_DIR:-/etc/mag-bots/tls}"
lib_dir="${MAG_BOTS_LIB_DIR:-/usr/local/lib/mag-bots}"
www_root="${MAG_BOTS_ROOT:-/var/lib/mag-bots}"
nginx_confd="${MAG_BOTS_NGINX_CONFD:-/etc/nginx/conf.d}"
systemd_dir="${MAG_BOTS_SYSTEMD:-/etc/systemd/system}"
ready_file="${MAG_BOTS_READY:-/var/lib/mag-bots/ready}"
if [[ ! -d $lego_dir/certificates ]] || ! compgen -G "$lego_dir/certificates/*.crt" >/dev/null; then
  echo "bot site install skipped (wildcard cert not issued yet)"
  rm -f "$ready_file"
  exit 0
fi
set +e
bash "$here/issue-wildcard.sh" renew
tls_rc=$?
set -e
# A due renew (lego missing, ACME error) used to abort the whole install.
# Deploy then printed WARN: bot site install incomplete even though the
# current wildcard and vhost were already valid.
if [[ $tls_rc -ne 0 && $tls_rc -ne 78 ]]; then
  if [[ -s $tls_dir/current/cert.pem && -s $tls_dir/current/key.pem ]]; then
    echo "WARN: bot wildcard renew failed (rc=${tls_rc}); keeping current cert" >&2
  else
    echo "bot wildcard TLS install failed" >&2
    exit 1
  fi
fi
if [[ ! -s $tls_dir/current/cert.pem || ! -s $tls_dir/current/key.pem ]]; then
  echo "bot site install skipped (no current wildcard cert)"
  rm -f "$ready_file"
  exit 0
fi
command -v nginx >/dev/null
repo=$(cd "$here/../.." && pwd)
pub=
for candidate in \
  ${MAG_BOTS_PUBLISH:+"$MAG_BOTS_PUBLISH"} \
  "$repo/build/mag-bots-publish" \
  /opt/mag-stack/current/bin/mag-bots-publish \
  "$lib_dir/mag-bots-publish"
do
  if [[ -n $candidate && -x $candidate ]]; then
    pub=$candidate
    break
  fi
done
[[ -n $pub ]] || { echo "bot site publisher binary missing" >&2; exit 1; }
install -d -m 0755 "$lib_dir" "$www_root/www" "$nginx_confd"
install -d -m 0700 "$www_root/tokens"
install -m 0755 "$here/site.sh" "$here/issue-wildcard.sh" "$lib_dir/"
installed_pub="$lib_dir/mag-bots-publish"
# GNU install errors "are the same file" when the fallback is already
# $lib_dir/mag-bots-publish. That aborted install after the other fixes.
if [[ ! -e $installed_pub || ! $pub -ef $installed_pub ]]; then
  install -m 0755 "$pub" "$installed_pub"
fi
install -m 0644 "$here/mag-bots-publish.service" "$systemd_dir/mag-bots-publish.service"
conf=$nginx_confd/mag-bots.conf
snapshot=$(mktemp)
had_conf=0
if [[ -f $conf ]]; then cp -a "$conf" "$snapshot"; had_conf=1; fi
trap 'rm -f "$snapshot"' EXIT
# Single-quoted replacement keeps the dots escaped in the nginx regex.
# The vhost listens on 127.0.0.1:4443. Do not substitute the Tailscale IP.
# Only listen lines: mag-bots.nginx.in documents 100.123.114.16:443 in a
# comment. Matching that aborted every live deploy with
# WARN: bot site install incomplete (deploy itself succeeded).
if grep -E '^[[:space:]]*listen[[:space:]]+(100\.123\.114\.16:443|@TAILNET_BIND@)' \
    "$here/mag-bots.nginx.in" >/dev/null; then
  echo "bot vhost must not bind the Tailscale address" >&2
  exit 1
fi
sed -e 's|@ZONE_REGEX@|bot\\.stardenburg\\.de|g' \
    "$here/mag-bots.nginx.in" > "$conf.next"
install -m 0644 "$conf.next" "$conf"
rm -f "$conf.next"
restore_conf() {
  if [[ $had_conf == 1 ]]; then cp -a "$snapshot" "$conf"; else rm -f "$conf"; fi
}
if [[ $test_mode != 1 ]]; then
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
fi
install -d -m 0755 "$systemd_dir/mag-acme-renew.service.d"
printf '%s\n' '[Service]' "ExecStartPost=-$lib_dir/issue-wildcard.sh renew" \
  > "$systemd_dir/mag-acme-renew.service.d/40-bots.conf"
if [[ $test_mode != 1 ]]; then
  systemctl daemon-reload
  # The release ops tree is already current. Open 8091 before the listener starts.
  bash "$here/../firewall.sh" || echo "WARN: bot firewall refresh failed" >&2
  if command -v nft >/dev/null && nft list table inet mag_offline >/dev/null 2>&1; then
    bash "$here/../magbot-netmode" offline \
      || echo "WARN: offline bot firewall not refreshed" >&2
  fi
fi
publish_up=0
if [[ $test_mode == 1 ]]; then
  echo "bot publish listener skipped (test)"
elif ip -4 -o addr show | grep -Fq ' 192.168.122.1/'; then
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
  printf '1\n' > "$ready_file"
  chmod 0644 "$ready_file"
else
  rm -f "$ready_file"
fi
"$lib_dir/site.sh" backfill || echo "WARN: bot site backfill incomplete" >&2
echo "MAG bot sites ready on the tailnet; mag-llm untouched"
