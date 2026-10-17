#!/usr/bin/env bash
# Run with sudo on disposable CI runner; intercept nft and DNS, never touch firewall.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
here="$(cd "$(dirname "$0")/.." && pwd)"
for f in "$here/refresh-tailscale-egress.sh" "$here/install-tailscale-egress.sh"; do bash -n "$f"; done
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat > "$tmp/bin/id" <<'MOCK'
#!/usr/bin/env bash
[[ "$*" == '-u magbot' ]] && { echo 993; exit 0; }
exec /usr/bin/id "$@"
MOCK
cat > "$tmp/bin/dig" <<'MOCK'
#!/usr/bin/env bash
[[ "${MAG_DNS_EMPTY:-0}" == 1 ]] && exit 0
if [[ "$*" == *' AAAA '* || "$*" == *' AAAA api.'* ]]; then
 echo '2606:4700::6810:abcd'
else
 echo '192.0.2.44'
fi
MOCK
cat > "$tmp/bin/nft" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$MAG_NFT_LOG"
if [[ "$*" == 'list chain inet mag_audit output' ]]; then echo 'chain output'; exit 0; fi
if [[ "$*" == 'list set inet mag_audit '* ]]; then exit 1; fi
if [[ "$*" == '-f -' ]]; then cat >> "$MAG_NFT_LOG"; exit 0; fi
exit 0
MOCK
chmod 0755 "$tmp/bin"/*
export MAG_NFT_LOG="$tmp/nft.log" PATH="$tmp/bin:$PATH"
bash "$here/refresh-tailscale-egress.sh"
grep -Fq 'meta skuid 993 ip daddr @mag_tailscale_api_v4 tcp dport 443 accept' "$MAG_NFT_LOG"
grep -Fq 'meta skuid 993 ip6 daddr @mag_tailscale_api_v6 tcp dport 443 accept' "$MAG_NFT_LOG"
grep -Fq 'add element inet mag_audit mag_tailscale_api_v4 { 192.0.2.44 }' "$MAG_NFT_LOG"
grep -Fq 'add element inet mag_audit mag_tailscale_api_v6 { 2606:4700::6810:abcd }' "$MAG_NFT_LOG"
: > "$MAG_NFT_LOG"
if MAG_DNS_EMPTY=1 bash "$here/refresh-tailscale-egress.sh" > "$tmp/error" 2>&1; then
 echo 'Unresolved DNS must fail closed' >&2; exit 1
fi
! grep -q '^insert rule\\|^add element' "$MAG_NFT_LOG"
echo 'MAG nft IPv4+IPv6 Tailscale-only uid 993 policy; empty DNS refused: PASS'
