#!/usr/bin/env bash
# Fail-safe Tailnet nginx cutover. No model or Caddy changes.
# All host files changed by this installer are snapshotted and reversible.
set -euo pipefail
[[ $EUID == 0 ]] || { echo "sudo required" >&2; exit 1; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
site=/etc/nginx/sites-available/mag-room.conf
enabled=/etc/nginx/sites-enabled/mag-room.conf
backup=$site.pre-whois
state=/var/lib/mag-private-cutover
remote=/usr/local/lib/mag-private
host_files=(
 /usr/local/lib/mag-private/rollback.sh
 /usr/local/lib/mag-private/confirm.sh
 /usr/local/lib/mag-acme/issue-cert.sh
 /usr/local/lib/mag-acme/deploy-hook.sh
 /etc/systemd/system/mag-acme-renew.service
 /etc/systemd/system/mag-acme-renew.timer
)
for cmd in nginx systemctl systemd-run openssl curl jq stat cp flock; do command -v "$cmd" >/dev/null; done
systemctl is-active --quiet nginx.service
systemctl is-active --quiet tailscaled.service
systemctl is-active --quiet mag-120.service
# Keep one cutover/recovery writer at a time.
# /run/lock is system-shared; NEVER chmod /var/lock (often a symlink).
exec 9>/run/lock/mag-private-cutover.lock
flock -n 9 || { echo "Cutover already running" >&2; exit 1; }
native_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --max-time 5 -H 'X-Mag-Remote: 192.0.2.1:34567' \
  http://127.0.0.1:8766/_internal/tailscale/whois || true)
[[ $native_status == 403 ]] || {
  echo "Native C++ whois unavailable (HTTP $native_status); deploy mag-120 only" >&2; exit 1;
}
[[ -s $site && ! -L $site ]] || { echo "Existing nginx site required" >&2; exit 1; }
[[ ! -e $backup && ! -e $state/pending && ! -e $state/confirmed &&
   ! -e $state/enabled.before && ! -e $state/host.ready ]] ||
 { echo "Prior cutover artifacts exist; inspect/rollback before retry" >&2; exit 1; }
[[ ! -e $enabled || -L $enabled ]] || { echo "Unexpected enabled site" >&2; exit 1; }
# A malformed/missing allowlist must never silently create remote Owners.
jq -e '
  (.owners | type=="array" and length>0) and
  (.owners | all(.[]; type=="string" and
     test("^[A-Za-z0-9._@+-]{1,160}$") and .!="LOGIN_AUS_WHOIS"))
' /var/lib/magbot/room/access.json >/dev/null ||
 { echo "Configure real owner LoginName in access.json" >&2; exit 1; }
[[ -s /etc/mag-acme/lego.env && $(stat -c %u /etc/mag-acme/lego.env) == 0 ]] ||
 { echo "Missing or non-root lego.env" >&2; exit 1; }
. /etc/mag-acme/lego.env
[[ ${MAG_ACME_DOMAIN:-} == mag.stardenburg.de ]] || exit 1
# Previous production certificates and NEXT nginx certificates are distinct.
# This is a read-only prerequisite; stage BOTH via deploy-hook.sh --no-reload.
for name in cert.pem key.pem; do
  [[ -s /etc/mag-nginx/tls/$name ]] ||
   { echo "New TLS pair absent: run deploy-hook.sh --no-reload first" >&2; exit 1; }
done
cert=/etc/mag-nginx/tls/cert.pem
key=/etc/mag-nginx/tls/key.pem
openssl x509 -in "$cert" -checkhost "$MAG_ACME_DOMAIN" -noout | grep -F 'does match' >/dev/null
openssl x509 -in "$cert" -checkend 86400 -noout >/dev/null
pub1=$(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | cut -d' ' -f1)
pub2=$(openssl pkey -in "$key" -pubout -outform DER | sha256sum | cut -d' ' -f1)
[[ $pub1 == "$pub2" ]] || { echo "TLS key/cert mismatch" >&2; exit 1; }
nginx -t # verify the OLD active configuration before touching anything

install -d -m 0700 "$state" "$state/host"
if [[ -L $enabled ]]; then readlink -- "$enabled" > "$state/enabled.before"
else printf '%s\n' ABSENT > "$state/enabled.before"; fi
cp -a -- "$site" "$backup" # preserve original owner, mode and timestamps
# Once the site backup is complete, ALL failures perform the same rollback.
trap 'rc=$?; if (( rc != 0 )); then "$here/rollback.sh" || echo "Manual recovery required" >&2; fi' EXIT
for i in "${!host_files[@]}"; do
  f=${host_files[$i]}
  if [[ -e $f || -L $f ]]; then
    cp -a -- "$f" "$state/host/$i"
  else
    touch "$state/host/$i.absent"
  fi
done
touch "$state/host.ready"

# Preserve mode rather than forcing 0600. No ACME or unit replacement until
# the entire active nginx configuration including mag-speech.inc passes.
mode=$(stat -c %a "$backup")
install -m "$mode" "$here/mag-room.nginx.conf" "$site.new"
mv -f -- "$site.new" "$site"
ln -sfn ../sites-available/mag-room.conf "$enabled"
nginx -t
nginx -T > "$state/nginx.dump" 2>&1
grep -F 'auth_request /_mag_whois;' "$state/nginx.dump" >/dev/null
grep -F 'location ~ "^/static/speech-[0-9a-f]{12}/[A-Za-z0-9_.-]+$"' "$state/nginx.dump" >/dev/null
rm -f "$state/nginx.dump"

# Every overwritten host file is snapshotted, and rollback restores each one.
install -d -m 0755 "$remote" /usr/local/lib/mag-acme
install -m 0755 "$here/rollback.sh" "$here/confirm.sh" "$remote/"
install -m 0755 "$here/issue-cert.sh" "$here/deploy-hook.sh" /usr/local/lib/mag-acme/
install -m 0644 "$here/mag-acme-renew.service" "$here/mag-acme-renew.timer" /etc/systemd/system/
systemctl daemon-reload
nginx -t

touch "$state/pending"
# ARMED before the first nginx reload; no mag-llm or mag-120 restart.
systemctl reset-failed mag-private-rollback.service mag-private-rollback.timer 2>/dev/null || true
systemd-run --quiet --unit=mag-private-rollback --on-active=180s "$remote/rollback.sh"
systemctl is-active --quiet mag-private-rollback.timer
systemctl reload nginx.service
trap - EXIT
echo "Cutover staged; AUTO-ROLLBACK in 180 seconds unless Owner verified."
echo "Second Tailscale device: https://mag.stardenburg.de/api/state"
echo "Only after exact Owner login + role: sudo $remote/confirm.sh --owner-verified"
