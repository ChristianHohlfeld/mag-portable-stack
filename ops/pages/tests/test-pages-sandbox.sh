#!/usr/bin/env bash
# Host-like systemd isolation regression: root cannot write /etc inside
# ProtectSystem=full, but fixed systemctl start delegates to a distinct
# root oneshot. No network, ACME DNS, Gitea, or long-lived process needed.
set -euxo pipefail
trap 'echo "Pages sandbox failure at line $LINENO" >&2; systemctl status mag-pages-reconcile.service nginx.service --no-pager -l || true; journalctl -u mag-pages-reconcile.service -u mag-pages-ci-readonly -u mag-pages-ci-delete -n 50 --no-pager || true' ERR
[[ "$EUID" == 0 ]] || { echo 'Run this CI test with sudo' >&2; exit 1; }
here="$(cd "$(dirname "$0")/../../.." && pwd)"
command -v systemd-run >/dev/null
command -v nginx >/dev/null
command -v jq >/dev/null
command -v visudo >/dev/null
id magbot >/dev/null 2>&1 || useradd -r -M -s /usr/sbin/nologin magbot
tmp="$(mktemp -d /var/lib/mag-pages-sandbox-XXXXXXXX)"
testunit=/run/systemd/system/mag-pages-reconcile.service
sudoers=/etc/sudoers.d/mag-pages-ci
nginxfile=/etc/nginx/conf.d/mag-pages-ci.conf
cleanup() {
 rm -f "$sudoers" "$testunit" "$nginxfile"
 systemctl daemon-reload
 rm -rf "$tmp"
}
trap cleanup EXIT
mkdir -p "$tmp/state/releases" "$tmp/state/tailnet" "$tmp/state/public" "$tmp/state/custom" \
 "$tmp/tls/custom" "$tmp/tls/releases" "$tmp/etc"
chown -R magbot:magbot "$tmp/state"
chmod 0755 "$tmp" "$tmp/state" "$tmp/etc"
printf 'wildcard' > "$tmp/tls/releases/wild.pem"
printf 'wildcard' > "$tmp/tls/current"
host=www.example.org
id="$(printf %s "$host" | sha256sum | cut -c1-16)"
mkdir -p "$tmp/state/releases/site--magbot-agent1/abc123"
printf '<html>Hi</html>' >"$tmp/state/releases/site--magbot-agent1/abc123/index.html"
ln -s "$tmp/state/releases/site--magbot-agent1/abc123" "$tmp/state/tailnet/site--magbot-agent1"
ln -s "$tmp/state/releases/site--magbot-agent1/abc123" "$tmp/state/public/site--magbot-agent1"
ln -s "$tmp/state/releases/site--magbot-agent1/abc123" "$tmp/state/custom/$host"
printf '{"magbot-agent1/site":{"sha":"abc123","slug":"site--magbot-agent1"}}\n' >"$tmp/state/sites.json"
printf '%s\tsite--magbot-agent1.pages.stardenburg.de\tsite--magbot-agent1\n' "$host" >"$tmp/state/custom-domains.tsv"
printf '{"magbot-agent1/site":"public"}\n' >"$tmp/etc/visibility.json"
printf 'PAGES_PUBLIC_BIND=\n' >"$tmp/etc/pages.env"
chmod 0644 "$tmp/etc/"*
printf '# Test-only custom Pages nginx snippet\n' > "$nginxfile"
cat >"$tmp/refresh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
[[ "\$1" == custom-only ]]
cp "$tmp/state/custom-domains.tsv" "$tmp/state/nginx-map"
if [[ -s "$tmp/state/custom-domains.tsv" ]]; then
  # Deterministic fake lego run: assert the root service issues immediately.
  install -d -m 0700 "$tmp/tls/custom/$id/accounts"
  printf 'LEGO_ACCOUNT' >"$tmp/tls/custom/$id/accounts/account.json"
  printf 'KEY' >"$tmp/tls/custom/$id/key.pem"
  printf 'issued' >"$tmp/state/issued-immediately"
fi
rm -f "$nginxfile"
nginx -t
systemctl reload nginx.service
EOF
chmod 0755 "$tmp/refresh"
cat > "$testunit" <<EOF
[Unit]
Description=CI MAG Pages outside read-only Room sandbox
After=nginx.service
[Service]
Type=oneshot
User=root
Environment=MAG_PAGES_TESTING=1
Environment=MAG_PAGES_ROOT=$tmp/state
Environment=MAG_PAGES_POLICY_SCRIPT=$here/ops/pages/pages-policy.sh
Environment=MAG_PAGES_GC_SCRIPT=$here/ops/pages/pages-gc.sh
Environment=MAG_PAGES_POLICY_FILE=$tmp/etc/visibility.json
Environment=MAG_PAGES_CONFIG_FILE=$tmp/etc/pages.env
Environment=MAG_PAGES_GC_ROOT=$tmp/state
Environment=MAG_PAGES_GC_VISIBILITY=$tmp/etc/visibility.json
Environment=MAG_PAGES_GC_TLS=$tmp/tls
Environment=MAG_PAGES_GC_REFRESH=$tmp/refresh
ExecStart=/bin/bash $here/ops/pages/pages-reconcile.sh
TimeoutStartSec=120
EOF
printf '%s\n' 'magbot ALL=(root) NOPASSWD: /usr/bin/systemctl start mag-pages-reconcile.service' >"$sudoers"
chmod 0440 "$sudoers"
visudo -cf "$sudoers"
systemctl daemon-reload
systemctl start nginx.service
# Root inside a ProtectSystem=full namespace also cannot modify /etc.
systemd-run --unit=mag-pages-ci-readonly --wait --collect --quiet \
 -p Type=oneshot -p User=root -p ProtectSystem=full \
 /bin/bash -c 'if touch /etc/mag-pages-ci-should-never-exist; then exit 1; fi'
test ! -e /etc/mag-pages-ci-should-never-exist
# Model pages_set_domain invokes the same fixed-command service immediately;
# fake ACME issuer materializes a key without any timer tick or DNS calls.
systemctl start mag-pages-reconcile.service
test -f "$tmp/state/issued-immediately"
test -s "$tmp/tls/custom/$id/accounts/account.json"
test -s "$tmp/tls/custom/$id/key.pem"
chown -R magbot:magbot "$tmp/state"
cat >"$tmp/client-policy" <<EOF
#!/usr/bin/env bash
set -euo pipefail
printf '{"repo":"magbot-agent1/site","action":"tailnet"}\\n' > "$tmp/state/request.json"
sudo -n /usr/bin/systemctl start mag-pages-reconcile.service
test ! -e "$tmp/state/request.json"
touch "$tmp/state/policy-completed"
EOF
chmod 0755 "$tmp/client-policy"
systemd-run --unit=mag-pages-ci-policy --wait --collect --quiet \
 -p Type=oneshot -p User=magbot -p ProtectSystem=full \
 /bin/bash "$tmp/client-policy"
test -f "$tmp/state/policy-completed"
# Visibility policy in /etc is root-only; assert it here outside the sandbox.
jq -e '."magbot-agent1/site"=="tailnet"' "$tmp/etc/visibility.json" >/dev/null
cat >"$tmp/client" <<EOF
#!/usr/bin/env bash
set -euo pipefail
# Simulate native C++ successful Pages DELETE after Gitea source removal.
rm -f "$tmp/state/tailnet/site--magbot-agent1"
rm -f "$tmp/state/public/site--magbot-agent1"
rm -f "$tmp/state/custom/$host"
rm -rf "$tmp/state/releases/site--magbot-agent1"
printf '{}\\n' > "$tmp/state/sites.json"
printf '["magbot-agent1/site"]\\n' >"$tmp/state/gc-retired.json"
: > "$tmp/state/custom-domains.tsv"
sudo -n /usr/bin/systemctl start mag-pages-reconcile.service
test ! -e "$tmp/state/request.json"
touch "$tmp/state/completed"
EOF
chmod 0755 "$tmp/client"
systemd-run --unit=mag-pages-ci-delete --wait --collect --quiet \
 -p Type=oneshot -p User=magbot -p ProtectSystem=full \
 /bin/bash "$tmp/client"
test -f "$tmp/state/completed"
test ! -e "$tmp/state/releases/site--magbot-agent1"
test ! -e "$tmp/state/tailnet/site--magbot-agent1"
test ! -e "$tmp/state/public/site--magbot-agent1"
test ! -e "$tmp/state/custom/$host"
test ! -e "$tmp/tls/custom/$id"
test ! -e "$nginxfile"
test ! -s "$tmp/state/nginx-map"
test ! -e "$tmp/state/request.json"
test ! -e "$tmp/state/gc-retired.json"
test ! -e "$tmp/state/gc.pending"
test "$(cat "$tmp/etc/visibility.json")" == '{}'
test "$(cat "$tmp/tls/current")" == wildcard
test "$(cat "$tmp/tls/releases/wild.pem")" == wildcard
nginx -t
systemctl reload nginx.service
# Repeated reconcile is idempotent even after all Page files are gone.
systemctl start mag-pages-reconcile.service
test ! -e "$tmp/tls/custom/$id"
echo 'ProtectSystem=full -> fixed root oneshot -> deletion, nginx reload, cert account cleanup: PASS'
