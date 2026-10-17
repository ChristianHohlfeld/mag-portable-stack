#!/usr/bin/env bash
# Safe nginx tailnet cutover. Reverts in 180s unless explicitly confirmed.
# This script NEVER touches mag-llm, room service, or the Caddy install.
set -euo pipefail
[[ $EUID == 0 ]] || { echo "sudo required" >&2; exit 1; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
site=/etc/nginx/sites-available/mag-room.conf
enabled=/etc/nginx/sites-enabled/mag-room.conf
backup=$site.pre-whois
state=/var/lib/mag-private-cutover
remote=/usr/local/lib/mag-private
for cmd in nginx systemctl systemd-run openssl curl; do command -v "$cmd" >/dev/null; done
systemctl is-active --quiet nginx.service
systemctl is-active --quiet tailscaled.service
systemctl is-active --quiet mag-120.service
# Refuse the cutover until the DEPLOYED C++ magroom has the new auth route.
# A nonexistent whois identity is a definitive 403; an older binary returns 404.
# 192.0.2.1 is documentation-only, not a real Tailnet client.
native_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --max-time 5 -H 'X-Mag-Remote: 192.0.2.1:34567' \
  http://127.0.0.1:8766/_internal/tailscale/whois || true)
[[ $native_status == 403 ]] || {
  echo "Native magroom whois not ready (HTTP $native_status); deploy C++ magroom first, NOT mag-llm" >&2
  exit 1
}
[[ -s $site && ! -L $site ]] || { echo "Existing nginx site required" >&2; exit 1; }
[[ ! -e $backup && ! -e $state/pending && ! -e $state/confirmed ]] ||
 { echo "Prior cutover/backup exists: inspect or rollback before retry" >&2; exit 1; }
[[ ! -e $enabled || -L $enabled ]] || { echo "Unexpected enabled site" >&2; exit 1; }
# Ensure the app's owner allowlist is in place BEFORE any ingress changes.
# A loopback curl looks like Owner even without this file, but Tailnet must not.
python3 - <<'PY'
import json, pathlib, re, sys
p=pathlib.Path("/var/lib/magbot/room/access.json")
try:
    access=json.loads(p.read_text())
    owners=access.get("owners",[])
    assert isinstance(owners,list) and len(owners)>0
    assert all(isinstance(o,str) and re.fullmatch(r"[A-Za-z0-9._@+-]{1,160}",o) and
               o.upper()!="LOGIN_AUS_WHOIS" for o in owners)
except Exception as e:
    sys.exit("Access owner allowlist missing/invalid; configure actual tailscale whois LoginName before cutover")
PY
[[ -s /etc/mag-acme/lego.env ]] || { echo "Missing existing lego.env" >&2; exit 1; }
[[ $(stat -c %u /etc/mag-acme/lego.env) == 0 ]] || { echo "lego.env must be root owned" >&2; exit 1; }
. /etc/mag-acme/lego.env
[[ $MAG_ACME_DOMAIN == mag.stardenburg.de ]] || { echo "Unexpected domain" >&2; exit 1; }
cert=/etc/mag-nginx/tls/cert.pem
key=/etc/mag-nginx/tls/key.pem
[[ -s $cert && -s $key ]] || { echo "Prepare certificates with deploy-hook.sh --no-reload first" >&2; exit 1; }
openssl x509 -in "$cert" -checkhost mag.stardenburg.de -noout | grep -F 'does match' >/dev/null
openssl x509 -in "$cert" -checkend 86400 -noout >/dev/null
pub1=$(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | cut -d' ' -f1)
pub2=$(openssl pkey -in "$key" -pubout -outform DER | sha256sum | cut -d' ' -f1)
[[ $pub1 == "$pub2" ]] || { echo "TLS key/cert mismatch" >&2; exit 1; }

install -d -m 0700 "$state"
if [[ -L $enabled ]]; then readlink -- "$enabled" > "$state/enabled.before"
else echo ABSENT > "$state/enabled.before"; fi
cp -a -- "$site" "$backup"
chmod 0600 "$backup"
install -d -m 0755 "$remote" /usr/local/lib/mag-acme
install -m 0755 "$here/rollback.sh" "$here/confirm.sh" "$remote/"
install -m 0755 "$here/issue-cert.sh" "$here/deploy-hook.sh" /usr/local/lib/mag-acme/
install -m 0644 "$here/mag-acme-renew.service" "$here/mag-acme-renew.timer" /etc/systemd/system/
systemctl daemon-reload
# All failures after modifying the site restore its previous configuration.
trap 'rc=$?; if (( rc != 0 )); then "$remote/rollback.sh" || true; fi' EXIT
install -m 0644 "$here/mag-room.nginx.conf" "$site.new"
mv -f -- "$site.new" "$site"
ln -sfn ../sites-available/mag-room.conf "$enabled"
nginx -t
nginx -T 2>&1 | grep -F 'auth_request /_mag_whois;' >/dev/null
touch "$state/pending"
# ARM THE AUTOMATIC ROLLBACK BEFORE RELOADING NGINX.
systemctl reset-failed mag-private-rollback.service mag-private-rollback.timer 2>/dev/null || true
systemd-run --quiet --unit=mag-private-rollback --on-active=180s "$remote/rollback.sh"
systemctl is-active --quiet mag-private-rollback.timer
systemctl reload nginx.service
trap - EXIT
echo "Cutover staged; WILL AUTO-ROLL BACK in 180 seconds."
echo "From a second Tailscale device check: https://mag.stardenburg.de/api/state"
echo "Owner + exact login verified? sudo $remote/confirm.sh --owner-verified"
echo "Do NOT remove Caddy until confirmation. mag-llm untouched."
