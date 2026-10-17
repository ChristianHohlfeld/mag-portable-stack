#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/state" "$tmp/tls/custom" "$tmp/tls/releases"
export MAG_PAGES_TESTING=1 MAG_PAGES_GC_ROOT="$tmp/state" \
 MAG_PAGES_GC_VISIBILITY="$tmp/visibility.json" MAG_PAGES_GC_TLS="$tmp/tls" \
 MAG_PAGES_GC_REFRESH="$tmp/refresh"
host=www.example.net
id="$(printf %s "$host" | sha256sum | cut -c1-16)"
mkdir -p "$tmp/tls/custom/$id/certificates" "$tmp/tls/custom/$id/accounts"
printf 'key' > "$tmp/tls/custom/$id/certificates/key.pem"
printf 'account' > "$tmp/tls/custom/$id/accounts/account.json"
printf 'wildcard' > "$tmp/tls/releases/shared.pem"
printf 'wildcard' > "$tmp/tls/current"
printf '{"alice/site":{"sha":"123abc"}}\n' > "$tmp/state/sites.json"
printf '[]\n' > "$tmp/state/gc-retired.json"
printf 'www.example.net\tsite--alice.pages.stardenburg.de\tsite--alice\n' > "$tmp/state/custom-domains.tsv"
printf '{"alice/site":"public"}\n' > "$tmp/visibility.json"
cat >"$tmp/refresh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == custom-only ]]
cp "$MAG_PAGES_GC_ROOT/custom-domains.tsv" "$MAG_PAGES_GC_ROOT/nginx-map"
EOF
chmod +x "$tmp/refresh"
bash "$here/ops/pages/pages-gc.sh"
test -s "$tmp/tls/custom/$id/certificates/key.pem"
grep -Fq www.example.net "$tmp/state/nginx-map"
printf '{}\n' >"$tmp/state/sites.json"
printf '["alice/site"]\n' >"$tmp/state/gc-retired.json"
: > "$tmp/state/custom-domains.tsv"
bash "$here/ops/pages/pages-gc.sh"
test ! -e "$tmp/tls/custom/$id"
test ! -s "$tmp/state/nginx-map"
test "$(cat "$tmp/visibility.json")" == '{}'
test "$(cat "$tmp/tls/releases/shared.pem")" == wildcard
test "$(cat "$tmp/tls/current")" == wildcard
bash "$here/ops/pages/pages-gc.sh"
test ! -e "$tmp/tls/custom/$id"
mkdir -p "$tmp/tls/custom/$id"
echo secret >"$tmp/tls/custom/$id/key"
printf '#!/usr/bin/env bash\nexit 1\n' >"$tmp/refresh"
if bash "$here/ops/pages/pages-gc.sh"; then echo 'Failed closed not enforced' >&2; exit 1; fi
test -s "$tmp/tls/custom/$id/key"
echo 'Pages lifecycle CREATE -> DELETE -> no remnants + wildcard retained: PASS'
