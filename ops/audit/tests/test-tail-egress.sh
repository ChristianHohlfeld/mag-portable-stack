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
if [[ "$*" == *AAAA* ]]; then
 echo '2001:db8::abcd'
 exit 0
fi
if [[ "$*" == *@8.8.8.8* ]]; then echo '192.0.2.45'; exit 0; fi
if [[ "$*" == *@1.1.1.1* || "$*" == *@9.9.9.9* ]]; then echo '192.0.2.44'; exit 0; fi
echo '192.0.2.46'
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
export MAG_TAILSCALE_RESOLVERS='1.1.1.1 8.8.8.8 9.9.9.9'
export MAG_TAILSCALE_V4_STABLE='192.0.2.101'
export MAG_TAILSCALE_V6_STABLE='2001:db8::101'
bash "$here/refresh-tailscale-egress.sh"
grep -Fq 'meta skuid 993 ip daddr @mag_tailscale_api_v4 tcp dport 443 accept' "$MAG_NFT_LOG"
grep -Fq 'meta skuid 993 ip6 daddr @mag_tailscale_api_v6 tcp dport 443 accept' "$MAG_NFT_LOG"
grep -Fq '192.0.2.44' "$MAG_NFT_LOG"
grep -Fq '192.0.2.45' "$MAG_NFT_LOG"
grep -Fq '192.0.2.46' "$MAG_NFT_LOG"
grep -Fq '192.0.2.101' "$MAG_NFT_LOG"
grep -Fq '2001:db8::101' "$MAG_NFT_LOG"
grep -Fq '2001:db8::abcd' "$MAG_NFT_LOG"
! grep -Fq 'flush set' "$MAG_NFT_LOG"
grep -Fq 'for i in $(seq 101 116); do add_v4 "192.200.0.$i"; done' "$here/refresh-tailscale-egress.sh"
grep -Fq 'for i in $(seq 101 116); do add_v6 "2606:b740:49::$i"; done' "$here/refresh-tailscale-egress.sh"
: > "$MAG_NFT_LOG"
if MAG_DNS_EMPTY=1 MAG_TAILSCALE_NO_STABLE=1 bash "$here/refresh-tailscale-egress.sh" > "$tmp/error" 2>&1; then
 echo 'Unresolved DNS without the stable pool must fail closed' >&2; exit 1
fi
! grep -Fq 'add element' "$MAG_NFT_LOG"
: > "$MAG_NFT_LOG"
MAG_DNS_EMPTY=1 bash "$here/refresh-tailscale-egress.sh"
grep -Fq '192.0.2.101' "$MAG_NFT_LOG"
! grep -Fq 'flush set' "$MAG_NFT_LOG"
echo 'MAG nft IPv4+IPv6 Tailscale union, stable pool, no flush; empty DNS without pool refused: PASS'
