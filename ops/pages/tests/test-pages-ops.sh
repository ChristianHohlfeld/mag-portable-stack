#!/usr/bin/env bash
# Offline regression: lego 5 CLI, ACME-DNS onboarding, public listener opt-in.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for tool in jq nginx openssl; do command -v "$tool" >/dev/null; done
cat >"$tmp/pages.env" <<EOF
PAGES_ZONE=pages.stardenburg.de
PAGES_TAILNET_BIND=100.123.114.16
PAGES_PUBLIC_BIND=
PAGES_PUBLIC_DNS=
PAGES_ACME_EMAIL=test@example.org
EOF
cat >"$tmp/acme.env" <<EOF
ACME_DNS_API_BASE=https://auth.acme-dns.io
ACME_DNS_STORAGE_PATH=$tmp/storage.json
EOF
cat >"$tmp/lego" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" >>"$PAGES_TEST_ARGS_FILE"
printf '\n' >>"$PAGES_TEST_ARGS_FILE"
[[ "$1" == run ]] || exit 89
if [[ ! -s "$ACME_DNS_STORAGE_PATH" ]]; then
  cat >"$ACME_DNS_STORAGE_PATH" <<'JSON'
{"*.pages.stardenburg.de":{"fulldomain":"a1234567-1234-1234-1234-1234567890ab.auth.acme-dns.io"}}
JSON
fi
exit 80
EOF
cat >"$tmp/dig" <<'EOF'
#!/usr/bin/env bash
[[ -e "$PAGES_TEST_DNS_FILE" ]] && cat "$PAGES_TEST_DNS_FILE"
exit 0
EOF
chmod +x "$tmp/lego" "$tmp/dig"
export MAG_PAGES_TESTING=1
export MAG_PAGES_CONFIG="$tmp/pages.env" MAG_PAGES_ACME_ENV="$tmp/acme.env"
export MAG_PAGES_LEGO="$tmp/lego" MAG_PAGES_LEGO_DIR="$tmp/lego-dir"
export MAG_PAGES_TLS_DIR="$tmp/tls" MAG_PAGES_DIG="$tmp/dig"
export PAGES_TEST_ARGS_FILE="$tmp/args" PAGES_TEST_DNS_FILE="$tmp/dns"
set +e
"$here/issue-wildcard.sh" run >"$tmp/output" 2>&1
rc=$?
set -e
[[ $rc -eq 78 ]] || { cat "$tmp/output"; echo "Expected onboarding 78 got $rc"; exit 1; }
grep -Fq 'CNAME  _acme-challenge.pages  a1234567-1234-1234-1234-1234567890ab.auth.acme-dns.io.' "$tmp/output"
jq -e '."pages.stardenburg.de".fulldomain == .["*.pages.stardenburg.de"].fulldomain' "$tmp/storage.json" >/dev/null
grep -Fq 'dig +short CNAME _acme-challenge.pages.stardenburg.de @1.1.1.1' "$tmp/output"
[[ "$(head -1 "$tmp/args")" == run ]]
! grep -E '^renew$|^--days$' "$tmp/args"
grep -Fxq -- '--accept-tos' "$tmp/args"
grep -Fxq -- '--dns' "$tmp/args"
grep -Fxq -- 'acmedns' "$tmp/args"
grep -Fxq -- '*.pages.stardenburg.de' "$tmp/args"
grep -Fxq -- 'pages.stardenburg.de' "$tmp/args"
# With registered credentials but no CNAME, never invoke lego again.
lines="$(wc -l < "$tmp/args")"
set +e
"$here/issue-wildcard.sh" renew >"$tmp/output" 2>&1
rc=$?
set -e
[[ $rc -eq 78 && "$(wc -l < "$tmp/args")" -eq "$lines" ]]
# After matching DNS, the v5 command runs (mock exits 80; no cert is installed).
printf 'a1234567-1234-1234-1234-1234567890ab.auth.acme-dns.io.\n' >"$tmp/dns"
set +e
"$here/issue-wildcard.sh" run >"$tmp/output" 2>&1
rc=$?
set -e
[[ $rc -eq 80 ]] || { cat "$tmp/output"; exit 1; }
[[ "$(wc -l < "$tmp/args")" -gt "$lines" ]]

# Bash -n accepts a SINGLE script; "bash -n ops/pages/*.sh" checks only
# the first globbed file. Parse the actual production verifier explicitly.
bash -n "$here/verify.sh"
grep -Fq 'cert_public_key_hash=' "$here/verify.sh"
grep -Fq 'private_key_public_hash=' "$here/verify.sh"
# PRIVATE ONLY: no port 80, no public listener, exactly Tailnet 443.
"$here/render-nginx.sh" >"$tmp/private.conf"
grep -Fq 'listen 100.123.114.16:443 ssl;' "$tmp/private.conf"
# Exact apex vhost is separate from the repo-wildcard regex:
# users can open https://pages.stardenburg.de/ without creating a repo.
grep -Fq 'server_name pages.stardenburg.de;' "$tmp/private.conf"
grep -Fq 'location = /healthz' "$tmp/private.conf"
grep -Fq 'root /var/lib/mag-pages/portal;' "$tmp/private.conf"
test -s "$here/index.html"
grep -Fq '<h1>MAG Pages</h1>' "$here/index.html"
# Pages-only activation must check native ABI and never call generic deploy.
grep -Fq 'pages_set_visibility' "$here/activate.sh"
grep -Fq 'bash "$here/issue-wildcard.sh" run' "$here/activate.sh"
grep -Fq 'bash "$here/install.sh"' "$here/activate.sh"
grep -Fq 'bash "$here/verify.sh"' "$here/activate.sh"
! grep -Eq '^[[:space:]]*(systemctl[[:space:]]+(start|enable|restart)[[:space:]].*mag-backup|restic[[:space:]]|aws[[:space:]])' "$here/activate.sh"
grep -Fq 'systemctl restart mag-120.service' "$here/activate.sh"
grep -Fq 'expected=1e915301-9bda-4d2d-b261-6c20c635aca4.auth.acme-dns.io' "$here/verify.sh"
[[ "$(grep -Fc 'listen 100.123.114.16:443 ssl;' "$tmp/private.conf")" -eq 2 ]]
grep -Fq 'server_name ~^(?<pageslug>[a-z0-9-]+)\.pages\.stardenburg\.de$;' "$tmp/private.conf"
! grep -q ':80;' "$tmp/private.conf"
! grep -q '@PUBLIC_BIND@' "$tmp/private.conf"
! grep -q '192.0.2.10' "$tmp/private.conf"
# PUBLIC OPT-IN: listener only on explicit public bind, private listener stays.
cat >>"$tmp/pages.env" <<'EOF'
PAGES_PUBLIC_BIND=127.0.0.2
PAGES_PUBLIC_DNS=127.0.0.2
EOF
"$here/render-nginx.sh" >"$tmp/public.conf"
grep -Fq 'listen 127.0.0.2:80;' "$tmp/public.conf"
grep -Fq 'listen 127.0.0.2:443 ssl;' "$tmp/public.conf"
grep -Fq 'listen 100.123.114.16:443 ssl;' "$tmp/public.conf"
# A set DNS IP alone must not enable public listener.
cat >>"$tmp/pages.env" <<'EOF'
PAGES_PUBLIC_BIND=
EOF
if "$here/render-nginx.sh" >"$tmp/should-fail" 2>/dev/null; then
  echo 'Accepted orphan public DNS without public bind' >&2; exit 1
fi
# nginx -t on loopback high ports with an ephemeral cert, without host IPs.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$tmp/key.pem" -out "$tmp/cert.pem" \
  -subj '/CN=pages.stardenburg.de' >/dev/null 2>&1
for mode in private public; do
 sed -e 's/100.123.114.16/127.0.0.3/g' \
     -e 's/:80;/:18080;/g' -e 's/:443 ssl;/:18443 ssl;/g' \
     -e "s|/etc/mag-pages/tls/current/cert.pem|$tmp/cert.pem|g" \
     -e "s|/etc/mag-pages/tls/current/key.pem|$tmp/key.pem|g" \
     "$tmp/$mode.conf" >"$tmp/$mode-test.conf"
 printf 'worker_processes 1;\npid %s/nginx.pid;\nerror_log stderr;\nevents { worker_connections 64; }\nhttp { access_log off; include %s/%s-test.conf; }\n' \
   "$tmp" "$tmp" "$mode" >"$tmp/nginx.conf"
 nginx -t -p "$tmp/" -c "$tmp/nginx.conf" >/dev/null
done
# Existing token path is used: never create/replace a PAT in Pages scripts.
grep -Fq '/etc/mag-pages/gitea-token' "$here/install.sh"
! grep -q 'cat > /etc/mag-pages/gitea-token' "$here/install.sh"
echo 'Pages shell regression OK: lego v5, DNS registration, private/public nginx, PAT'
