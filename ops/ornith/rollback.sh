#!/usr/bin/env bash
# Ornith -> Qwen: stop the 4 instances, secondary=qwen, start mag-llm-qwen.
# Only Qwen/Ornith units, gateway and room change; mag-llm and tunnels stay untouched. Qwen files are kept.
set -uo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
R=${MAG_ORNITH_ROOTFS:-}
CONF=$R/etc/mag-stack/secondary-model.env
GATE=$R/opt/mag-audit/bin/mag-audit-gateway
SNAP=$R/var/lib/mag-ornith/cutover
[[ $EUID == 0 || -n $R ]] || { echo 'root required' >&2; exit 4; }
rc=0
for n in 1 2 3 4; do
  systemctl disable --now "mag-llm-ornith@$n.service" || rc=1
done
install -d -m 0755 "$(dirname "$CONF")"
printf 'MAG_SECONDARY_MODEL=qwen\n' > "$CONF.next" && chmod 0644 "$CONF.next" && mv -f "$CONF.next" "$CONF" || rc=1
# The Qwen-era gateway also understands qwen; keep the new one unless a snapshot exists.
if [[ -x $SNAP/gateway.bin ]]; then
  install -m 0755 "$SNAP/gateway.bin" "$GATE.next" && mv -f "$GATE.next" "$GATE" || rc=1
fi
systemctl daemon-reload || rc=1
systemctl enable --now mag-llm-qwen.service || rc=1
systemctl restart mag-audit-gateway.service || rc=1
systemctl restart mag-120.service || rc=1
(( rc == 0 )) && rm -rf -- "$SNAP"
echo "Ornith rollback: secondary=qwen, mag-llm-qwen started (loads in background); rc=$rc"
exit "$rc"
