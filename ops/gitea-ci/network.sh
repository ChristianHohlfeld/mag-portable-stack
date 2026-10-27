#!/usr/bin/env bash
# Dedicated CI network, never a bot/libvirt interface. No guest access to host
# services except the authenticated Gitea proxy, nor to private/Tailnet peers.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
ip link show mag-ci >/dev/null 2>&1 || ip link add mag-ci type bridge
ip addr replace 172.31.255.1/30 dev mag-ci
ip link set mag-ci up
ip link show magci0 >/dev/null 2>&1 || ip tuntap add dev magci0 mode tap user mag-ci-vm
ip link set magci0 master mag-ci
ip link set magci0 up
sysctl -q -w net.ipv4.ip_forward=1
# Replace our own table atomically. Never flush another service's firewall.
file=$(mktemp);trap 'rm -f "$file"' EXIT
if nft list table inet mag_ci >/dev/null 2>&1; then echo 'delete table inet mag_ci' > "$file"; fi
cat >> "$file" <<'NFT'
table inet mag_ci {
 set blocked_v4 { type ipv4_addr; flags interval; elements = { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 240.0.0.0/4 } }
 chain input { type filter hook input priority -15; policy accept;
   iifname "mag-ci" ip saddr 172.31.255.2 tcp dport 3000 accept
   iifname "mag-ci" drop
 }
 chain forward { type filter hook forward priority -15; policy accept;
   oifname "mag-ci" ct state established,related accept
   oifname "mag-ci" drop
   iifname "mag-ci" ip saddr != 172.31.255.2 drop
   iifname "mag-ci" meta nfproto ipv6 drop
   iifname "mag-ci" ip daddr @blocked_v4 drop
   iifname "mag-ci" tcp dport { 80, 443, 53 } accept
   iifname "mag-ci" udp dport 53 accept
   iifname "mag-ci" drop
 }
 chain nat { type nat hook postrouting priority srcnat; policy accept;
   ip saddr 172.31.255.2 oifname != "mag-ci" masquerade
 }
}
NFT
nft -c -f "$file"
nft -f "$file"
