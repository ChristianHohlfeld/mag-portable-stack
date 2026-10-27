#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# deploy.sh runs deploy-nginx.sh stage (nginx -t) BEFORE
# /opt/mag-stack/current points at the new release. The room include must
# not depend on current. This test uses the real include line and a real
# nginx -t. It does not sed-rewrite mag-tailnet-ok.inc onto the repo file.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
command -v nginx >/dev/null
command -v openssl >/dev/null
room="$root/ops/private-access/mag-room.nginx.conf"
deploy="$root/ops/private-access/deploy-nginx.sh"
inc="$root/ops/nginx/mag-tailnet-ok.inc"
grep -Fq 'include /etc/nginx/snippets/mag-tailnet-ok.inc;' "$room"
! grep -F 'include /opt/mag-stack/current/ops/nginx/mag-tailnet-ok.inc;' "$room"
grep -Fq 'snippet=/etc/nginx/snippets/mag-tailnet-ok.inc' "$deploy"
grep -Fq 'snippet_src="$here/../nginx/mag-tailnet-ok.inc"' "$deploy"
python3 - "$deploy" <<'PY'
import pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text()
start = text.index("\nstage(){")
end = text.index("\nactivate(){")
stage = text[start:end]
for needle in (
    '[[ -f $snippet_src ]]',
    'install -m 0644 "$snippet_src"',
    'install -m 0644 "$template"',
    "nginx -t || die 'Candidate nginx -t failed",
):
    if needle not in stage:
        raise SystemExit(f"stage is missing {needle!r}")
if stage.index('install -m 0644 "$snippet_src"') > stage.index("nginx -t || die 'Candidate nginx -t failed"):
    raise SystemExit("stage runs nginx -t before installing the snippet")
if stage.index('install -m 0644 "$snippet_src"') > stage.index('install -m 0644 "$template"'):
    raise SystemExit("stage copies the vhost before the snippet")
PY

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
old="$tmp/releases/20261027T083709Z"
new="$tmp/releases/20261027T092300Z"
mkdir -p "$old/ops/nginx" "$new/ops/nginx" "$tmp/opt/mag-stack" \
  "$tmp/snippets" "$tmp/body" "$tmp/proxy" "$tmp/fastcgi" "$tmp/uwsgi" "$tmp/scgi"
cp "$root/ops/nginx/mag-speech.inc" "$old/ops/nginx/"
cp "$root/ops/nginx/mag-speech.inc" "$new/ops/nginx/"
cp "$inc" "$new/ops/nginx/"
ln -sfn "$old" "$tmp/opt/mag-stack/current"
[[ ! -e $tmp/opt/mag-stack/current/ops/nginx/mag-tailnet-ok.inc ]]
[[ -f $tmp/opt/mag-stack/current/ops/nginx/mag-speech.inc ]]

openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$tmp/key.pem" -out "$tmp/cert.pem" \
  -subj '/CN=mag.stardenburg.de' >/dev/null 2>&1

write_nginx() {
  local include_path=$1
  cat >"$tmp/site.conf" <<EOF
include $include_path;
server {
    listen 127.0.0.1:18443 ssl;
    server_name mag.stardenburg.de;
    ssl_certificate $tmp/cert.pem;
    ssl_certificate_key $tmp/key.pem;
    if (\$mag_tailnet_ok = 0) { return 403; }
    location / { return 200; }
}
EOF
  cat >"$tmp/nginx.conf" <<EOF
worker_processes 1;
pid $tmp/nginx.pid;
error_log $tmp/nginx.err;
events { worker_connections 64; }
http {
  access_log off;
  client_body_temp_path $tmp/body;
  proxy_temp_path $tmp/proxy;
  fastcgi_temp_path $tmp/fastcgi;
  uwsgi_temp_path $tmp/uwsgi;
  scgi_temp_path $tmp/scgi;
  include $tmp/site.conf;
}
EOF
}

# Bug: include via current while current is the previous release.
write_nginx "$tmp/opt/mag-stack/current/ops/nginx/mag-tailnet-ok.inc"
if nginx -t -c "$tmp/nginx.conf" -p "$tmp/" >/dev/null 2>"$tmp/current.err"; then
  echo "nginx -t via current must fail before the release switch" >&2
  exit 1
fi
grep -E 'mag-tailnet-ok.inc|unknown "mag_tailnet_ok"' "$tmp/current.err" >/dev/null

# Fix: install the snippet from the NEW release, current still old.
install -m 0644 "$new/ops/nginx/mag-tailnet-ok.inc" "$tmp/snippets/mag-tailnet-ok.inc"
[[ ! -e $tmp/opt/mag-stack/current/ops/nginx/mag-tailnet-ok.inc ]]
write_nginx "$tmp/snippets/mag-tailnet-ok.inc"
nginx -t -c "$tmp/nginx.conf" -p "$tmp/" >/dev/null
# Production include line is the snippets path, not a sed rewrite to the repo.
grep -Fq 'include /etc/nginx/snippets/mag-tailnet-ok.inc;' "$room"
echo "nginx stage include: current-before-switch fails; snippet-from-new-release passes"
