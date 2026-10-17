#!/usr/bin/env bash
# Root-managed nft set for magbot -> api.tailscale.com:443 only.
# Never flush the set: api.tailscale.com rotates inside a stable pool, and a
# single resolver only sees a subset. Union that pool with every resolver.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
[[ "$(id -u magbot)" == 993 ]] || { echo 'Unexpected magbot uid, refusal' >&2; exit 1; }
command -v nft >/dev/null
command -v dig >/dev/null
nft list chain inet mag_audit output >/dev/null

valid_v4() {
  local ip=$1 octet
  [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local IFS=.
  # shellcheck disable=SC2086
  for octet in $ip; do ((10#$octet <= 255)) || return 1; done
}
valid_v6() {
  [[ "$1" =~ ^[0-9A-Fa-f:]+$ && "$1" == *:* && "$1" != *::*::* ]]
}

declare -A V4=() V6=()
add_v4() { valid_v4 "$1" && V4["$1"]=1 || true; }
add_v6() { valid_v6 "$1" && V6["$1"]=1 || true; }

if [[ "${MAG_TAILSCALE_NO_STABLE:-0}" != 1 ]]; then
  if [[ -n "${MAG_TAILSCALE_V4_STABLE:-}" ]]; then
    for ip in ${MAG_TAILSCALE_V4_STABLE//,/ }; do add_v4 "$ip"; done
  else
    for i in $(seq 101 116); do add_v4 "192.200.0.$i"; done
  fi
  if [[ -n "${MAG_TAILSCALE_V6_STABLE:-}" ]]; then
    for ip in ${MAG_TAILSCALE_V6_STABLE//,/ }; do add_v6 "$ip"; done
  else
    for i in $(seq 101 116); do add_v6 "2606:b740:49::$i"; done
  fi
fi

resolvers=(1.1.1.1 8.8.8.8 9.9.9.9)
if [[ -n "${MAG_TAILSCALE_RESOLVERS:-}" ]]; then
  # shellcheck disable=SC2206
  resolvers=(${MAG_TAILSCALE_RESOLVERS})
fi
lookup() {
  local type=$1 server=$2
  if [[ -n "$server" ]]; then
    dig +time=2 +tries=1 +short "$type" api.tailscale.com @"$server" || true
  else
    dig +time=2 +tries=1 +short "$type" api.tailscale.com || true
  fi
}
for server in "${resolvers[@]}" ""; do
  while IFS= read -r ip; do
    [[ -n "$ip" ]] && add_v4 "$ip"
  done < <(lookup A "$server")
  while IFS= read -r ip; do
    [[ -n "$ip" ]] && add_v6 "$ip"
  done < <(lookup AAAA "$server")
done

v4s=()
v6s=()
if ((${#V4[@]})); then mapfile -t v4s < <(printf '%s\n' "${!V4[@]}" | LC_ALL=C sort -u); fi
if ((${#V6[@]})); then mapfile -t v6s < <(printf '%s\n' "${!V6[@]}" | LC_ALL=C sort -u); fi
(( ${#v4s[@]} + ${#v6s[@]} > 0 && ${#v4s[@]} <= 64 && ${#v6s[@]} <= 64 )) ||
  { echo 'Invalid/empty Tailscale API DNS; refusing to widen egress' >&2; exit 1; }

for family in v4 v6; do
  setname="mag_tailscale_api_$family"
  if ! nft list set inet mag_audit "$setname" >/dev/null 2>&1; then
    typ=ipv4_addr; [[ "$family" == v6 ]] && typ=ipv6_addr
    nft add set inet mag_audit "$setname" "{ type $typ; }"
  fi
  comment="magbot_tailscale_${family}_only"
  if ! nft list chain inet mag_audit output | grep -Fq "$comment"; then
    dest="ip"; [[ "$family" == v6 ]] && dest="ip6"
    nft insert rule inet mag_audit output meta skuid 993 \
      "$dest" daddr "@$setname" tcp dport 443 accept comment "$comment"
  fi
done
{
  if ((${#v4s[@]})); then
    printf 'add element inet mag_audit mag_tailscale_api_v4 { %s }\n' "$(IFS=,; echo "${v4s[*]}")"
  fi
  if ((${#v6s[@]})); then
    printf 'add element inet mag_audit mag_tailscale_api_v6 { %s }\n' "$(IFS=,; echo "${v6s[*]}")"
  fi
} | nft -f -
echo 'MAG Tail API IPv4/v6 allowlist union refreshed (no flush, magbot uid 993, TCP/443)'
