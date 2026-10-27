#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Offline checks for per-bot tailnet names. No root, no lego, no DNS writes.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
repo=$(cd "$here/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for tool in openssl python3 nginx g++; do command -v "$tool" >/dev/null; done

chmod +x "$here/site.sh" "$here/issue-wildcard.sh" "$here/install.sh" \
  "$repo/ops/magbot-agent-ctl"
bash -n "$here/site.sh" "$here/issue-wildcard.sh" "$here/install.sh" "$repo/ops/magbot-agent-ctl"

# Lifecycle wrapper assigns after spawn and removes only after a successful despawn.
cat >"$tmp/magbot" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >>"$tmp/log"
if [[ \$1 == file-write ]]; then
  mkdir -p "$tmp/guest"
  cat >"$tmp/guest/\$(basename "\$3")"
fi
if [[ \$1 == status ]]; then
  printf '%-33s %s\n' agent1 ready
fi
if [[ \$1 == despawn && -f $tmp/fail-despawn ]]; then exit 1; fi
exit 0
EOF
chmod +x "$tmp/magbot"
export MAG_BOTS_ROOT="$tmp/root" MAG_BOTS_MAGBOT="$tmp/magbot" MAG_BOTS_SITE="$here/site.sh"
"$repo/ops/magbot-agent-ctl" spawn agent1
[[ -s $tmp/root/www/agent1/index.html ]]
[[ $(stat -c %a "$tmp/root/tokens/agent1") == 600 ]]
grep -Fq 'agent1.bot.stardenburg.de' "$tmp/root/www/agent1/index.html"
grep -Fq 'https://agent1.bot.stardenburg.de' "$tmp/guest/SITE.md"
bash -n "$tmp/guest/publish-site"
grep -Fq 'tar --format=ustar -C /home/bot/www' "$tmp/guest/publish-site"
grep -Fq 'http://192.168.122.1:8091/publish' "$tmp/guest/publish-site"
! grep -Fq "$(cat "$tmp/root/tokens/agent1")" "$tmp/guest/SITE.md"
! grep -Fq "$(cat "$tmp/root/tokens/agent1")" "$tmp/guest/publish-site"
printf 'CUSTOM\n' >"$tmp/root/www/agent1/index.html"
MAG_BOTS_SITE="$here/site.sh" "$repo/ops/magbot-agent-ctl" spawn agent1
grep -Fxq CUSTOM "$tmp/root/www/agent1/index.html"
head -1 "$tmp/log" | grep -qx 'spawn agent1'
touch "$tmp/fail-despawn"
set +e
"$repo/ops/magbot-agent-ctl" despawn agent1
rc=$?
set -e
[[ $rc -ne 0 && -f $tmp/root/www/agent1/index.html ]]
rm -f "$tmp/fail-despawn"
"$repo/ops/magbot-agent-ctl" despawn agent1
[[ ! -e $tmp/root/www/agent1 && ! -e $tmp/root/tokens/agent1 ]]
# Despawn deletes nothing: the published site moves to the root-only archive.
archived=("$tmp"/root/archive/agent1-*)
[[ ${#archived[@]} -eq 1 && $(cat "${archived[0]}/index.html") == CUSTOM ]]
[[ $(stat -c %a "$tmp/root/archive") == 700 ]]
for bad in agent agentX ../etc 'agent1/../agent2' 'agent 1'; do
  set +e
  "$here/site.sh" assign "$bad" >"$tmp/bad.out" 2>&1
  rc=$?
  set -e
  [[ $rc -ne 0 ]]
done
mkdir -p "$tmp/root/www" "$tmp/secret"
echo keep >"$tmp/secret/keep"
ln -s "$tmp/secret" "$tmp/root/www/agent4"
set +e
"$here/site.sh" remove agent4 >"$tmp/bad.out" 2>&1
rc=$?
set -e
[[ $rc -ne 0 && -L $tmp/root/www/agent4 && -f $tmp/secret/keep ]]
rm "$tmp/root/www/agent4"
set +e
"$repo/ops/magbot-agent-ctl" spawn 'agent1;rm' >"$tmp/bad.out" 2>&1
rc=$?
set -e
[[ $rc -eq 64 ]]
"$here/site.sh" backfill
[[ -s $tmp/root/www/agent1/index.html ]]
[[ ! -e $tmp/root/www/agent2 ]]

# Renewal uses the existing wildcard and never registers a second account.
make_cert() {
  local days=$1 dir=$2
  mkdir -p "$dir/certificates"
  openssl req -x509 -newkey rsa:2048 -nodes -days "$days" \
    -keyout "$dir/certificates/_.bot.stardenburg.de.key" \
    -out "$dir/certificates/_.bot.stardenburg.de.crt" \
    -subj '/CN=*.bot.stardenburg.de' \
    -addext 'subjectAltName=DNS:*.bot.stardenburg.de' >/dev/null 2>&1
}
make_cert 40 "$tmp/lego"
cat >"$tmp/lego-bin" <<EOF
#!/bin/bash
printf '%s\n' "\$@" >>"$tmp/lego.args"
exit 99
EOF
chmod +x "$tmp/lego-bin"
export MAG_BOTS_TESTING=1
export MAG_BOTS_LEGO_DIR="$tmp/lego" MAG_BOTS_TLS_DIR="$tmp/tls"
export MAG_BOTS_LEGO="$tmp/lego-bin" MAG_BOTS_ACME_STORAGE="$tmp/missing-storage.json"
export MAG_BOTS_ACME_ENV="$tmp/no-such-env" MAG_BOTS_DIG="$tmp/dig"
"$here/issue-wildcard.sh" renew >"$tmp/renew.out" 2>&1
grep -Fq 'renewal not due' "$tmp/renew.out"
[[ -s $tmp/tls/current/cert.pem && -s $tmp/tls/current/key.pem ]]
[[ ! -e $tmp/lego.args ]]
grep -Fq 'DNS:*.bot.stardenburg.de' <<<"$(openssl x509 -in "$tmp/tls/current/cert.pem" -noout -ext subjectAltName)"

rm -rf "$tmp/lego" "$tmp/tls"
export MAG_BOTS_LEGO_DIR="$tmp/lego-none"
"$here/issue-wildcard.sh" renew >"$tmp/renew.out" 2>&1
grep -Fq 'skip registration' "$tmp/renew.out"
[[ ! -e $tmp/lego.args && ! -e $tmp/tls/current ]]

cat >"$tmp/storage.json" <<'JSON'
{"mag.stardenburg.de":{"fulldomain":"fb263606-8d4e-4b03-be14-43bb9313382a.auth.acme-dns.io","password":"secret"}}
JSON
cp "$tmp/storage.json" "$tmp/storage.before"
export MAG_BOTS_ACME_STORAGE="$tmp/storage.json"
"$here/issue-wildcard.sh" renew >"$tmp/renew.out" 2>&1
grep -Fq 'skip registration' "$tmp/renew.out"
cmp "$tmp/storage.json" "$tmp/storage.before"
[[ ! -e $tmp/lego.args ]]

python3 - <<PY
import json
p="$tmp/storage.json"
data=json.load(open(p))
data["*.bot.stardenburg.de"]={"fulldomain":"b3462ee7-55f9-4a5e-bf0e-aa216eb10db8.auth.acme-dns.io","password":"secret"}
json.dump(data, open(p,"w"))
PY
cp "$tmp/storage.json" "$tmp/storage.before"
printf 'other.auth.acme-dns.io\n' >"$tmp/dns"
cat >"$tmp/dig" <<EOF
#!/bin/bash
cat "$tmp/dns"
EOF
chmod +x "$tmp/dig"
set +e
"$here/issue-wildcard.sh" renew >"$tmp/renew.out" 2>&1
rc=$?
set -e
[[ $rc -eq 78 ]]
grep -Fq 'CNAME  _acme-challenge.bot  b3462ee7-55f9-4a5e-bf0e-aa216eb10db8.auth.acme-dns.io.' "$tmp/renew.out"
cmp "$tmp/storage.json" "$tmp/storage.before"
[[ ! -e $tmp/lego.args ]]
printf 'b3462ee7-55f9-4a5e-bf0e-aa216eb10db8.auth.acme-dns.io.\n' >"$tmp/dns"
set +e
"$here/issue-wildcard.sh" run >"$tmp/renew.out" 2>&1
rc=$?
set -e
[[ $rc -eq 99 ]]
grep -Fxq -- '*.bot.stardenburg.de' "$tmp/lego.args"
grep -Fxq -- '--dns' "$tmp/lego.args"
grep -Fxq acmedns "$tmp/lego.args"
! grep -Fxq -- '--email' "$tmp/lego.args"
! grep -Fxq -- 'bot.stardenburg.de' "$tmp/lego.args"
[[ $(grep -c 'bot.stardenburg.de' "$tmp/lego.args") -eq 1 ]]

# nginx accepts the tailnet vhost and the template has no public bind.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$tmp/key.pem" -out "$tmp/cert.pem" \
  -subj '/CN=*.bot.stardenburg.de' >/dev/null 2>&1
sed -e 's|@ZONE_REGEX@|bot\\.stardenburg\\.de|g' \
    -e "s|/etc/mag-bots/tls/current/cert.pem|$tmp/cert.pem|g" \
    -e "s|/etc/mag-bots/tls/current/key.pem|$tmp/key.pem|g" \
    -e 's|127.0.0.1:4443|127.0.0.1:18443|' \
    "$here/mag-bots.nginx.in" >"$tmp/bots.conf"
grep -Fq 'listen 127.0.0.1:4443 ssl http2 proxy_protocol;' "$here/mag-bots.nginx.in"
grep -Fq 'listen 127.0.0.1:18443 ssl http2 proxy_protocol;' "$tmp/bots.conf"
grep -Fq 'server_name ~^(?<botslug>agent[0-9]+)\.bot\.stardenburg\.de$;' "$tmp/bots.conf"
! grep -q '0.0.0.0' "$here/mag-bots.nginx.in"
! grep -q ':80' "$here/mag-bots.nginx.in"
# Same address:port as the room vhost. Mismatched listen options are an emerg.
cat >"$tmp/room.conf" <<EOF
server {
    listen 127.0.0.1:18443 ssl http2 proxy_protocol;
    server_name mag.stardenburg.de;
    ssl_certificate $tmp/cert.pem;
    ssl_certificate_key $tmp/key.pem;
    location / { return 200; }
}
EOF
mkdir -p "$tmp/body" "$tmp/proxy" "$tmp/fastcgi" "$tmp/uwsgi" "$tmp/scgi"
printf 'pid %s/nginx.pid;\nerror_log %s/nginx.err;\nevents { worker_connections 64; }\nhttp { access_log off; client_body_temp_path %s/body; proxy_temp_path %s/proxy; fastcgi_temp_path %s/fastcgi; uwsgi_temp_path %s/uwsgi; scgi_temp_path %s/scgi; include %s/bots.conf; include %s/room.conf; }\n' \
  "$tmp" "$tmp" "$tmp" "$tmp" "$tmp" "$tmp" "$tmp" "$tmp" "$tmp" >"$tmp/nginx.conf"
nginx -t -p "$tmp/" -c "$tmp/nginx.conf" >/dev/null

# Publisher: bridge token, tar replace, traversal rejected, previous site kept.
token=$(cat "$tmp/root/tokens/agent1")
export MAG_BOTS_PUBLISH_BIND=127.0.0.1 MAG_BOTS_PUBLISH_PORT=18091
g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -o "$tmp/publishd" "$here/publishd.cpp"
mkdir -p "$tmp/pages/nested"
printf '<p>tar</p>\n' >"$tmp/pages/index.html"
printf 'ok\n' >"$tmp/pages/nested/note.txt"
tar --format=ustar -C "$tmp/pages" -cf "$tmp/pages.tar" .
"$tmp/publishd" >"$tmp/publish.log" 2>&1 &
pub=$!
cleanup_pub() { kill "$pub" 2>/dev/null || true; wait "$pub" 2>/dev/null || true; }
trap 'cleanup_pub; rm -rf "$tmp"' EXIT
python3 - <<PY
import io, json, os, tarfile, time, urllib.error, urllib.request
root = "$tmp/root"
token = open(os.path.join(root, "tokens", "agent1"), encoding="ascii").read().strip()
url = "http://127.0.0.1:18091/publish"

def tar_of(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        for name, data in files:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()

def post(bot, bearer, payload):
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "X-Mag-Bot": bot,
        "Authorization": "Bearer " + bearer,
        "Content-Type": "application/x-tar",
    })
    try:
        with urllib.request.urlopen(req, timeout=2) as res:
            return res.status
    except urllib.error.HTTPError as exc:
        return exc.code

deadline = time.time() + 5
while True:
    try:
        post("nope", "x", b"")
        break
    except Exception:
        if time.time() > deadline:
            raise
        time.sleep(0.05)
assert post("agent1", "0"*64, tar_of([("index.html", b"x")])) == 403
assert post("agent2", token, tar_of([("index.html", b"x")])) == 403
code = post("agent1", token, tar_of([("index.html", b"<p>neu</p>"), ("a/b.txt", b"ok")]))
assert code == 204, code
assert open(os.path.join(root, "www", "agent1", "index.html"), "rb").read() == b"<p>neu</p>"
assert open(os.path.join(root, "www", "agent1", "a", "b.txt"), "rb").read() == b"ok"
evil = tar_of([("index.html", b"nope"), ("../../outside.txt", b"pwn")])
assert post("agent1", token, evil) == 400
assert not os.path.exists("$tmp/outside.txt")
assert open(os.path.join(root, "www", "agent1", "index.html"), "rb").read() == b"<p>neu</p>"
blob = open("$tmp/pages.tar", "rb").read()
assert post("agent1", token, blob) == 204, "ustar"
assert open(os.path.join(root, "www", "agent1", "index.html"), "rb").read() == b"<p>tar</p>\n"
assert open(os.path.join(root, "www", "agent1", "nested", "note.txt"), "rb").read() == b"ok\n"
print("publish ok")
PY
cleanup_pub
trap 'rm -rf "$tmp"' EXIT

# Shipped wiring. The site sentence stays out of the cached VM_RULES prefix.
grep -Fq 'https://{vm}.bot.stardenburg.de' "$repo/mag-room/native/magroom.cpp"
grep -Fq 'bash /home/bot/publish-site' "$repo/mag-room/native/magroom.cpp"
grep -Fq 'bot_site_ready' "$repo/mag-room/native/magroom.cpp"
! awk '/static const char \*VM_RULES/,/static const char \*CHAT_ONLY_RULES/' "$repo/mag-room/native/magroom.cpp" | grep -q bot.stardenburg
grep -Fq 'ops/bots/install.sh' "$repo/ops/deploy.sh"
grep -Fq 'mag-bots-publish' "$repo/ops/deploy.sh"
# update.sh and bootstrap.sh build the shared target list, which includes the publisher.
(source "$repo/ops/build-targets.sh"; [[ " ${MAG_BUILD_TARGETS[*]} " == *" mag-bots-publish "* && " ${MAG_RELEASE_BINARIES[*]} " == *" mag-bots-publish "* ]])
for f in update.sh bootstrap.sh; do
  grep -Fq 'source "$ROOT/ops/build-targets.sh"' "$repo/ops/$f"
  grep -Fq -- '--target "${MAG_BUILD_TARGETS[@]}"' "$repo/ops/$f"
done
grep -Fq 'add_executable(mag-bots-publish ops/bots/publishd.cpp)' "$repo/CMakeLists.txt"
[[ ! -e $here/publishd.py ]]
grep -Fq 'tcp dport { 53, 3001, 8767, 8091, 8888, 8889 }' "$repo/ops/mag-agents.nft"
grep -Fq 'tcp dport { 8767, 8091 }' "$repo/ops/magbot-netmode"
grep -Fq 'ExecStart=/usr/local/lib/mag-bots/mag-bots-publish' "$here/mag-bots-publish.service"
! grep -q python "$here/mag-bots-publish.service"
grep -Fq '/var/lib/mag-bots/ready' "$here/install.sh"
grep -Fq 'firewall.sh' "$here/install.sh"
grep -Fq 'ExecStartPost=-/usr/local/lib/mag-bots/issue-wildcard.sh renew' "$here/install.sh"
# The repo copy is 0644 (git); install.sh must not depend on an exec bit.
grep -Fxq 'bash "$here/issue-wildcard.sh" renew' "$here/install.sh"
grep -Fq 's|@ZONE_REGEX@|bot\\.stardenburg\\.de|g' "$here/install.sh"
! grep -R -E 'systemctl (restart|stop).*mag-llm' "$here" "$repo/ops/magbot-agent-ctl" "$repo/ops/deploy.sh"
"$here/install.sh" >"$tmp/install.out" 2>&1
grep -Fq 'not root' "$tmp/install.out"
echo "bot site regression OK"
