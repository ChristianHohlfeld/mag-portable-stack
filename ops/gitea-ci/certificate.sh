#!/usr/bin/env bash
set +x
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
. /etc/mag-ci/public.env
[[ ${MAG_GIT_HOST:-git.mag.stardenburg.de} == git.mag.stardenburg.de ]] || exit 1
: "${MAG_GIT_ACME_EMAIL:?ACME contact missing}"
# Public DNS and port 80 must reach this host. No DomainFactory API key needed.
/usr/local/bin/lego run --accept-tos --email "$MAG_GIT_ACME_EMAIL" --path /etc/mag-ci/lego \
  --server https://acme-v02.api.letsencrypt.org/directory --http --http.webroot /var/lib/mag-ci/acme \
  -d git.mag.stardenburg.de >/dev/null
cert=/etc/mag-ci/lego/certificates/git.mag.stardenburg.de.crt
key=/etc/mag-ci/lego/certificates/git.mag.stardenburg.de.key
openssl x509 -in "$cert" -checkhost git.mag.stardenburg.de -noout >/dev/null
openssl x509 -in "$cert" -checkend 86400 -noout >/dev/null
[[ $(openssl x509 -in "$cert" -pubkey -noout | openssl sha256) == "$(openssl pkey -in "$key" -pubout | openssl sha256)" ]] || exit 1
hash=$(openssl x509 -in "$cert" -outform DER | sha256sum | cut -c1-16)
dest=/etc/mag-ci/tls/releases/$hash
install -d -m 0700 "$dest"
install -m 0644 "$cert" "$dest/cert.pem"
install -m 0600 "$key" "$dest/key.pem"
prior=$(readlink /etc/mag-ci/tls/current || true)
ln -sfn "$dest" /etc/mag-ci/tls/.next
mv -Tf /etc/mag-ci/tls/.next /etc/mag-ci/tls/current
if ! nginx -t || ! systemctl reload nginx.service; then
  [[ -z $prior ]] || { ln -sfn "$prior" /etc/mag-ci/tls/.next; mv -Tf /etc/mag-ci/tls/.next /etc/mag-ci/tls/current; }
  echo 'Git TLS reload failed; previous certificate retained' >&2;exit 1
fi
