#!/usr/bin/env bash
# Real lego 5.5.2 parser regression without contacting Let's Encrypt.
set -euo pipefail
[[ $(uname -m) == x86_64 ]] || { echo "CI test expects amd64"; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
url=https://github.com/go-acme/lego/releases/download/v5.5.2
curl -fsSL "$url/lego_v5.5.2_linux_amd64.tar.gz" -o "$tmp/lego.tgz"
curl -fsSL "$url/lego_5.5.2_checksums.txt" -o "$tmp/checksums.txt"
# Release checksums detect incomplete/misnamed assets.
cp "$tmp/lego.tgz" "$tmp/lego_v5.5.2_linux_amd64.tar.gz"
(cd "$tmp" && awk '$2=="lego_v5.5.2_linux_amd64.tar.gz" || $2=="*lego_v5.5.2_linux_amd64.tar.gz" {print}' checksums.txt | sha256sum -c -)
tar -xzf "$tmp/lego.tgz" -C "$tmp" lego
"$tmp/lego" --version | grep -F '5.5.2'
cat > "$tmp/lego.env" <<'ENV'
MAG_ACME_DOMAIN=mag.stardenburg.de
ACME_DNS_API_BASE=https://auth.acme-dns.io
ACME_DNS_STORAGE_PATH=/tmp/never-used-lego-test.json
ENV
# Keep production script root-only, but substitute only paths in a test COPY.
sed -e "s|/etc/mag-acme/lego.env|$tmp/lego.env|g" \
    -e "s|/usr/local/bin/lego|$tmp/lego-proxy|g" \
    ops/private-access/issue-cert.sh > "$tmp/issue-cert.sh"
cat > "$tmp/lego-proxy" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$tmp/argv.txt"
exec "$tmp/lego" "\$@" --help
EOF
chmod +x "$tmp/lego-proxy" "$tmp/issue-cert.sh"
for mode in staging production renew; do
  sudo "$tmp/issue-cert.sh" "$mode" >"$tmp/help.txt" 2>"$tmp/stderr.txt" || {
    cat "$tmp/stderr.txt" >&2
    echo "lego v5 rejected $mode" >&2
    exit 1
  }
  head -n1 "$tmp/argv.txt" | grep -Fx run
  grep -Fx -- '--accept-tos' "$tmp/argv.txt"
  grep -Fx -- '--dns.resolvers' "$tmp/argv.txt"
  grep -Fx -- '8.8.8.8:53,1.1.1.1:53' "$tmp/argv.txt"
  grep -Fx -- '--dns' "$tmp/argv.txt"
  grep -Fx -- acmedns "$tmp/argv.txt"
  grep -Fx -- 'mag.stardenburg.de' "$tmp/argv.txt"
  if [[ $mode == staging ]]; then
    ! grep -Fx -- '--deploy-hook' "$tmp/argv.txt"
  else
    grep -Fx -- '--deploy-hook' "$tmp/argv.txt"
  fi
done
echo "lego 5.5.2 staging/production/renew parser accepted; no ACME calls"
