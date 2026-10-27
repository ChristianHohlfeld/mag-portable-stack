#!/usr/bin/env bash
# Publish Gitea on https://mag.tail3e02ae.ts.net/ (no port).
# tailscaled terminates TLS on :443 and proxies to http://127.0.0.1:3000.
# nginx is moved off the Tailscale address so it cannot take that socket.
# Idempotent. Does not restart a resident model or the room. Does not print keys.
#   bash ops/tailscale/apply-gitea-funnel.sh [--dry-run]
# MAG_GITEA_FUNNEL_TEST=1 remaps host paths for tests. It is refused unless
# MAG_GITEA_FUNNEL_NGINX is a directory outside /etc/nginx.
set +x
set -eEuo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
root=$(cd -- "$here/../.." && pwd)
origin=https://mag.tail3e02ae.ts.net
node=mag.tail3e02ae.ts.net
test_mode=${MAG_GITEA_FUNNEL_TEST:-0}
nginx_root=${MAG_GITEA_FUNNEL_NGINX:-/etc/nginx}
mag_ci_root=${MAG_GITEA_FUNNEL_MAG_CI:-/etc/mag-ci}
ini=${MAG_GITEA_FUNNEL_INI:-/etc/gitea/app.ini}
systemd_dir=${MAG_GITEA_FUNNEL_SYSTEMD:-/etc/systemd/system}
lib_dir=${MAG_GITEA_FUNNEL_LIB:-/usr/local/lib/mag-ci}
sock=${MAG_GITEA_FUNNEL_SOCK:-/var/run/tailscale/tailscaled.sock}

funnel_logged=0
on_err() {
  funnel_logged=1
  echo "gitea funnel: failed at line ${1:-?} (exit ${2:-1}): ${3:-unknown command}" >&2
}
die() {
  local rc=${1:-1} why=${2:-${BASH_COMMAND:-exit}}
  echo "gitea funnel: failed at line ${BASH_LINENO[0]:-?} (exit ${rc}): ${why}" >&2
  funnel_logged=1
  exit "$rc"
}
trap 'on_err "$LINENO" "$?" "$BASH_COMMAND"' ERR
dump_nginx_t() {
  local dest=$1
  # Live `nginx -T` prints the config dump on stderr. Candidates/scan must
  # read both streams or leftover sites-enabled listens stay invisible.
  nginx -T >"$dest.stdout" 2>"$dest.stderr" || true
  cat -- "$dest.stdout" "$dest.stderr" >"$dest"
}
run_cmd() {
  local rc=0
  echo "gitea funnel: + $*" >&2
  "$@" || rc=$?
  echo "gitea funnel: rc=${rc} $*" >&2
  return "$rc"
}

if [[ ${1:-} == --dry-run ]]; then
  python3 "$here/merge-gitea-funnel.py" merge </dev/null
  echo "funnel ${origin}/ -> http://127.0.0.1:3000; tailnet-only :8443 :8445 :8446"
  exit 0
fi
if [[ $test_mode == 1 ]]; then
  [[ -n $nginx_root && $nginx_root != /etc/nginx && -d $nginx_root && ! -L $nginx_root ]] || {
    echo 'gitea funnel: TEST mode needs MAG_GITEA_FUNNEL_NGINX as a real directory outside /etc/nginx' >&2
    exit 1
  }
  [[ -n ${MAG_GITEA_FUNNEL_SERVE:-} ]] || {
    echo 'gitea funnel: TEST mode needs MAG_GITEA_FUNNEL_SERVE' >&2
    exit 1
  }
elif [[ -e /etc/mag-stack/TEST-HOST ]]; then
  echo 'gitea funnel: skipped on TEST-HOST (no tailnet)'
  exit 0
fi
if [[ $test_mode != 1 ]]; then
  [[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
fi
[[ ${1:-} == "" ]] || { echo 'usage: apply-gitea-funnel.sh [--dry-run]' >&2; exit 2; }
command -v python3 >/dev/null || { echo 'gitea funnel: python3 missing' >&2; exit 1; }
command -v nginx >/dev/null || { echo 'gitea funnel: nginx missing' >&2; exit 1; }
command -v systemctl >/dev/null || { echo 'gitea funnel: systemctl missing' >&2; exit 1; }
command -v tailscale >/dev/null || { echo 'gitea funnel: tailscale missing' >&2; exit 1; }
if [[ $test_mode != 1 ]]; then
  command -v curl >/dev/null || { echo 'gitea funnel: curl missing' >&2; exit 1; }
fi
dns=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))') || {
  echo 'gitea funnel: tailscale status failed' >&2
  exit 1
}
[[ $dns == "$node" ]] || { echo "gitea funnel: this node is ${dns}, expected ${node}" >&2; exit 1; }
if [[ $test_mode != 1 ]]; then
  [[ -S $sock ]] || { echo 'gitea funnel: tailscaled socket missing' >&2; exit 1; }
fi

tmp=
snapdir=
manifest=
keep_nginx=0
if [[ $test_mode == 1 ]]; then
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/mag-gitea-funnel.XXXXXXXX")
else
  tmp=$(mktemp -d /run/mag-gitea-funnel.XXXXXXXX)
fi
snapdir=$tmp/snap
mkdir -p "$snapdir/files" || { echo 'gitea funnel: cannot create snapshot dir' >&2; exit 1; }
manifest=$snapdir/manifest
: > "$manifest"
: > "$snapdir/saved.paths"
# After nginx has left 100.123.114.16:443, do not put it back. Funnel can only
# bind that socket once nginx is gone. A missing funnel nodeAttr must not undo
# the collision fix.
cleanup() {
  local rc=$?
  trap - EXIT ERR
  if (( rc != 0 && funnel_logged == 0 )); then
    echo "gitea funnel: failed at line ${BASH_LINENO[0]:-?} (exit ${rc}): ${BASH_COMMAND:-exit}" >&2
    funnel_logged=1
  fi
  if (( rc != 0 && keep_nginx == 0 )); then
    if [[ $test_mode == 1 && -n ${MAG_GITEA_FUNNEL_SERVE:-} && -n ${tmp:-} && -s $tmp/current ]]; then
      cp -a "$tmp/current" "$MAG_GITEA_FUNNEL_SERVE" || true
    elif [[ -n ${tmp:-} && -s $tmp/current && -S ${sock:-} ]]; then
      curl -sS --unix-socket "$sock" -o /dev/null -X POST \
        -H 'Content-Type: application/json' --data-binary @"$tmp/current" \
        http://local-tailscaled.sock/localapi/v0/serve-config || true
    fi
    if [[ -n ${manifest:-} && -s $manifest ]]; then
      while IFS=$'\t' read -r id file kind; do
        [[ -n ${id:-} ]] || continue
        if [[ $kind == file ]]; then cp -a "$snapdir/files/$id" "$file" || true
        elif [[ $kind == link ]]; then
          rm -f -- "$file" || true
          ln -s -- "$(cat "$snapdir/files/$id")" "$file" || true
        else rm -f -- "$file" || true; fi
      done < "$manifest"
      if nginx -t; then
        systemctl reload nginx.service || true
      fi
    fi
    echo 'gitea funnel: previous listeners restored' >&2
  fi
  if [[ -n ${tmp:-} && -d $tmp ]]; then
    rm -rf -- "$tmp"
  fi
  exit "$rc"
}
# Restore leftover nginx if anything below fails. Installed before cert, GET,
# snapshot, and the MAG_NGINX_TXN copy — those used to abort with no message.
trap cleanup EXIT

allowed_path() {
  local file=$1
  case "$file" in
    "$nginx_root"/*|"$mag_ci_root"/*) return 0 ;;
  esac
  return 1
}

save_nginx() {
  local file=$1 id target
  if [[ -e $snapdir/saved.paths ]] && grep -Fxq -- "$file" "$snapdir/saved.paths"; then
    return 0
  fi
  printf '%s\n' "$file" >> "$snapdir/saved.paths" || {
    echo "gitea funnel: cannot record $file" >&2
    exit 1
  }
  id=$(grep -c . "$snapdir/saved.paths" || true)
  [[ $id =~ ^[1-9][0-9]*$ ]] || { echo "gitea funnel: bad snapshot id for $file" >&2; exit 1; }
  if ! allowed_path "$file"; then
    echo "gitea funnel: refusing to snapshot $file" >&2
    exit 1
  fi
  if [[ -L $file ]]; then
    target=$(readlink -- "$file") || { echo "gitea funnel: cannot read symlink $file" >&2; exit 1; }
    [[ -n $target ]] || { echo "gitea funnel: empty symlink $file" >&2; exit 1; }
    printf '%s\n' "$target" > "$snapdir/files/$id" || {
      echo "gitea funnel: cannot snapshot symlink $file" >&2
      exit 1
    }
    printf '%s\t%s\tlink\n' "$id" "$file" >> "$manifest"
  elif [[ -f $file ]]; then
    cp -a "$file" "$snapdir/files/$id" || {
      echo "gitea funnel: cannot snapshot $file" >&2
      exit 1
    }
    printf '%s\t%s\tfile\n' "$id" "$file" >> "$manifest"
  else
    printf '%s\t%s\tabsent\n' "$id" "$file" >> "$manifest"
  fi
}

publish_snapshot() {
  local dest=${MAG_NGINX_TXN:-} snap
  if [[ -z $dest || ! -d $dest || -L $dest ]]; then
    return 0
  fi
  snap=$dest/funnel-snapshot
  # A leftover dest from a previous attempt used to turn `cp -a snap dest`
  # into dest/snap, so restore looked at an empty tree. Replace dest with
  # snap's contents (trailing /.) so a pre-existing directory cannot nest.
  rm -rf -- "$snap" || { echo "gitea funnel: cannot clear $snap" >&2; exit 1; }
  mkdir -p -- "$snap/files" || { echo "gitea funnel: cannot create $snap" >&2; exit 1; }
  cp -a -- "$snapdir"/. "$snap"/ || {
    echo "gitea funnel: cannot copy snapshot to $snap" >&2
    exit 1
  }
  if [[ ! -f $snap/serve.json || ! -f $snap/manifest || ! -f $snap/saved.paths ]]; then
    echo "gitea funnel: snapshot copy incomplete at $snap" >&2
    exit 1
  fi
  if [[ -e $snap/snap ]]; then
    echo "gitea funnel: snapshot nested unexpectedly at $snap/snap" >&2
    exit 1
  fi
}

api() {
  local method=$1 out=$2 code=0 http arg src=
  shift 2
  if [[ $test_mode == 1 ]]; then
    local store=${MAG_GITEA_FUNNEL_SERVE:?}
    if [[ $method == GET ]]; then
      [[ -f $store ]] || { echo 'gitea funnel: test serve-config missing' >&2; exit 1; }
      cp -a "$store" "$out" || { echo 'gitea funnel: cannot read test serve-config' >&2; exit 1; }
      printf '%s' 200
      return 0
    fi
    if [[ $method == POST ]]; then
      local prev=
      for arg in "$@"; do
        case "$arg" in
          --data-binary@*) src=${arg#--data-binary@} ;;
          --data-binary) prev=data ;;
          *)
            if [[ $prev == data ]]; then
              src=${arg#@}
              prev=
            fi
            ;;
        esac
      done
      [[ -n $src && -f $src ]] || { echo 'gitea funnel: test POST missing body' >&2; exit 1; }
      cp -a "$src" "$store" || { echo 'gitea funnel: cannot write test serve-config' >&2; exit 1; }
      : > "$out"
      printf '%s' 200
      return 0
    fi
    echo "gitea funnel: test serve-config ${method} unsupported" >&2
    exit 1
  fi
  command -v curl >/dev/null || { echo 'gitea funnel: curl missing' >&2; exit 1; }
  http=$(curl -sS --unix-socket "$sock" -o "$out" -w '%{http_code}' -X "$method" "$@" \
    http://local-tailscaled.sock/localapi/v0/serve-config) || code=$?
  if (( code != 0 )); then
    echo "gitea funnel: serve-config ${method} curl exit ${code}" >&2
    exit 1
  fi
  printf '%s' "$http"
}

# Certificate before nginx -t. The gitea vhost cannot load without it.
if [[ $test_mode == 1 ]]; then
  echo "tailscale cert: $node"
else
  bash "$here/issue-tailscale-cert.sh" --no-reload || {
    echo 'gitea funnel: certificate issuance failed' >&2
    exit 1
  }
fi
get_code=$(api GET "$tmp/current") || {
  echo 'gitea funnel: serve-config GET failed' >&2
  exit 1
}
[[ $get_code == 200 ]] || { echo "gitea funnel: serve-config GET HTTP ${get_code}" >&2; exit 1; }
[[ -f $tmp/current ]] || { echo 'gitea funnel: serve-config GET wrote no body' >&2; exit 1; }
cp -a "$tmp/current" "$snapdir/serve.json" || {
  echo 'gitea funnel: cannot snapshot serve-config' >&2
  exit 1
}

# Snapshot first, then move listeners. A failure restores this snapshot,
# including from deploy rollback via restore-gitea-funnel.sh.
# nginx -T is the source of truth: sites-enabled/mag-room.conf is a symlink
# and live bots/pages still sit in conf.d with listen 100.123.114.16:443.
site=$nginx_root/conf.d/mag-git.conf
proxy=$mag_ci_root/gitea-proxy.inc
dump_nginx_t "$tmp/nginx-t"
to_retarget=()
if ! python3 "$here/merge-gitea-funnel.py" candidates \
    "$nginx_root/sites-available" \
    "$nginx_root/sites-enabled" \
    "$nginx_root/conf.d" \
    "$nginx_root/snippets" \
    "$nginx_root/streams-enabled" \
    "$mag_ci_root" <"$tmp/nginx-t" >"$tmp/candidates"
then
  echo 'gitea funnel: candidate discovery failed' >&2
  exit 1
fi
if [[ -s $tmp/candidates ]]; then
  mapfile -t to_retarget < "$tmp/candidates" || {
    echo 'gitea funnel: cannot read candidate list' >&2
    exit 1
  }
fi
for file in "${to_retarget[@]}"; do
  if ! allowed_path "$file"; then
    echo "gitea funnel: refusing to rewrite $file" >&2
    exit 1
  fi
  save_nginx "$file"
done
save_nginx "$site"
save_nginx "$proxy"
publish_snapshot
if [[ $test_mode == 1 && ${MAG_GITEA_FUNNEL_FAIL:-} == after-snapshot ]]; then
  false
fi
if (( ${#to_retarget[@]} )); then
  for file in "${to_retarget[@]}"; do
    # Follow a symlink to the live bytes, then replace the path with a
    # regular file so a stale release tree cannot put nginx back on :443.
    if [[ -e $file || -L $file ]]; then
      mode=$(stat -c %a "$file") || { echo "gitea funnel: cannot stat $file" >&2; exit 1; }
    else
      mode=644
    fi
    python3 "$here/merge-gitea-funnel.py" retarget <"$file" >"$tmp/retargeted" || {
      echo "gitea funnel: cannot retarget $file" >&2
      exit 1
    }
    rm -f -- "$file" || { echo "gitea funnel: cannot replace $file" >&2; exit 1; }
    install -m "$mode" "$tmp/retargeted" "$file" || {
      echo "gitea funnel: cannot install $file" >&2
      exit 1
    }
    echo "gitea funnel: moved $file off 100.123.114.16:443"
  done
fi
run_cmd install -d -m 0755 "$mag_ci_root" || die 1 "install -d $mag_ci_root"
run_cmd install -m 0644 "$root/ops/gitea-ci/gitea-proxy.inc" "$proxy" ||
  die 1 "install gitea-proxy.inc"
run_cmd install -m 0644 "$root/ops/gitea-ci/public.nginx.in" "$site" ||
  die 1 "install mag-git.conf"
if ! run_cmd nginx -t || ! run_cmd systemctl reload nginx.service; then
  die 1 'nginx -t || systemctl reload nginx.service'
fi
# Workers can keep 100.123.114.16:443 for a few seconds after reload.
# A same-tick ss check aborted live deploy; wait up to 15s first.
if command -v ss >/dev/null; then
  ss_tries=${MAG_GITEA_FUNNEL_SS_TRIES:-30}
  ss_sleep=${MAG_GITEA_FUNNEL_SS_SLEEP:-0.5}
  for ((i=1; i<=ss_tries; i++)); do
    ss -ltnH 'sport = :443' | grep -q '100.123.114.16:443' || break
    echo "gitea funnel: waiting for 100.123.114.16:443 to leave ($i/${ss_tries})" >&2
    sleep "$ss_sleep"
  done
fi
# Funnel cannot bind :443 while any managed vhost still listens there.
# nginx -T is required: ss alone misses a listen that failed to leave conf.d.
dump_nginx_t "$tmp/nginx-scan"
if ! python3 "$here/merge-gitea-funnel.py" scan <"$tmp/nginx-scan"; then
  die 1 'nginx still binds 100.123.114.16:443 (scan)'
fi
if command -v ss >/dev/null && ss -ltnH 'sport = :443' | grep -q '100.123.114.16:443'; then
  echo 'gitea funnel: 100.123.114.16:443 is still taken; refusing to enable Funnel' >&2
  grep -n 'listen[[:space:]]\+100\.123\.114\.16:443' "$tmp/nginx-scan" >&2 || true
  die 1 'ss: 100.123.114.16:443 still taken'
fi
keep_nginx=1
if [[ $test_mode != 1 && -s $ini ]]; then
  before=$(sha256sum "$ini")
  bash "$root/ops/gitea-ci/configure-gitea.sh" || die 1 'configure-gitea.sh'
  after=$(sha256sum "$ini")
  if [[ $before != "$after" ]]; then systemctl restart gitea.service; fi
fi
# The old lego timer must not run HTTP-01 for a name Funnel now serves.
systemctl disable --now mag-ci-cert.timer >/dev/null 2>&1 || true
run_cmd install -d -m 0755 "$lib_dir" || die 1 "install -d $lib_dir"
run_cmd install -d -m 0755 "$systemd_dir" || die 1 "install -d $systemd_dir"
run_cmd install -m 0755 "$here/issue-tailscale-cert.sh" "$lib_dir/issue-tailscale-cert.sh" ||
  die 1 'install issue-tailscale-cert.sh'
run_cmd install -m 0644 "$here/mag-tailscale-cert.service" "$systemd_dir/mag-tailscale-cert.service" ||
  die 1 'install mag-tailscale-cert.service'
run_cmd install -m 0644 "$here/mag-tailscale-cert.timer" "$systemd_dir/mag-tailscale-cert.timer" ||
  die 1 'install mag-tailscale-cert.timer'
run_cmd systemctl daemon-reload || die 1 'systemctl daemon-reload'
run_cmd systemctl enable mag-tailscale-cert.timer || die 1 'enable mag-tailscale-cert.timer'
run_cmd systemctl start mag-tailscale-cert.timer || die 1 'start mag-tailscale-cert.timer'

python3 "$here/merge-gitea-funnel.py" merge <"$tmp/current" >"$tmp/merged" ||
  die 1 'serve-config merge failed'
python3 "$here/merge-gitea-funnel.py" check <"$tmp/merged" ||
  die 1 'merged serve-config rejected'
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
  post_code=$(api POST "$tmp/post" -H 'Content-Type: application/json' --data-binary @"$tmp/merged") ||
    die 1 'serve-config POST failed'
  if [[ $post_code != 200 ]]; then
    die 1 "serve-config POST HTTP ${post_code}"
  fi
fi
get_code=$(api GET "$tmp/applied") || die 1 'serve-config reread failed'
[[ $get_code == 200 ]] || die 1 "serve-config reread HTTP ${get_code}"
python3 "$here/merge-gitea-funnel.py" check <"$tmp/applied" ||
  die 1 'applied serve-config rejected'
status=$(tailscale funnel status 2>&1 || true)
sed "s#@ROOT@#$root#g" "$here/mag-gitea-funnel.service" > "$systemd_dir/mag-gitea-funnel.service"
install -m 0644 "$here/mag-gitea-funnel.timer" "$systemd_dir/mag-gitea-funnel.timer"
systemctl daemon-reload
systemctl enable mag-gitea-funnel.service mag-gitea-funnel.timer
systemctl start mag-gitea-funnel.timer
funnel_admin_note() {
  cat >&2 <<'EOF'
Public DNS for mag.tail3e02ae.ts.net is published by Tailscale's control
plane, not by this repo. The node already posts ServeConfig via LocalAPI
(AllowFunnel mag.tail3e02ae.ts.net:443 -> http://127.0.0.1:3000). That is
the same as `tailscale funnel --bg` / `tailscale serve --bg` on :443.
Do not add a second serve on :443.

If `tailscale funnel status` says Funnel on but 8.8.8.8 / 1.1.1.1 still
return NXDOMAIN, the missing piece is Tailscale admin (Owner / Admin /
Network admin), not bootstrap:

1. Access controls → nodeAttrs funnel for this node (or autogroup:member).
   mag-gitea-funnel.timer retries the serve-config; it cannot grant the attr.

"nodeAttrs": [
  {
    "target": ["autogroup:member"],
    "attr": ["funnel"]
  }
]

2. DNS page: MagicDNS ON and HTTPS Certificates ON. Funnel does not
   publish a public A/AAAA until both are on.
3. Wait up to 10 minutes. Flush a cached NXDOMAIN (`dig @8.8.8.8
   mag.tail3e02ae.ts.net A`). Local resolvers can keep NXDOMAIN longer.
EOF
}
if ! grep -Fq 'http://127.0.0.1:3000' <<<"$status" || ! grep -Eq 'Funnel on|Available on the internet' <<<"$status"; then
  echo 'gitea funnel: public Funnel is not active, so mag.tail3e02ae.ts.net has no public DNS' >&2
  echo 'nginx is off 100.123.114.16:443 and the serve config points :443 at http://127.0.0.1:3000.' >&2
  funnel_admin_note
  if [[ ${MAG_GITEA_FUNNEL_REQUIRE_PUBLIC:-0} == 1 ]]; then
    die 1 'public Funnel is not active'
  fi
  echo "gitea funnel: ${origin}/ waiting for funnel nodeAttr"
  exit 0
fi
# Funnel on still yields NXDOMAIN until Tailscale publishes the name.
dns_out=
if [[ $test_mode == 1 && -n ${MAG_GITEA_FUNNEL_DIG:-} ]]; then
  dns_out=$("${MAG_GITEA_FUNNEL_DIG}" "$node" 2>/dev/null || true)
elif command -v dig >/dev/null; then
  dns_out=$(dig +time=2 +tries=1 +short A "$node" @8.8.8.8 2>/dev/null || true)
  dns_out+=$'\n'$(dig +time=2 +tries=1 +short AAAA "$node" @8.8.8.8 2>/dev/null || true)
fi
if [[ -z ${dns_out//[$' \t\n']/} || $dns_out == *NXDOMAIN* ]]; then
  echo "gitea funnel: public DNS ${node} is NXDOMAIN (Funnel is on)" >&2
  funnel_admin_note
  if [[ ${MAG_GITEA_FUNNEL_REQUIRE_PUBLIC:-0} == 1 ]]; then
    die 1 "public DNS ${node} is NXDOMAIN"
  fi
else
  echo "gitea funnel: public DNS ${node} $(tr '\n' ' ' <<<"$dns_out")"
fi
echo "gitea funnel: ${origin}/"
