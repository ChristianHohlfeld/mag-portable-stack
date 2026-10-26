#!/usr/bin/env bash
# Stage definitions only. Never restart/stop a resident model or delete weights.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
if [[ ${1:-} == --dry-run ]]; then
  echo '+ install DeepSeek per-NUMA units, room-model.env and presets; disable inactive Ornith units; no model restart'
  exit 0
fi
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
OLD=(mag-llm-ornith@{1..4}.service mag-llm-ornith-node@{0..1}.service)
for UNIT in "${OLD[@]}"; do
  if systemctl is-active --quiet "$UNIT"; then
    echo "$UNIT still active; stop it explicitly before the DeepSeek cutover" >&2; exit 1
  fi
done
install -d -m 0755 /etc/mag-stack /etc/mag-llm /usr/local/lib/mag-room /etc/systemd/system/mag-120.service.d
[[ -e /etc/mag-stack/room-model.env ]] || install -m 0644 "$ROOT/ops/room/model.env" /etc/mag-stack/room-model.env
if [[ ! -e /etc/mag-llm/ds-node.env ]]; then
  install -m 0644 "$ROOT/ops/room/ds-node.env" /etc/mag-llm/ds-node.env
fi
install -m 0755 "$ROOT/ops/room/start-ds-node.sh" /usr/local/lib/mag-room/start-ds-node.sh
install -m 0644 "$ROOT/ops/room/mag-llm-ds-node@.service" /etc/systemd/system/mag-llm-ds-node@.service
install -m 0644 "$ROOT/ops/mag-120.d/20-room-audit.conf" /etc/systemd/system/mag-120.service.d/20-room-audit.conf
rm -f /etc/systemd/system/mag-120.service.d/{20-deepseek-audit,30-dual-numa,50-two-bots}.conf
# Empty compatibility file prevents old EnvironmentFile references selecting Ornith.
# A local rollback must restore its own secondary-model.env along with its units.
if [[ -f /etc/mag-stack/secondary-model.env ]]; then
  cp -an /etc/mag-stack/secondary-model.env /etc/mag-stack/secondary-model.env.before-ds
  printf '# Replaced by room-model.env (DeepSeek per NUMA)\n' > /etc/mag-stack/secondary-model.env
fi
systemctl disable "${OLD[@]}" >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl enable mag-llm-ds-node@0.service mag-llm-ds-node@1.service
