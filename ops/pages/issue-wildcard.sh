#!/usr/bin/env bash
# lego v5.5.2, DNS-01 through existing acme-dns; no inbound public port needed.
set -euo pipefail
[[ $EUID == 0 || "${MAG_PAGES_TESTING:-}" == 1 ]] || { echo 'Root required' >&2; exit 1; }
pages_config="${MAG_PAGES_CONFIG:-/etc/mag-pages/pages.env}"
acme_config="${MAG_PAGES_ACME_ENV:-/etc/mag-acme/lego.env}"
. "$pages_config"
. "$acme_config"
export ACME_DNS_API_BASE ACME_DNS_STORAGE_PATH
zone="$PAGES_ZONE"
[[ "$zone" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$zone" == *.* && "$zone" != *".."* ]] || { echo 'Invalid PAGES_ZONE' >&2; exit 2; }
[[ "$PAGES_ACME_EMAIL" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+$ && "$PAGES_ACME_EMAIL" != operator@example.com ]] || { echo 'Set real PAGES_ACME_EMAIL' >&2; exit 2; }
mode="${1:-run}"
[[ "$mode" == run || "$mode" == renew ]] || { echo 'Usage: issue-wildcard.sh run (renew is an alias)' >&2; exit 2; }
# v5 has no renew command; BOTH first issuance and periodic renewal use "lego run".
lego="${MAG_PAGES_LEGO:-/usr/local/bin/lego}"
dig_bin="${MAG_PAGES_DIG:-dig}"
jq_bin="${MAG_PAGES_JQ:-jq}"
base="${MAG_PAGES_LEGO_DIR:-/etc/mag-acme/lego-pages}"
tls="${MAG_PAGES_TLS_DIR:-/etc/mag-pages/tls}"
"$jq_bin" --version >/dev/null
mkdir -p "$base" "$tls/releases"
chmod 0700 "$base"
fqdn="_acme-challenge.$zone"
target_from_storage() {
  [[ -s "$ACME_DNS_STORAGE_PATH" ]] || return 0
  "$jq_bin" -r --arg d "$zone" \
    '[ .[$d].fulldomain, .[("*." + $d)].fulldomain ] | map(select(type == "string" and length > 0)) | unique | .[]' \
    "$ACME_DNS_STORAGE_PATH"
}
# One CNAME serves both wildcard and apex. Lego/acme-dns may initially
# register only the first domain, keyed separately in JSON. Reuse that SAME
# account for both identifiers so both DNS-01 TXT values share the delegation.
# Preserve every unrelated registration, including mag.stardenburg.de.
share_acmedns_account() {
  [[ -s "$ACME_DNS_STORAGE_PATH" ]] || return 0
  # Normal renewals must not rewrite the shared MAG ACME credential store.
  if "$jq_bin" -e --arg d "$zone" '.[$d] != null and .[("*." + $d)] != null'       "$ACME_DNS_STORAGE_PATH" >/dev/null 2>&1; then
    return 0
  fi
  local tmp
  tmp="$(mktemp "$(dirname "$ACME_DNS_STORAGE_PATH")/.mag-pages-account.XXXXXXXX")"
  if ! "$jq_bin" --arg d "$zone" '
    if .[$d] != null and .[("*." + $d)] == null then
       .[("*." + $d)] = .[$d]
    elif .[("*." + $d)] != null and .[$d] == null then
       .[$d] = .[("*." + $d)]
    else . end' "$ACME_DNS_STORAGE_PATH" >"$tmp"; then
      rm -f "$tmp"
      echo 'Invalid acme-dns storage; accounts preserved' >&2
      return 1
  fi
  chmod --reference="$ACME_DNS_STORAGE_PATH" "$tmp"
  chown --reference="$ACME_DNS_STORAGE_PATH" "$tmp"
  mv -f "$tmp" "$ACME_DNS_STORAGE_PATH"
}
show_required_dns() {
  echo "DomainFactory DNS-01: set this exact one-time CNAME before running Pages TLS:"
  echo "  CNAME  _acme-challenge.pages  $1."
  echo "  Full:  $fqdn. CNAME $1."
  echo "Verify with: $dig_bin +short CNAME $fqdn @1.1.1.1"
}
# Per-domain registration persists in the existing ACME_DNS_STORAGE_PATH.
# If no account exists yet, lego registers and normally exits with
# ErrCNAMERequired. We read only the fulldomain, never passwords/credentials.
share_acmedns_account
targets="$(target_from_storage)" || { echo 'Invalid ACME-DNS storage JSON' >&2; exit 1; }
if [[ -z "$targets" ]]; then
  log="$(mktemp)"
  trap 'rm -f "$log"' EXIT
  set +e
  "$lego" run --accept-tos --email "$PAGES_ACME_EMAIL" --path "$base" \
    --dns acmedns --dns.resolvers 8.8.8.8:53,1.1.1.1:53 \
    -d "*.$zone" -d "$zone" >"$log" 2>&1
  rc=$?
  set -e
  share_acmedns_account
  targets="$(target_from_storage)" || { echo 'Invalid ACME-DNS storage after lego registration' >&2; exit 1; }
  if [[ -z "$targets" && $rc -ne 0 ]]; then
    echo 'lego acme-dns registration failed before returning a CNAME target (check API and account storage).' >&2
    exit 1
  fi
fi
[[ -n "$targets" ]] || { echo 'No acme-dns account target registered; check storage.' >&2; exit 1; }
if [[ "$(printf '%s\n' "$targets" | wc -l)" -ne 1 ]]; then
  echo "Multiple ACME-DNS targets for $fqdn; cannot configure a single DNS CNAME. Fix account registration." >&2
  exit 1
fi
target="$targets"
[[ "$target" =~ ^[a-zA-Z0-9-]+\.auth\.acme-dns\.io$ ]] || { echo 'Invalid acme-dns target in storage' >&2; exit 1; }
actual="$("$dig_bin" +short CNAME "$fqdn" @1.1.1.1 | sed 's/[.]$//' | tr '[:upper:]' '[:lower:]')"
if [[ "$actual" != "$target" ]]; then
  show_required_dns "$target"
  echo "DNS not delegated/propagated yet (observed: ${actual:-<none>}); skipping issuance." >&2
  exit 78
fi
# Only call lego after DNS propagation; do not retry failed issuance in a tight loop.
"$lego" run --accept-tos --email "$PAGES_ACME_EMAIL" --path "$base" \
  --dns acmedns --dns.resolvers 8.8.8.8:53,1.1.1.1:53 \
  -d "*.$zone" -d "$zone"
crt=
for cert in "$base"/certificates/*.crt; do
  [[ -f "$cert" ]] || continue
  san="$(openssl x509 -in "$cert" -noout -ext subjectAltName)"
  if grep -Fq "DNS:*.$zone" <<<"$san" && grep -Fq "DNS:$zone" <<<"$san"; then crt="$cert"; break; fi
done
[[ -n "$crt" ]] || { echo 'lego returned no cert with both wildcard and apex SAN' >&2; exit 1; }
key="${crt%.crt}.key"
[[ -s "$key" ]] || { echo 'lego key missing' >&2; exit 1; }
hash="$(openssl x509 -in "$crt" -outform DER | sha256sum | cut -c1-16)"
dest="$tls/releases/$hash"
install -d -m 0750 -o root -g www-data "$dest"
install -m 0644 -o root -g www-data "$crt" "$dest/cert.pem"
install -m 0640 -o root -g www-data "$key" "$dest/key.pem"
openssl x509 -in "$dest/cert.pem" -noout -checkend 86400 >/dev/null
[[ "$(openssl x509 -in "$dest/cert.pem" -pubkey -noout | openssl sha256)" == \
   "$(openssl pkey -in "$dest/key.pem" -pubout | openssl sha256)" ]] || { echo 'Mismatched key' >&2; exit 1; }
prior="$(readlink "$tls/current" 2>/dev/null || true)"
ln -sfn "$dest" "$tls/.next"
mv -Tf "$tls/.next" "$tls/current"
if [[ -e /etc/nginx/conf.d/mag-pages.conf && "${MAG_PAGES_TESTING:-}" != 1 ]]; then
 if ! nginx -t || ! systemctl reload nginx.service; then
   if [[ -n "$prior" ]]; then
     ln -sfn "$prior" "$tls/.next"; mv -Tf "$tls/.next" "$tls/current"
   else rm -f "$tls/current"; fi
   echo 'nginx rejected new Pages TLS; prior certificate restored' >&2
   exit 1
 fi
fi
echo 'MAG Pages wildcard+apex TLS ready; mag-llm untouched'
