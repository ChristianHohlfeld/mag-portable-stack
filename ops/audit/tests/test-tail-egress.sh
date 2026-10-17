#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
bash -n "$here/refresh-tailscale-egress.sh" "$here/install-tailscale-egress.sh"
grep -Fq 'meta skuid 993' "$here/refresh-tailscale-egress.sh"
grep -Fq 'tcp dport 443 accept' "$here/refresh-tailscale-egress.sh"
grep -Fq 'mag_tailscale_api_v4' "$here/refresh-tailscale-egress.sh"
grep -Fq 'mag_tailscale_api_v6' "$here/refresh-tailscale-egress.sh"
grep -Fq 'OnUnitActiveSec=10min' "$here/mag-tailscale-egress.timer"
echo 'Tailscale egress static contract PASS'
