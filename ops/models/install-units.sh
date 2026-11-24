#!/usr/bin/env bash
# Refresh unit definitions independently of model configuration and runtime builds.
set -euo pipefail
here="$(cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=../host.sh
source "$here/../host.sh"

units=${1:-/etc/systemd/system}
install_unit() {
  local source=$1 target=$2
  install -d -m 0755 "$(dirname "$target")"
  mag_render "$here/$source" > "$target.next"
  chmod 0644 "$target.next"
  mv -f "$target.next" "$target"
}

for unit in mag-modeld.service mag-model@.service mag-model-fetch@.service mag-model-cpu-build.service mag-model-hip-build.service; do
  install_unit "$unit" "$units/$unit"
done
install_unit mag-model-qwen.conf "$units/mag-model@qwen.service.d/20-mi50.conf"
systemctl daemon-reload
