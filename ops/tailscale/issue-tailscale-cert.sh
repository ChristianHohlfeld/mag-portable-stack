#!/usr/bin/env bash
# Issue or renew the publicly trusted certificate for mag.tail3e02ae.ts.net.
# `tailscale cert` writes a Let's Encrypt certificate via Tailscale HTTPS.
# The private key stays mode 0640, root:www-data, and is never printed.
# Reloads nginx only when the certificate bytes change.
#   issue-tailscale-cert.sh [--no-reload]
set +x
set -euo pipefail
name=mag.tail3e02ae.ts.net
dir=/etc/mag-nginx/tls/tailscale
crt=$dir/$name.crt
key=$dir/$name.key
[[ ${1:-} == "" || ${1:-} == --no-reload ]] || { echo 'usage: issue-tailscale-cert.sh [--no-reload]' >&2; exit 2; }
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
if [[ -e /etc/mag-stack/TEST-HOST ]]; then
  echo 'tailscale cert: skipped on TEST-HOST'
  exit 0
fi
command -v tailscale >/dev/null
command -v openssl >/dev/null
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
# tailscale cert refuses to overwrite an existing file. Write beside it.
tailscale cert --cert-file "$tmp/cert" --key-file "$tmp/key" "$name" >/dev/null
[[ -s $tmp/cert && -s $tmp/key ]] || { echo 'tailscale cert: empty output' >&2; exit 1; }
openssl x509 -in "$tmp/cert" -noout -checkhost "$name" | grep -F 'does match' >/dev/null
pubcrt=$(openssl x509 -in "$tmp/cert" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
pubkey=$(openssl pkey -in "$tmp/key" -pubout -outform DER | sha256sum | awk '{print $1}')
[[ $pubcrt == "$pubkey" ]] || { echo 'tailscale cert: certificate/key mismatch' >&2; exit 1; }
if getent group www-data >/dev/null; then
  install -d -m 0750 -o root -g www-data "$dir"
  install -m 0644 -o root -g www-data "$tmp/cert" "$tmp/cert.installed"
  install -m 0640 -o root -g www-data "$tmp/key" "$tmp/key.installed"
else
  install -d -m 0750 "$dir"
  install -m 0644 "$tmp/cert" "$tmp/cert.installed"
  install -m 0600 "$tmp/key" "$tmp/key.installed"
fi
changed=0
if [[ ! -f $crt ]] || ! cmp -s "$tmp/cert.installed" "$crt"; then changed=1; fi
mv -f "$tmp/cert.installed" "$crt"
mv -f "$tmp/key.installed" "$key"
if [[ ${1:-} == --no-reload ]]; then
  echo "tailscale cert: $name"
  exit 0
fi
if (( changed )) && systemctl is-active --quiet nginx.service; then
  nginx -t
  systemctl reload nginx.service
fi
echo "tailscale cert: $name"
