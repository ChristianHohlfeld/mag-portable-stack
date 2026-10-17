#!/usr/bin/env bash
# Publish one validated lego certificate to BOTH known nginx TLS locations.
# Existing live nginx uses /etc/nginx/mag-tls; cutover uses /etc/mag-nginx/tls.
# Reload only after all targets and the active nginx config are valid.
set -euo pipefail
[[ $EUID == 0 ]] || { echo "root required" >&2; exit 1; }
[[ $# == 0 || ( $# == 1 && $1 == --no-reload ) ]] || exit 2
. /etc/mag-acme/lego.env
[[ ${MAG_ACME_DOMAIN:-} == mag.stardenburg.de ]] || exit 1
src=/etc/mag-acme/lego/certificates
crt=$src/$MAG_ACME_DOMAIN.crt
key=$src/$MAG_ACME_DOMAIN.key
[[ -s $crt && -s $key ]] || { echo "ACME cert/key missing" >&2; exit 1; }
openssl x509 -in "$crt" -noout -checkend 86400 >/dev/null
openssl x509 -in "$crt" -noout -checkhost "$MAG_ACME_DOMAIN" | grep -F 'does match' >/dev/null
pubcrt=$(openssl x509 -in "$crt" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | cut -d' ' -f1)
pubkey=$(openssl pkey -in "$key" -pubout -outform DER | sha256sum | cut -d' ' -f1)
[[ $pubcrt == "$pubkey" ]] || { echo "Certificate/key mismatch" >&2; exit 1; }

# Serialize renewal, including validation/reload, to prevent partial pairs.
exec 9>/run/lock/mag-acme-deploy.lock
flock -x 9
destinations=(/etc/nginx/mag-tls /etc/mag-nginx/tls)
snapshot=$(mktemp -d /etc/mag-acme/.tls-snapshot.XXXXXXXX)
chmod 0700 "$snapshot"
restore=1
cleanup() {
  local rc=$? d i name
  trap - EXIT
  if (( restore )); then
    for i in "${!destinations[@]}"; do
      d=${destinations[$i]}
      for name in cert.pem key.pem; do
        if [[ -e $snapshot/$i/$name || -L $snapshot/$i/$name ]]; then
          cp -a -- "$snapshot/$i/$name" "$d/$name"
        else
          rm -f -- "$d/$name"
        fi
      done
    done
    echo "TLS deploy failed; previous certificate pairs restored" >&2
  fi
  rm -rf -- "$snapshot"
  return "$rc"
}
# Snapshot all targets BEFORE touching either one.
for i in "${!destinations[@]}"; do
  d=${destinations[$i]}
  mkdir -p "$snapshot/$i"
  for name in cert.pem key.pem; do
    if [[ -e $d/$name || -L $d/$name ]]; then
      cp -a -- "$d/$name" "$snapshot/$i/$name"
    fi
  done
done
trap cleanup EXIT
for d in "${destinations[@]}"; do
  install -d -m 0750 -o root -g www-data "$d"
  install -m 0644 -o root -g www-data "$crt" "$d/cert.pem.new.$$"
  install -m 0640 -o root -g www-data "$key" "$d/key.pem.new.$$"
  mv -f -- "$d/cert.pem.new.$$" "$d/cert.pem"
  mv -f -- "$d/key.pem.new.$$" "$d/key.pem"
done
if [[ $# == 0 ]]; then
  nginx -t
  systemctl reload nginx.service
fi
restore=0
