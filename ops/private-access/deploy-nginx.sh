#!/usr/bin/env bash
# Atomic nginx companion for MAG: preflight -> stage -> activate -> smoke.
# If an Owner probe device is configured (/etc/mag-stack/deploy-owner-probe.conf)
# and the nginx/whois config changes, an owner-origin smoke from that second
# Tailnet device is mandatory; localhost can never emulate whois. Otherwise the
# smoke is the local 443 ingress check. Any failure rolls nginx and room back.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
site=/etc/nginx/sites-available/mag-room.conf
enabled=/etc/nginx/sites-enabled/mag-room.conf
template="$here/mag-room.nginx.conf"
snippet=/etc/nginx/snippets/mag-tailnet-ok.inc
snippet_src="$here/../nginx/mag-tailnet-ok.inc"
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
  # A userspace Tailscale node has no kernel tun interface. Its SOCKS5
  # connection still originates from the REAL remote Tailscale node, so nginx
  # and native whois must independently attest that exact Owner identity.
  # Proxy mode never relaxes authorization, the Owner role, or the HTTP gate.
  if [[ -n ${MAG_DEPLOY_OWNER_SOCKS5:-} ]]; then
    [[ $MAG_DEPLOY_OWNER_SOCKS5 =~ ^127[.]0[.]0[.]1:([0-9]{1,5})$ ]] ||
      die 'Owner SOCKS5 must be IPv4 loopback 127.0.0.1:PORT on remote device'
    local port=${BASH_REMATCH[1]}
    (( 10#$port >= 1 && 10#$port <= 65535 )) ||
      die 'Owner SOCKS5 port out of range'
    [[ ${MAG_DEPLOY_OWNER_IP:-} =~ ^100[.]([0-9]{1,3}[.]){2}[0-9]{1,3}$ ]] ||
      die 'Pin MAG_DEPLOY_OWNER_IP to the second real Tailnet node in SOCKS5 mode'
    [[ $MAG_DEPLOY_OWNER_IP != 100.123.114.16 ]] ||
      die 'MAG may never be its own Owner probe'
  fi
  jq -e --arg id "$MAG_DEPLOY_OWNER_LOGIN" \
    '.owners|type=="array" and any(.[]; type=="string" and ascii_downcase==($id|ascii_downcase))' \
    /var/lib/magbot/room/access.json >/dev/null ||
    die 'Owner not allowlisted in access.json'
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
      "$MAG_DEPLOY_OWNER_SSH" true >/dev/null ||
    die 'Owner SSH probe unavailable: no safe deploy'
  # Refuse accidentally using MAG itself as its own "external" owner probe.
  local remote_ip
  remote_ip=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
      "$MAG_DEPLOY_OWNER_SSH" 'tailscale ip -4') ||
    die 'Owner probe must have the Tailscale CLI installed'
  [[ $remote_ip =~ ^100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ &&
     $remote_ip != 100.123.114.16 ]] ||
    die 'Owner probe must be a SECOND real Tailscale device (not MAG)'
  if [[ -n ${MAG_DEPLOY_OWNER_SOCKS5:-} ]]; then
    [[ $remote_ip == "$MAG_DEPLOY_OWNER_IP" ]] ||
      die 'Owner SSH target is not the pinned Tailnet node'
    # Early failure if this is an unattended/tagged agent box. No attempt
    # to upgrade a tagged device into an Owner; the real HTTPS smoke below
    # remains the authoritative end-to-end authorization test.
    local tsstatus
    tsstatus=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
      "$MAG_DEPLOY_OWNER_SSH" 'tailscale status --json') ||
      die 'Cannot inspect remote Tailscale user identity'
    jq -e --arg user "$MAG_DEPLOY_OWNER_LOGIN" '
      . as $ts |
      $ts.Self.Online == true and
      (($ts.Self.Tags // []) | length == 0) and
      ([($ts.User // {})[] |
        select(.LoginName != null and
               (.LoginName | ascii_downcase) == ($user | ascii_downcase) and
               .ID == $ts.Self.UserID)] | length == 1)
    ' <<<"$tsstatus" >/dev/null ||
      die 'Remote Tailscale node is not an online, untagged Owner device'
  fi
}
nginx_changes(){
  ! { [[ -f $site ]] && cmp -s "$site" "$template" &&
      [[ $(readlink "$enabled" 2>/dev/null) == ../sites-available/mag-room.conf ]]; }
}
needs_owner_probe(){
  [[ -e $probe_file ]] && nginx_changes
}
local_smoke(){
  local status
  # nginx is on the shared loopback socket. PROXY protocol carries the peer
  # so whois does not see 127.0.0.1. The tailnet path is 100.123.114.16:443
  # after tailscaled is forwarding; this check talks to nginx directly.
  status=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
    --haproxy-protocol \
    --resolve mag.stardenburg.de:4443:127.0.0.1 \
    https://mag.stardenburg.de:4443/ || true)
  [[ $status =~ ^(200|401|403)$ ]] || die "Local ingress check failed (HTTP $status)"
  echo "Smoke OK (local): nginx and room answer on 443 (HTTP $status)"
}
preflight(){
  for bin in nginx curl jq openssl systemctl ssh ip flock; do
    command -v "$bin" >/dev/null || die "Missing $bin"
  done
  [[ -f $template && -d /etc/nginx/sites-available && -d /etc/nginx/sites-enabled ]] ||
    die 'nginx template or site directories missing'
  [[ ! -e /var/lib/mag-private-cutover/pending ]] ||
    die 'Unconfirmed initial ingress cutover in progress; confirm/rollback first'
  nginx -t || die 'Current nginx config already invalid'
  systemctl is-active --quiet nginx.service || die 'nginx not active'
  systemctl is-active --quiet tailscaled.service || die 'tailscaled not active'
  grep -Fq '100.123.114.16/' <<<"$(ip -4 addr show)" ||  # no SIGPIPE under pipefail
    die 'Expected Tailnet IP 100.123.114.16 missing'
  [[ -s /etc/mag-nginx/tls/cert.pem && -s /etc/mag-nginx/tls/key.pem ]] ||
    die 'Staged mag-nginx certificate/key missing'
  openssl x509 -in /etc/mag-nginx/tls/cert.pem -checkhost mag.stardenburg.de -noout |
    grep -Fq 'does match' || die 'Certificate hostname mismatch'
  openssl x509 -in /etc/mag-nginx/tls/cert.pem -checkend 86400 -noout ||
    die 'Certificate expires soon'
  [[ -f /var/lib/magbot/room/access.json ]] || die 'access.json missing'
  if needs_owner_probe; then
    probe_config
  elif nginx_changes; then
    echo 'nginx config changes; no Owner probe device configured: local ingress check only' >&2
  else
    echo 'nginx config unchanged: external Owner probe not required'
  fi
  # Native whois must be present in the old running room BEFORE switching.
  # First install (deploy.sh on a blank host): no old room exists yet; the
  # new room is checked by activate/smoke after the switch instead.
  if [[ ${MAG_DEPLOY_FIRST_INSTALL:-0} == 1 ]] && ! systemctl is-active --quiet mag-120.service; then
    echo 'First install: no previous room to probe for native whois' >&2
    echo 'Preflight OK (no service changes)'
    return 0
  fi
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
  [[ -f $snippet_src ]] || die "mag-tailnet-ok.inc missing from this release: $snippet_src"
  if [[ -f $site ]]; then cp -a "$site" "$MAG_NGINX_TXN/nginx.site.old"
  else touch "$MAG_NGINX_TXN/nginx.site.absent"; fi
  if [[ -L $enabled ]]; then readlink "$enabled" > "$MAG_NGINX_TXN/nginx.link.old"
  else touch "$MAG_NGINX_TXN/nginx.link.absent"; fi
  if [[ -f $snippet ]]; then cp -a "$snippet" "$MAG_NGINX_TXN/nginx.snippet.old"
  else touch "$MAG_NGINX_TXN/nginx.snippet.absent"; fi
  if needs_owner_probe; then touch "$MAG_NGINX_TXN/nginx.changed"; fi
  # Armed before first mutation, including syntax or disk failures.
  touch "$MAG_NGINX_TXN/nginx.staged"
  # Snippet must exist before nginx -t. /opt/mag-stack/current still points
  # at the previous release, which does not have mag-tailnet-ok.inc.
  install -d -m 0755 "$(dirname "$snippet")"
  install -m 0644 "$snippet_src" "$snippet.next"
  mv -f "$snippet.next" "$snippet"
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
  grep -Fq 'listen 127.0.0.1:4443 ssl http2 proxy_protocol;' <<<"$dump" ||
    die 'Shared tailnet TLS listener missing'
  if grep -Eq 'listen[[:space:]]+(100\.123\.114\.16|@TAILNET_BIND@):443\b' <<<"$dump"; then
    echo "$dump" | grep -nE 'listen[[:space:]]+(100\.123\.114\.16|@TAILNET_BIND@):443' >&2 || true
    die 'A managed nginx vhost still listens on the Tailscale :443'
  fi
  systemctl reload nginx.service || die 'nginx reload failed'
  systemctl is-active --quiet nginx.service || die 'nginx unhealthy'
}
smoke(){
  if [[ -n ${MAG_NGINX_TXN:-} && -d $MAG_NGINX_TXN && ! -f $MAG_NGINX_TXN/nginx.changed ]]; then
    local_smoke
    return
  fi
  probe_config
  local data status
  # SSH target must be an independent Owner-authenticated Tailscale node.
  # In userspace mode curl MUST dial through that node's loopback-only SOCKS5.
  # A successful response is checked again against native whois owner+login;
  # neither SSH identity nor self-reported tailscale status alone is trusted.
  local proxy_args=""
  if [[ -n ${MAG_DEPLOY_OWNER_SOCKS5:-} ]]; then
    proxy_args="--noproxy '' --socks5-hostname $MAG_DEPLOY_OWNER_SOCKS5"
  fi
  data=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
    "$MAG_DEPLOY_OWNER_SSH" \
    "curl -q --silent --show-error --fail --connect-timeout 3 --max-time 12 --proto '=https' $proxy_args https://mag.stardenburg.de/api/state") ||
    die 'Owner-origin /api/state failed (403/5xx/TLS/proxy/network)'
  jq -e --arg id "$MAG_DEPLOY_OWNER_LOGIN" \
    '.you.role=="owner" and (.you.login|ascii_downcase)==($id|ascii_downcase)' \
    <<<"$data" >/dev/null || die 'Owner /api/state role/login mismatch'
  status=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
    "$MAG_DEPLOY_OWNER_SSH" \
    "curl -q --silent --show-error --fail --connect-timeout 3 --max-time 12 --proto '=https' $proxy_args --output /dev/null --write-out '%{http_code}' https://mag.stardenburg.de/") ||
    die 'Owner-origin / failed (403/5xx/TLS/proxy/network)'
  [[ $status == 200 ]] || die "Owner / not HTTP 200 ($status)"
  echo 'Smoke OK: / and /api/state through nginx from external Owner'
}
rollback(){
  transaction
  [[ -f $MAG_NGINX_TXN/nginx.staged || -f $MAG_NGINX_TXN/nginx.verified ]] || return 0
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
  if [[ -f $MAG_NGINX_TXN/nginx.snippet.old ]]; then
    cp -a "$MAG_NGINX_TXN/nginx.snippet.old" "$snippet.old-next"
    mv -f "$snippet.old-next" "$snippet"
  elif [[ -f $MAG_NGINX_TXN/nginx.snippet.absent ]]; then
    rm -f "$snippet"
  fi
  nginx -t || die 'Restored nginx syntax invalid: manual recovery needed'
  systemctl reload nginx.service || die 'Restored nginx reload failed'
  rm -f "$MAG_NGINX_TXN/nginx.staged" "$MAG_NGINX_TXN/nginx.verified"
  echo 'Previous nginx ingress restored' >&2
}
case "${1:-}" in
 preflight) preflight ;;
 stage) stage ;;
 activate) activate ;;
 smoke) smoke ;;
 rollback) rollback ;;
 commit)
   transaction
   [[ -f $MAG_NGINX_TXN/nginx.staged ]] || die "Unstaged nginx transaction"
   touch "$MAG_NGINX_TXN/nginx.verified"
   rm -f "$MAG_NGINX_TXN/nginx.staged"
   ;;
 standalone)
   preflight
   # shellcheck source=../deploy-guard.sh
   source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../deploy-guard.sh"
   export MAG_DEPLOY_LOCK=/run/lock/mag-private-deploy.lock
   export MAG_DEPLOY_LOCK_MESSAGE='Another nginx deploy in progress'
   mag_deploy_lock "$@"
   MAG_NGINX_TXN=$(mktemp -d /var/backups/mag-stack/nginx-XXXXXXXX)
   export MAG_NGINX_TXN
   chmod 0700 "$MAG_NGINX_TXN"
   trap 'rc=$?; if ((rc)); then rollback || true; fi' EXIT
   stage; activate; smoke
   rm -f "$MAG_NGINX_TXN/nginx.staged"
   trap - EXIT
   echo 'nginx whois installed and Owner-verified'
   ;;
 *) die 'Usage: deploy-nginx.sh {preflight|stage|activate|smoke|rollback|commit|standalone}' ;;
esac
