#!/usr/bin/env bash
# Atomic nginx companion for MAG: preflight -> stage -> activate -> smoke.
# An owner-origin probe is mandatory; localhost can never emulate whois.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
site=/etc/nginx/sites-available/mag-room.conf
enabled=/etc/nginx/sites-enabled/mag-room.conf
template="$here/mag-room.nginx.conf"
probe_file=/etc/mag-stack/deploy-owner-probe.conf
die(){ echo "MAG whois deploy: $*" >&2; exit 1; }
[[ $EUID == 0 ]] || die 'Root required'
probe_config(){
  [[ -f $probe_file && ! -L $probe_file ]] || die "Owner probe missing: $probe_file"
  [[ $(stat -c %u "$probe_file") == 0 ]] || die 'Owner probe must be root-owned'
  [[ $(stat -c %a "$probe_file") == 600 || $(stat -c %a "$probe_file") == 400 ]] ||
    die 'Owner probe must be 0600 or 0400'
  # Root-only, statically provisioned host config; never read from the browser.
  source "$probe_file"
  [[ ${MAG_DEPLOY_OWNER_SSH:-} =~ ^[a-zA-Z0-9._@-]+$ ]] ||
    die 'MAG_DEPLOY_OWNER_SSH is not a safe SSH destination'
  [[ ${MAG_DEPLOY_OWNER_LOGIN:-} =~ ^[a-zA-Z0-9._@+-]+$ ]] ||
    die 'MAG_DEPLOY_OWNER_LOGIN invalid'
  jq -e --arg id "$MAG_DEPLOY_OWNER_LOGIN" \
    '.owners|type=="array" and any(.[]; type=="string" and ascii_downcase==($id|ascii_downcase))' \
    /var/lib/magbot/room/access.json >/dev/null ||
    die 'Owner not allowlisted in access.json'
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
      "$MAG_DEPLOY_OWNER_SSH" true >/dev/null ||
    die 'Owner SSH probe unavailable: no safe deploy'
}
preflight(){
  for bin in nginx curl jq openssl systemctl ssh ip flock; do
    command -v "$bin" >/dev/null || die "Missing $bin"
  done
  [[ -f $template && -d /etc/nginx/sites-available && -d /etc/nginx/sites-enabled ]] ||
    die 'nginx template or site directories missing'
  nginx -t || die 'Current nginx config already invalid'
  systemctl is-active --quiet nginx.service || die 'nginx not active'
  systemctl is-active --quiet tailscaled.service || die 'tailscaled not active'
  ip -4 addr show | grep -Fq '100.123.114.16/' ||
    die 'Expected Tailnet IP 100.123.114.16 missing'
  [[ -s /etc/mag-nginx/tls/cert.pem && -s /etc/mag-nginx/tls/key.pem ]] ||
    die 'Staged mag-nginx certificate/key missing'
  openssl x509 -in /etc/mag-nginx/tls/cert.pem -checkhost mag.stardenburg.de -noout |
    grep -Fq 'does match' || die 'Certificate hostname mismatch'
  openssl x509 -in /etc/mag-nginx/tls/cert.pem -checkend 86400 -noout ||
    die 'Certificate expires soon'
  [[ -f /var/lib/magbot/room/access.json ]] || die 'access.json missing'
  probe_config
  # Native whois must be present in the old running room BEFORE switching.
  local status
  status=$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' \
     -H 'X-Mag-Remote: 192.0.2.1:34567' \
     http://127.0.0.1:8766/_internal/tailscale/whois || true)
  [[ $status == 403 ]] || die "Native whois unavailable (HTTP $status)"
  if [[ ! -s /etc/credstore/mag-guest-oauth ]]; then
    echo 'Optional guest OAuth absent: invitation disabled; do not block Owner room' >&2
  fi
  echo 'Preflight OK (no service changes)'
}
transaction(){
  [[ ${MAG_NGINX_TXN:-} == /var/backups/mag-stack/* && -d $MAG_NGINX_TXN &&
      ! -L $MAG_NGINX_TXN ]] ||
    die 'MAG_NGINX_TXN must be a real /var/backups/mag-stack/ directory'
}
stage(){
  transaction
  [[ ! -e $MAG_NGINX_TXN/nginx.staged ]] || die 'nginx already staged'
  [[ ! -e $site || ( -f $site && ! -L $site ) ]] || die 'Invalid existing nginx site'
  [[ ! -e $enabled || -L $enabled ]] || die 'Invalid enabled nginx site'
  if [[ -f $site ]]; then cp -a "$site" "$MAG_NGINX_TXN/nginx.site.old"
  else touch "$MAG_NGINX_TXN/nginx.site.absent"; fi
  if [[ -L $enabled ]]; then readlink "$enabled" > "$MAG_NGINX_TXN/nginx.link.old"
  else touch "$MAG_NGINX_TXN/nginx.link.absent"; fi
  # Armed before first mutation, including syntax or disk failures.
  touch "$MAG_NGINX_TXN/nginx.staged"
  install -m 0644 "$template" "$site.next"
  mv -f "$site.next" "$site"
  ln -sfn ../sites-available/mag-room.conf "$enabled"
  nginx -t || die 'Candidate nginx -t failed; rollback required'
}
activate(){
  transaction
  [[ -f $MAG_NGINX_TXN/nginx.staged ]] || die 'No staged nginx change'
  local dump
  dump=$(nginx -T 2>&1) || die 'nginx -T failed'
  grep -Fq 'auth_request /_mag_whois;' <<<"$dump" ||
    die 'whois auth_request missing from effective config'
  grep -Fq 'proxy_set_header X-Mag-Auth-Proof $mag_proof;' <<<"$dump" ||
    die 'Signed whois HMAC header missing'
  grep -Fq 'proxy_set_header X-Tailscale-IP $mag_ip;' <<<"$dump" ||
    die 'Trusted client IP header missing'
  grep -Fq 'listen 100.123.114.16:443 ssl' <<<"$dump" ||
    die 'Private 443 listener missing'
  systemctl reload nginx.service || die 'nginx reload failed'
  systemctl is-active --quiet nginx.service || die 'nginx unhealthy'
}
smoke(){
  probe_config
  local data status
  # SSH client MUST be an independent Owner-logged-in Tailscale device.
  # Curl uses real networking + CA verification: no spoofed identity headers.
  data=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
    "$MAG_DEPLOY_OWNER_SSH" \
    'curl --silent --show-error --fail --connect-timeout 3 --max-time 12 https://mag.stardenburg.de/api/state') ||
    die 'Owner-origin /api/state failed (403/5xx/TLS/network)'
  jq -e --arg id "$MAG_DEPLOY_OWNER_LOGIN" \
    '.you.role=="owner" and (.you.login|ascii_downcase)==($id|ascii_downcase)' \
    <<<"$data" >/dev/null || die 'Owner /api/state role/login mismatch'
  status=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
    "$MAG_DEPLOY_OWNER_SSH" \
    "curl --silent --show-error --fail --connect-timeout 3 --max-time 12 --output /dev/null --write-out '%{http_code}' https://mag.stardenburg.de/") ||
    die 'Owner-origin / failed (403/5xx/TLS/network)'
  [[ $status == 200 ]] || die "Owner / not HTTP 200 ($status)"
  echo 'Smoke OK: / and /api/state through nginx from external Owner'
}
rollback(){
  transaction
  [[ -f $MAG_NGINX_TXN/nginx.staged ]] || return 0
  if [[ -f $MAG_NGINX_TXN/nginx.site.old ]]; then
    cp -a "$MAG_NGINX_TXN/nginx.site.old" "$site.old-next"
    mv -f "$site.old-next" "$site"
  elif [[ -f $MAG_NGINX_TXN/nginx.site.absent ]]; then
    rm -f "$site"
  else die 'Missing nginx site rollback snapshot'; fi
  if [[ -f $MAG_NGINX_TXN/nginx.link.old ]]; then
    local target
    target=$(cat "$MAG_NGINX_TXN/nginx.link.old")
    [[ $target == /* || $target == ../* ]] || die 'Invalid previous site link'
    ln -sfn "$target" "$enabled"
  elif [[ -f $MAG_NGINX_TXN/nginx.link.absent ]]; then
    rm -f "$enabled"
  else die 'Missing enabled-link snapshot'; fi
  nginx -t || die 'Restored nginx syntax invalid: manual recovery needed'
  systemctl reload nginx.service || die 'Restored nginx reload failed'
  rm -f "$MAG_NGINX_TXN/nginx.staged"
  echo 'Previous nginx ingress restored' >&2
}
case "${1:-}" in
 preflight) preflight ;;
 stage) stage ;;
 activate) activate ;;
 smoke) smoke ;;
 rollback) rollback ;;
 standalone)
   preflight
   exec 9>/run/lock/mag-private-deploy.lock
   flock -n 9 || die 'Another nginx deploy in progress'
   MAG_NGINX_TXN=$(mktemp -d /var/backups/mag-stack/nginx-XXXXXXXX)
   export MAG_NGINX_TXN
   chmod 0700 "$MAG_NGINX_TXN"
   trap 'rc=$?; if ((rc)); then rollback || true; fi' EXIT
   stage; activate; smoke
   rm -f "$MAG_NGINX_TXN/nginx.staged"
   trap - EXIT
   echo 'nginx whois installed and Owner-verified'
   ;;
 *) die 'Usage: deploy-nginx.sh {preflight|stage|activate|smoke|rollback|standalone}' ;;
esac
