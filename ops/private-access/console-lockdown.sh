#!/usr/bin/env bash
# One-time operator-gated lockdown. Must be run at MAG's physical/serial
# console AFTER Ali's SSH deployment; doing it remotely would lock Ali out.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root console required' >&2; exit 4; }
[[ -z "${SSH_CONNECTION:-}" && -z "${SSH_CLIENT:-}" ]] ||
  { echo 'Refuse SSH session; use MAG console' >&2; exit 4; }
terminal="$(tty)" || exit 4
case "$terminal" in /dev/tty[0-9]*|/dev/ttyS[0-9]*|/dev/hvc[0-9]*|/dev/console) ;; *)
  echo "Not a physical/serial console: $terminal" >&2; exit 4;;
esac
for cmd in nft systemctl tailscale ss; do command -v "$cmd" >/dev/null || exit 4; done
# All remote sshd shell listeners must be on port 22; refuse unknown ports.
if command -v sshd >/dev/null; then
  ports="$(sshd -T | awk '$1=="port"{print $2}' | sort -u)"
  [[ "$ports" == 22 ]] || { echo "Nonstandard sshd ports: $ports" >&2; exit 4; }
fi
[[ "$(nft list tables | grep -F 'table inet mag_audit' | wc -l)" -eq 1 ]] ||
  { echo 'Existing audit firewall missing; refuse' >&2; exit 4; }
here="$(cd "$(dirname "$0")" && pwd)"
install -D -m 0644 -o root -g root "$here/mag-console-only.nft" /etc/nftables.d/mag-console-only.nft
install -D -m 0755 -o root -g root "$here/console-lockdown-apply.sh" /usr/local/lib/mag-private/console-lockdown-apply.sh
install -D -m 0644 -o root -g root "$here/mag-console-only.service" /etc/systemd/system/mag-console-only.service
# Disable Tailscale's userspace SSH as well as kernel TCP/22. Do not touch
# tailscaled identity, peers, MAG :443 or model services.
tailscale set --ssh=false
systemctl daemon-reload
systemctl enable --now mag-console-only.service
nft list table inet mag_console_only
echo 'Direct inbound SSH disabled. MAG owner via authenticated Tailnet/nginx only; physical console remains.'
