#!/usr/bin/env bash
# Shared Ornith -> qwen (default) or -> instances (previous 4x Ornith layout, 8191-8194).
# Only Qwen/Ornith units, gateway and room change; mag-llm and tunnels stay untouched. No files are deleted
# except this tool's own snapshot directories after a successful rollback.
# Usage: rollback.sh [qwen|instances]   (MAG_ORNITH_ROOTFS only for tests)
set -uo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
R=${MAG_ORNITH_ROOTFS:-}
TARGET=${1:-qwen}
CONF=$R/etc/mag-stack/secondary-model.env
GATE=$R/opt/mag-audit/bin/mag-audit-gateway
GUNIT=$R/etc/systemd/system/mag-audit-gateway.service
SNAP=$R/var/lib/mag-ornith/cutover          # Qwen -> 4x instances (older cutover)
SNAP2=$R/var/lib/mag-ornith/cutover-shared  # -> shared server
[[ $EUID == 0 || -n $R ]] || { echo 'root required' >&2; exit 4; }
[[ $TARGET == qwen || $TARGET == instances ]] || { echo 'usage: rollback.sh [qwen|instances]' >&2; exit 4; }
rc=0
systemctl disable --now mag-llm-ornith.service || rc=1
if [[ $TARGET == instances ]]; then
  # Gateway unit with one port per bot (snapshot, else the legacy port list).
  if [[ -f $SNAP2/gateway.service ]]; then
    cp -p "$SNAP2/gateway.service" "$GUNIT" || rc=1
  else
    sed -i 's/^Environment=MAG_MODEL_ORNITH_PORTS=.*/Environment=MAG_MODEL_ORNITH_PORTS=8191,8192,8193,8194/' "$GUNIT" || rc=1
  fi
  if [[ -x $SNAP2/gateway.bin ]]; then
    install -m 0755 "$SNAP2/gateway.bin" "$GATE.next" && mv -f "$GATE.next" "$GATE" || rc=1
  fi
  printf 'MAG_SECONDARY_MODEL=ornith\n' > "$CONF.next" && chmod 0644 "$CONF.next" && mv -f "$CONF.next" "$CONF" || rc=1
  systemctl daemon-reload || rc=1
  for n in 1 2 3 4; do systemctl enable "mag-llm-ornith@$n.service" || rc=1; done
  systemctl start --no-block mag-llm-ornith@{1,2,3,4}.service || rc=1
  systemctl restart mag-audit-gateway.service || rc=1
  systemctl restart mag-120.service || rc=1
  (( rc == 0 )) && rm -rf -- "$SNAP2"
  echo "Ornith rollback: 4x instances 8191-8194 started (load in background); rc=$rc"
  exit "$rc"
fi
for n in 1 2 3 4; do
  systemctl disable --now "mag-llm-ornith@$n.service" || rc=1
done
install -d -m 0755 "$(dirname "$CONF")"
printf 'MAG_SECONDARY_MODEL=qwen\n' > "$CONF.next" && chmod 0644 "$CONF.next" && mv -f "$CONF.next" "$CONF" || rc=1
# The Qwen-era gateway also understands qwen; keep the new one unless a snapshot exists.
if [[ -x $SNAP/gateway.bin ]]; then
  install -m 0755 "$SNAP/gateway.bin" "$GATE.next" && mv -f "$GATE.next" "$GATE" || rc=1
elif [[ -x $SNAP2/gateway.bin && $(cat "$SNAP2/from" 2>/dev/null) == qwen ]]; then
  install -m 0755 "$SNAP2/gateway.bin" "$GATE.next" && mv -f "$GATE.next" "$GATE" || rc=1
fi
systemctl daemon-reload || rc=1
systemctl enable --now mag-llm-qwen.service || rc=1
systemctl restart mag-audit-gateway.service || rc=1
systemctl restart mag-120.service || rc=1
(( rc == 0 )) && rm -rf -- "$SNAP" "$SNAP2"
echo "Ornith rollback: secondary=qwen, mag-llm-qwen started (loads in background); rc=$rc"
exit "$rc"
