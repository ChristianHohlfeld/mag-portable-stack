#!/usr/bin/env bash
# Renew the existing *.bot.stardenburg.de certificate. The acme-dns account
# was registered once by hand; this script must not create another one and
# must not request the apex name (the live certificate is wildcard-only).
set -euo pipefail
zone="${MAG_BOTS_ZONE:-bot.stardenburg.de}"
[[ $zone =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && $zone == *.* && $zone != *..* ]] || {
  echo "Invalid bot zone" >&2; exit 2; }
mode="${1:-renew}"
[[ $mode == run || $mode == renew ]] || { echo "Usage: issue-wildcard.sh renew" >&2; exit 2; }
if [[ ${MAG_BOTS_TESTING:-} != 1 && $EUID != 0 ]]; then
  echo "Root required" >&2; exit 1
fi

lego_dir="${MAG_BOTS_LEGO_DIR:-/etc/mag-acme/lego-bot}"
tls="${MAG_BOTS_TLS_DIR:-/etc/mag-bots/tls}"
storage="${MAG_BOTS_ACME_STORAGE:-/etc/mag-acme/acme-dns.json}"
lego="${MAG_BOTS_LEGO:-/usr/local/bin/lego}"
dig_bin="${MAG_BOTS_DIG:-dig}"
nginx_bin="${MAG_BOTS_NGINX:-nginx}"
acme_env="${MAG_BOTS_ACME_ENV:-/etc/mag-acme/lego.env}"

san_of() { openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null || true; }

wildcard_cert() {
  local candidate san
  [[ -d $lego_dir/certificates ]] || return 1
  for candidate in "$lego_dir"/certificates/*.crt; do
    [[ -s $candidate && -s ${candidate%.crt}.key ]] || continue
    san=$(san_of "$candidate")
    if grep -Fq "DNS:*.$zone" <<<"$san"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

install_cert() {
  local crt=$1 key dest hash prior pub_cert pub_key
  key="${crt%.crt}.key"
  [[ -s $key ]] || { echo "Bot wildcard key missing" >&2; exit 1; }
  hash=$(openssl x509 -in "$crt" -outform DER | sha256sum | cut -c1-16)
  dest="$tls/releases/$hash"
  install -d -m 0755 "$tls/releases"
  install -d -m 0750 "$dest"
  install -m 0644 "$crt" "$dest/cert.pem"
  install -m 0640 "$key" "$dest/key.pem"
  if [[ $EUID == 0 ]] && getent group www-data >/dev/null; then
    chgrp www-data "$dest" "$dest/cert.pem" "$dest/key.pem"
  fi
  openssl x509 -in "$dest/cert.pem" -noout -checkend 86400 >/dev/null
  pub_cert=$(openssl x509 -in "$dest/cert.pem" -pubkey -noout | openssl sha256)
  pub_key=$(openssl pkey -in "$dest/key.pem" -pubout | openssl sha256)
  [[ $pub_cert == "$pub_key" ]] || { echo "Mismatched bot wildcard key" >&2; exit 1; }
  if [[ $(readlink -f "$tls/current" 2>/dev/null || true) == "$dest" ]]; then
    return 0
  fi
  prior=$(readlink "$tls/current" 2>/dev/null || true)
  ln -sfn "$dest" "$tls/.next"
  mv -Tf "$tls/.next" "$tls/current"
  if [[ -e /etc/nginx/conf.d/mag-bots.conf && ${MAG_BOTS_TESTING:-} != 1 ]]; then
    if ! "$nginx_bin" -t || ! systemctl reload nginx.service; then
      if [[ -n $prior ]]; then
        ln -sfn "$prior" "$tls/.next"
        mv -Tf "$tls/.next" "$tls/current"
      else
        rm -f "$tls/current"
      fi
      echo "nginx rejected bot TLS; prior certificate restored" >&2
      exit 1
    fi
  fi
}

account_target() {
  [[ -s $storage ]] || return 0
  python3 - "$storage" "$zone" <<'PY'
import json, sys
path, zone = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
if not isinstance(data, dict):
    sys.exit(2)
found = []
for key in ("*." + zone, zone):
    row = data.get(key)
    if isinstance(row, dict) and isinstance(row.get("fulldomain"), str):
        found.append(row["fulldomain"])
found = list(dict.fromkeys(found))
if len(found) > 1:
    sys.exit(3)
print(found[0] if found else "")
PY
}

crt=$(wildcard_cert || true)
if [[ -n $crt ]] && openssl x509 -in "$crt" -noout -checkend 2592000 >/dev/null 2>&1; then
  install_cert "$crt"
  echo "MAG bot wildcard TLS current; renewal not due; mag-llm untouched"
  exit 0
fi
if [[ -n $crt ]] && openssl x509 -in "$crt" -noout -checkend 86400 >/dev/null 2>&1; then
  install_cert "$crt"
fi

target=$(account_target) || { echo "Invalid acme-dns storage; not rewritten" >&2; exit 1; }
if [[ -z $target ]]; then
  if [[ -z $crt ]]; then
    echo "Bot wildcard cert missing and no existing acme-dns account; skip registration" >&2
    exit 0
  fi
  echo "No existing acme-dns account for *.${zone}; refusing a second registration" >&2
  exit 78
fi
[[ $target =~ ^[A-Za-z0-9-]+\.auth\.acme-dns\.io$ ]] || { echo "Invalid acme-dns target" >&2; exit 1; }
fqdn="_acme-challenge.${zone}"
actual=$("$dig_bin" +short CNAME "$fqdn" @1.1.1.1 | sed 's/[.]$//' | tr '[:upper:]' '[:lower:]')
if [[ $actual != "$target" ]]; then
  echo "DomainFactory DNS-01 CNAME required (do not create a new account):" >&2
  echo "  CNAME  _acme-challenge.bot  ${target}." >&2
  echo "observed: ${actual:-<none>}" >&2
  exit 78
fi
[[ -x $lego ]] || { echo "lego missing; cannot renew bot wildcard" >&2; exit 1; }
if [[ -r $acme_env ]]; then
  # shellcheck disable=SC1090
  . "$acme_env"
fi
export ACME_DNS_API_BASE="${ACME_DNS_API_BASE:-https://auth.acme-dns.io}"
export ACME_DNS_STORAGE_PATH="$storage"
# Same command as the one-time issuance: wildcard only, existing account, no --email.
"$lego" run --accept-tos --server https://acme-v02.api.letsencrypt.org/directory \
  --path "$lego_dir" --dns acmedns --dns.resolvers 8.8.8.8:53,1.1.1.1:53 \
  -d "*.${zone}"
crt=$(wildcard_cert || true)
[[ -n $crt ]] || { echo "lego returned no bot wildcard cert" >&2; exit 1; }
install_cert "$crt"
echo "MAG bot wildcard TLS renewed; mag-llm untouched"
