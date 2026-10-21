#!/usr/bin/env bash
# Read-only readiness check on MAG. A second Tailnet client must also probe.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run: sudo bash ops/pages/verify.sh' >&2; exit 1; }
. /etc/mag-pages/pages.env
[[ "$PAGES_ZONE" == pages.stardenburg.de &&
   "${PAGES_TAILNET_BIND:-}" == 100.123.114.16 &&
   -z "${PAGES_PUBLIC_BIND:-}" && -z "${PAGES_PUBLIC_DNS:-}" ]] ||
  { echo 'Unsafe Pages bind; expected Tailnet-only' >&2; exit 1; }
fqdn=_acme-challenge.pages.stardenburg.de
expected=1e915301-9bda-4d2d-b261-6c20c635aca4.auth.acme-dns.io
actual="$(dig +short CNAME "$fqdn" @1.1.1.1 | sed 's/[.]$//' | tr '[:upper:]' '[:lower:]')"
[[ "$actual" == "$expected" ]] ||
  { echo "DNS delegation mismatch: expected $expected; got ${actual:-<missing>}" >&2; exit 1; }
actual="$(dig +short A pages.stardenburg.de @1.1.1.1)"
[[ "$actual" == 100.123.114.16 ]] || { echo 'Apex A record mismatch' >&2; exit 1; }
actual="$(dig +short A probe--mag.pages.stardenburg.de @1.1.1.1)"
[[ "$actual" == 100.123.114.16 ]] || { echo 'Wildcard A record mismatch' >&2; exit 1; }
cert=/etc/mag-pages/tls/current/cert.pem
key=/etc/mag-pages/tls/current/key.pem
[[ -s "$cert" && -s "$key" ]] || { echo 'Nginx-referenced TLS cert/key missing' >&2; exit 1; }
openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null
san="$(openssl x509 -in "$cert" -noout -ext subjectAltName)"
grep -Fq 'DNS:*.pages.stardenburg.de' <<<"$san"
grep -Fq 'DNS:pages.stardenburg.de' <<<"$san"
cert_public_key_hash="$(openssl x509 -in "$cert" -pubkey -noout | openssl sha256)"
private_key_public_hash="$(openssl pkey -in "$key" -pubout | openssl sha256)"
[[ "$cert_public_key_hash" == "$private_key_public_hash" ]] ||
  { echo 'TLS key/certificate mismatch' >&2; exit 1; }
conf=/etc/nginx/conf.d/mag-pages.conf
grep -Fq 'server_name pages.stardenburg.de;' "$conf"
[[ "$(grep -Fc 'listen 100.123.114.16:443 ssl http2;' "$conf")" -eq 2 ]] ||
  { echo 'Expected exactly two Tailnet-only Pages TLS listeners' >&2; exit 1; }
! grep -Eq 'listen [^;]*:80;' "$conf" ||
  { echo 'Pages HTTP public listener unexpectedly configured' >&2; exit 1; }
[[ ! -s /etc/nginx/conf.d/mag-pages-custom.conf ]] ||
  { echo 'Custom public vhosts must be absent in Tailnet-only mode' >&2; exit 1; }
[[ -f /etc/systemd/system/mag-acme-renew.service.d/30-pages.conf ]]
grep -Fq 'refresh-custom.sh run' /etc/systemd/system/mag-acme-renew.service.d/30-pages.conf
systemctl is-enabled --quiet mag-acme-renew.timer
systemctl is-active --quiet mag-120.service
systemctl is-active --quiet nginx.service
systemctl cat mag-120.service | grep -F 'MAG_PAGES_ENABLE=1' >/dev/null
nginx -t
response="$(curl -fsS --connect-timeout 5 --max-time 15 --resolve \
 'pages.stardenburg.de:443:100.123.114.16' \
 'https://pages.stardenburg.de/healthz')"
[[ "$response" == ok ]] || { echo 'Pages HTTPS /healthz did not return ok' >&2; exit 1; }
curl -fsS --connect-timeout 5 --max-time 15 --resolve \
 'pages.stardenburg.de:443:100.123.114.16' \
 'https://pages.stardenburg.de/' | grep -F '<h1>MAG Pages</h1>' >/dev/null
echo 'PASS: A/CNAME delegation, wildcard+apex TLS SAN, nginx private 443, HTTPS portal, MAG ACME renew and room enabled.'
echo 'OTHER DEVICE: curl -fsS https://pages.stardenburg.de/healthz from a second Tailnet node.'
echo 'PRIVATE REPOS: verify deployed read:repository PAT can see a bot-created private repo.'
