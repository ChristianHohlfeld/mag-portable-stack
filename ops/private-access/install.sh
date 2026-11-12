#!/usr/bin/env bash
# The room's ingress on a new host: the ACME renewal of mag.stardenburg.de
# (lego, acme-dns) and nginx with the Tailscale whois gate. Prerequisites:
# install-lego.sh and /etc/mag-acme/lego.env, the owners in
# /var/lib/magbot/room/access.json, a running room (mag-120) and tailscaled.
# Every release refreshes nginx itself (deploy-nginx.sh, called by deploy.sh);
# run this again only after changing the ACME scripts or units.
set -euo pipefail
[[ $EUID == 0 ]] || { echo "sudo required" >&2; exit 1; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
for cmd in nginx systemctl openssl curl jq; do command -v "$cmd" >/dev/null; done
systemctl is-active --quiet tailscaled.service
systemctl is-active --quiet mag-120.service
# A malformed or missing allowlist must never create owners by accident.
jq -e '
  (.owners | type=="array" and length>0) and
  (.owners | all(.[]; type=="string" and
     test("^[A-Za-z0-9._@+-]{1,160}$") and .!="LOGIN_AUS_WHOIS"))
' /var/lib/magbot/room/access.json >/dev/null ||
 { echo "Configure the real owner LoginName in access.json" >&2; exit 1; }
[[ -s /etc/mag-acme/lego.env && $(stat -c %u /etc/mag-acme/lego.env) == 0 ]] ||
 { echo "Missing or non-root /etc/mag-acme/lego.env (see install-lego.sh)" >&2; exit 1; }
install -d -m 0755 /usr/local/lib/mag-acme
install -m 0755 "$here/issue-cert.sh" "$here/deploy-hook.sh" /usr/local/lib/mag-acme/
install -m 0644 "$here/mag-acme-renew.service" "$here/mag-acme-renew.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mag-acme-renew.timer
# The first certificate, when there is none yet (DNS-01; the hook stages it
# for nginx without a reload).
[[ -s /etc/mag-nginx/tls/cert.pem ]] || /usr/local/lib/mag-acme/issue-cert.sh production
exec bash "$here/deploy-nginx.sh" standalone
