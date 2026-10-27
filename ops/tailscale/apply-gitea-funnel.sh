#!/usr/bin/env bash
# Publish Gitea on Tailscale Funnel :443. Idempotent. Leaves tailnet serves.
# Does not restart a resident model. Does not print credentials.
#   bash ops/tailscale/apply-gitea-funnel.sh [--dry-run]
set +x
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
root=$(cd -- "$here/../.." && pwd)
origin=https://mag.tail3e02ae.ts.net
node=mag.tail3e02ae.ts.net
if [[ ${1:-} == --dry-run ]]; then
  python3 "$here/merge-gitea-funnel.py" merge </dev/null
  echo "funnel ${origin}/ -> http://127.0.0.1:3010 (nginx); tailnet-only :8443 :8445 :8446"
  exit 0
fi
if [[ -e /etc/mag-stack/TEST-HOST ]]; then
  echo 'gitea funnel: skipped on TEST-HOST (no tailnet)'
  exit 0
fi
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
[[ ${1:-} == "" ]] || { echo 'usage: apply-gitea-funnel.sh [--dry-run]' >&2; exit 2; }
command -v tailscale >/dev/null
command -v python3 >/dev/null
command -v nginx >/dev/null
dns=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
[[ $dns == "$node" ]] || { echo "gitea funnel: this node is ${dns}, expected ${node}" >&2; exit 1; }
sock=/var/run/tailscale/tailscaled.sock
[[ -S $sock ]] || { echo 'gitea funnel: tailscaled socket missing' >&2; exit 1; }

site=/etc/nginx/conf.d/mag-git.conf
ini=/etc/gitea/app.ini
tmp=$(mktemp -d /run/mag-gitea-funnel.XXXXXXXX)
cleanup() { rm -rf -- "$tmp"; }
trap cleanup EXIT
[[ -e $site ]] && cp -a "$site" "$tmp/nginx.conf"
[[ -s $ini ]] && cp -a "$ini" "$tmp/app.ini"
install -d -m 0755 /etc/mag-ci
install -m 0644 "$root/ops/gitea-ci/gitea-proxy.inc" /etc/mag-ci/gitea-proxy.inc
install -m 0644 "$root/ops/gitea-ci/public.nginx.in" "$site"
restore_site() {
  if [[ -s $tmp/nginx.conf ]]; then cp -a "$tmp/nginx.conf" "$site"; else rm -f "$site"; fi
}
if ! nginx -t || ! systemctl reload nginx.service; then
  restore_site
  echo 'gitea funnel: nginx config rejected; previous site restored' >&2
  exit 1
fi
if [[ -s $ini ]]; then
  before=$(sha256sum "$ini")
  bash "$root/ops/gitea-ci/configure-gitea.sh"
  after=$(sha256sum "$ini")
  if [[ $before != "$after" ]]; then systemctl restart gitea.service; fi
fi
# Funnel's certificate replaces the public lego listener. An old timer must
# not try HTTP-01 against a name that is no longer served here.
systemctl disable --now mag-ci-cert.timer >/dev/null 2>&1 || true

api() {
  local method=$1 out=$2
  shift 2
  curl -sS --unix-socket "$sock" -o "$out" -w '%{http_code}' -X "$method" "$@" \
    http://local-tailscaled.sock/localapi/v0/serve-config
}
get_code=$(api GET "$tmp/current")
[[ $get_code == 200 ]] || { echo "gitea funnel: serve-config GET HTTP ${get_code}" >&2; exit 1; }
python3 "$here/merge-gitea-funnel.py" merge <"$tmp/current" >"$tmp/merged"
python3 "$here/merge-gitea-funnel.py" check <"$tmp/merged"
same=0
if python3 - "$tmp/current" "$tmp/merged" <<'PY'
import json, sys
def load(path):
    raw = open(path).read().strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return {} if data is None else data
sys.exit(0 if load(sys.argv[1]) == load(sys.argv[2]) else 1)
PY
then same=1
fi
if (( same == 0 )); then
  post_code=$(api POST "$tmp/post" -H 'Content-Type: application/json' --data-binary @"$tmp/merged")
  if [[ $post_code != 200 ]]; then
    echo "gitea funnel: serve-config POST HTTP ${post_code}" >&2
    api POST "$tmp/restore" -H 'Content-Type: application/json' --data-binary @"$tmp/current" >/dev/null || true
    exit 1
  fi
fi
get_code=$(api GET "$tmp/applied")
[[ $get_code == 200 ]] || { echo "gitea funnel: serve-config reread HTTP ${get_code}" >&2; exit 1; }
if ! python3 "$here/merge-gitea-funnel.py" check <"$tmp/applied"; then
  echo 'gitea funnel: applied config is not Gitea-only; restoring previous' >&2
  api POST "$tmp/restore" -H 'Content-Type: application/json' --data-binary @"$tmp/current" >/dev/null || true
  exit 1
fi
echo "gitea funnel: ${origin}/"
