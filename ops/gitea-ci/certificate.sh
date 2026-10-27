#!/usr/bin/env bash
# Public Git TLS is the Tailscale Funnel certificate for mag.tail3e02ae.ts.net.
# lego, public DNS and port 80 are not used for Gitea.
set +x
set -euo pipefail
echo 'Git TLS is Tailscale Funnel :443; lego is not used for Gitea' >&2
exit 0
