#!/usr/bin/env bash
# Root-managed nft rule for magbot -> Tailscale API only; never flush policy.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
[[ "$(id -u magbot)" == 993 ]] || { echo 'Unexpected magbot uid, refusal' >&2; exit 1; }
command -v nft >/dev/null; command -v dig >/dev/null
nft list chain inet mag_audit output >/dev/null
mapfile -t v4 < <(dig +short A api.tailscale.com @1.1.1.1 | grep -E '^([0-9]{1,3}[.]){3}[0-9]{1,3}$' | sort -u || :)
mapfile -t v6 < <(dig +short AAAA api.tailscale.com @1.1.1.1 | grep -Ei '^[0-9a-f:]+$' | grep -F ':' | sort -u || :)
(( ${#v4[@]} + ${#v6[@]} > 0 && ${#v4[@]} <= 32 && ${#v6[@]} <= 32 )) ||
  { echo 'Invalid/empty Tailscale API DNS; refusing to widen egress' >&2; exit 1; }
for ip in "${v4[@]}"; do
  IFS=. read -r a b c d <<<"$ip"
  for octet in "$a" "$b" "$c" "$d"; do (( 10#$octet <= 255 )) || exit 1; done
done
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
  echo 'flush set inet mag_audit mag_tailscale_api_v4'
  echo 'flush set inet mag_audit mag_tailscale_api_v6'
  if (( ${#v4[@]} )); then
    printf 'add element inet mag_audit mag_tailscale_api_v4 { %s }\n' "$(IFS=,; echo "${v4[*]}")"
  fi
  if (( ${#v6[@]} )); then
    printf 'add element inet mag_audit mag_tailscale_api_v6 { %s }\n' "$(IFS=,; echo "${v6[*]}")"
  fi
} | nft -f -
echo 'MAG Tail API IPv4/v6 allowlist refreshed (magbot uid 993, TCP/443)'
