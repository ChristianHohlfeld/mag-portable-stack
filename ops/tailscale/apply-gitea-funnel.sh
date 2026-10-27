#!/usr/bin/env bash
# Publish Gitea on https://mag.tail3e02ae.ts.net/ (no port).
# tailscaled owns TCP 443 and forwards raw TLS, with PROXY protocol v2, to
# nginx on 127.0.0.1:4443. nginx SNI-routes that socket. Idempotent.
# Does not restart a resident model or the room. Does not print keys.
#   bash ops/tailscale/apply-gitea-funnel.sh [--dry-run]
set +x
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
root=$(cd -- "$here/../.." && pwd)
origin=https://mag.tail3e02ae.ts.net
node=mag.tail3e02ae.ts.net
if [[ ${1:-} == --dry-run ]]; then
  python3 "$here/merge-gitea-funnel.py" merge </dev/null
  echo "funnel ${origin}/ -> tcp://127.0.0.1:4443 (nginx SNI); tailnet-only :8443 :8445 :8446"
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
command -v systemctl >/dev/null
dns=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
[[ $dns == "$node" ]] || { echo "gitea funnel: this node is ${dns}, expected ${node}" >&2; exit 1; }
sock=/var/run/tailscale/tailscaled.sock
[[ -S $sock ]] || { echo 'gitea funnel: tailscaled socket missing' >&2; exit 1; }

tmp=$(mktemp -d /run/mag-gitea-funnel.XXXXXXXX)
snapdir=$tmp/snap
mkdir -p "$snapdir/files"
manifest=$snapdir/manifest
: > "$manifest"
: > "$snapdir/saved.paths"
cleanup() {
  local rc=$?
  trap - EXIT
  if (( rc != 0 )); then
    if [[ -s $tmp/current && -S $sock ]]; then
      curl -sS --unix-socket "$sock" -o /dev/null -X POST \
        -H 'Content-Type: application/json' --data-binary @"$tmp/current" \
        http://local-tailscaled.sock/localapi/v0/serve-config || true
    fi
    if [[ -s $manifest ]]; then
      while IFS=$'\t' read -r id file kind; do
        [[ -n ${id:-} ]] || continue
        if [[ $kind == file ]]; then cp -a "$snapdir/files/$id" "$file"
        else rm -f -- "$file"; fi
      done < "$manifest"
      nginx -t && systemctl reload nginx.service || true
    fi
    echo 'gitea funnel: previous listeners restored' >&2
  fi
  rm -rf -- "$tmp"
  exit "$rc"
}
save_nginx() {
  local file=$1 id
  [[ -e $snapdir/saved.paths ]] && grep -Fxq "$file" "$snapdir/saved.paths" && return 0
  printf '%s\n' "$file" >> "$snapdir/saved.paths"
  id=$(wc -l < "$snapdir/saved.paths")
  case "$file" in
    /etc/nginx/*|/etc/mag-ci/gitea-proxy.inc) ;;
    *) echo "gitea funnel: refusing to snapshot $file" >&2; exit 1 ;;
  esac
  if [[ -f $file && ! -L $file ]]; then
    cp -a "$file" "$snapdir/files/$id"
    printf '%s\t%s\tfile\n' "$id" "$file" >> "$manifest"
  else
    printf '%s\t%s\tabsent\n' "$id" "$file" >> "$manifest"
  fi
}
api() {
  local method=$1 out=$2
  shift 2
  curl -sS --unix-socket "$sock" -o "$out" -w '%{http_code}' -X "$method" "$@" \
    http://local-tailscaled.sock/localapi/v0/serve-config
}

# Certificate before nginx -t. The gitea vhost cannot load without it.
bash "$here/issue-tailscale-cert.sh" --no-reload
get_code=$(api GET "$tmp/current")
[[ $get_code == 200 ]] || { echo "gitea funnel: serve-config GET HTTP ${get_code}" >&2; exit 1; }
cp -a "$tmp/current" "$snapdir/serve.json"

# Snapshot first, then move listeners. A failure restores this snapshot,
# including from deploy rollback via restore-gitea-funnel.sh.
site=/etc/nginx/conf.d/mag-git.conf
proxy=/etc/mag-ci/gitea-proxy.inc
ini=/etc/gitea/app.ini
shopt -s nullglob
to_retarget=()
for file in /etc/nginx/sites-available/*.conf /etc/nginx/conf.d/*.conf; do
  [[ -f $file && ! -L $file ]] || continue
  grep -Fq 'listen 100.123.114.16:443 ssl http2;' "$file" || continue
  save_nginx "$file"
  to_retarget+=("$file")
done
shopt -u nullglob
save_nginx "$site"
save_nginx "$proxy"
if [[ -n ${MAG_NGINX_TXN:-} && -d $MAG_NGINX_TXN && ! -L $MAG_NGINX_TXN ]]; then
  rm -rf -- "$MAG_NGINX_TXN/funnel-snapshot"
  cp -a "$snapdir" "$MAG_NGINX_TXN/funnel-snapshot"
fi
trap cleanup EXIT
if (( ${#to_retarget[@]} )); then
  for file in "${to_retarget[@]}"; do
    mode=$(stat -c %a "$file")
    python3 "$here/merge-gitea-funnel.py" retarget <"$file" >"$tmp/retargeted"
    install -m "$mode" "$tmp/retargeted" "$file"
  done
fi
install -d -m 0755 /etc/mag-ci
install -m 0644 "$root/ops/gitea-ci/gitea-proxy.inc" "$proxy"
install -m 0644 "$root/ops/gitea-ci/public.nginx.in" "$site"
if ! nginx -t || ! systemctl reload nginx.service; then
  echo 'gitea funnel: nginx config rejected' >&2
  exit 1
fi
if [[ -s $ini ]]; then
  before=$(sha256sum "$ini")
  bash "$root/ops/gitea-ci/configure-gitea.sh"
  after=$(sha256sum "$ini")
  if [[ $before != "$after" ]]; then systemctl restart gitea.service; fi
fi
# The old lego timer must not run HTTP-01 for a name Funnel now serves.
systemctl disable --now mag-ci-cert.timer >/dev/null 2>&1 || true
install -d -m 0755 /usr/local/lib/mag-ci
install -m 0755 "$here/issue-tailscale-cert.sh" /usr/local/lib/mag-ci/issue-tailscale-cert.sh
install -m 0644 "$here/mag-tailscale-cert.service" /etc/systemd/system/mag-tailscale-cert.service
install -m 0644 "$here/mag-tailscale-cert.timer" /etc/systemd/system/mag-tailscale-cert.timer
systemctl daemon-reload
systemctl enable mag-tailscale-cert.timer
systemctl start mag-tailscale-cert.timer

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
    exit 1
  fi
fi
get_code=$(api GET "$tmp/applied")
[[ $get_code == 200 ]] || { echo "gitea funnel: serve-config reread HTTP ${get_code}" >&2; exit 1; }
python3 "$here/merge-gitea-funnel.py" check <"$tmp/applied"
echo "gitea funnel: ${origin}/"
