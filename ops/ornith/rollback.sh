#!/usr/bin/env bash
# Ornith per-node servers (B) -> qwen (default) or -> instances (previous 4x Ornith layout, 8191-8194).
# Only Qwen/Ornith units, gateway and room change; retired DeepSeek units stay disabled. No files are deleted
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
SNAP2=$R/var/lib/mag-ornith/cutover-numa    # -> per-node servers
[[ $EUID == 0 || -n $R ]] || { echo 'root required' >&2; exit 4; }
[[ $TARGET == qwen || $TARGET == instances ]] || { echo 'usage: rollback.sh [qwen|instances]' >&2; exit 4; }
rc=0
# Never switch off Ornith before checking the Qwen fallback is actually staged.
# Test rootfs fixtures perform their own fake unit health checks.
if [[ $TARGET == qwen && -z $R ]]; then
  [[ -x /usr/local/lib/mag-qwen/start.sh &&
     -r /etc/mag-qwen/model.sha256 &&
     -s /home/chris/models/qwen3.8-flash-next-uncensored/Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf ]] ||
    { echo 'Qwen rollback refused: verified model/runtime not staged; Ornith stays online' >&2; exit 4; }
fi
# Stop both node servers first (synchronous): legacy instances 1/2 reuse ports 8191/8192.
systemctl disable --now mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service || rc=1
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
# Keep the new fail-closed Ornith/Qwen gateway. Never restore an old snapshot
# binary that could still route retired DeepSeek at :8090.
systemctl daemon-reload || rc=1
systemctl enable --now mag-llm-qwen.service || rc=1
systemctl restart mag-audit-gateway.service || rc=1
systemctl restart mag-120.service || rc=1
(( rc == 0 )) && rm -rf -- "$SNAP" "$SNAP2"
echo "Ornith rollback: secondary=qwen, mag-llm-qwen started (loads in background); rc=$rc"
exit "$rc"
