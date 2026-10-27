#!/usr/bin/env bash
# The shipped Root YE must verify Let's Encrypt YE2 with no system trust store.
# Guest install stays on the overlay: despawn deletes the disk, golden stays read-only.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
openssl verify -CAfile "$here/isrg-root-ye.pem" -no-CApath -no-CAstore "$here/testdata/ye2.pem" >/dev/null
bundle=$(mktemp)
cat "$here"/isrg-root-x1.pem "$here"/isrg-root-x2.pem "$here"/isrg-root-ye.pem "$here"/isrg-root-yr.pem > "$bundle"
openssl verify -CAfile "$bundle" -no-CApath -no-CAstore "$here/testdata/ye2.pem" >/dev/null
rm -f "$bundle"
for name in isrg-root-x1 isrg-root-x2 isrg-root-ye isrg-root-yr; do
  openssl x509 -in "$here/$name.pem" -noout -subject | grep -q .
done
grep -q 'isrg-roots.pem' "$root/mag-room/native/vm.cpp"
grep -q 'remove_all' "$root/mag-room/native/vm.cpp"
grep -q 'golden image is never modified' "$root/mag-room/native/vm.cpp"
grep -q 'ops/trust/install-trust.sh' "$root/ops/deploy.sh"
grep -q 'mag-isrg-' "$root/ops/audit/vm-proxy-setup.sh"
grep -q 'mcp-card-overflow.patch' "$root/ops/deploy.sh"
html=$(mktemp)
cp "$root/mag-room/web/index.html" "$html"
patch --forward --batch "$html" "$here/mcp-card-overflow.patch" >/dev/null
grep -q 'overflow-wrap:anywhere' "$html"
grep -q "el('span','',c.name)" "$html"
python3 -c 'from pathlib import Path; s=Path("'"$html"'").read_text(); Path("/tmp/mag-trust-index.js").write_text(s.split("<script>")[-1].split("</script>")[0])'
node --check /tmp/mag-trust-index.js
rm -f "$html"
bash -n "$here/install-trust.sh" "$here/probe-free-mcp.sh" "$root/ops/audit/vm-proxy-setup.sh" "$root/ops/deploy.sh"
grep -q 'ops/trust/probe-free-mcp.sh' "$root/ops/deploy.sh"
grep -q -- '--cacert' "$here/probe-free-mcp.sh"
grep -q 'FREE_MCP_TLS_OK' "$here/probe-free-mcp.sh"
# Regression #81: probing the direct upstream cannot establish that the room
# (which uses a TLS-auditing local MITM proxy) can actually connect.
grep -q '/etc/mag-audit/ca-bundle.pem' "$here/probe-free-mcp.sh"
grep -q -- '--proxy "$proxy"' "$here/probe-free-mcp.sh"
! grep -q -- '--retry' "$here/probe-free-mcp.sh"
grep -q 'HTTP 429' "$here/probe-free-mcp.sh"
grep -q 'timeout --kill-after' "$here/probe-free-mcp.sh"
grep -q 'not rolling back a healthy room' "$root/ops/deploy.sh"
! grep -Eq 'systemctl (restart|stop|reload).*mag-llm' "$root/ops/deploy.sh" "$root/ops/update.sh"
grep -q 'CURLOPT_SSL_VERIFYPEER, 1L' "$root/mag-room/native/mcp.hpp"
grep -q 'CURLOPT_SSL_VERIFYHOST, 2L' "$root/mag-room/native/mcp.hpp"
grep -q 'audited = proxy ==' "$root/mag-room/native/mcp.hpp"
grep -q 'AGENT_CTL, "ip", vm' "$root/mag-room/native/magroom.cpp"
grep -q 'domifaddr "magbot-$2" --source "$source"' "$root/ops/magbot-agent-ctl"
bash -n "$root/ops/magbot-agent-ctl"
# 429 is a warning, not a failure. A hung proxy dies inside the total budget.
probe_tmp=$(mktemp -d)
printf 'x\n' > "$probe_tmp/ca"
cat > "$probe_tmp/curl429" <<'SH'
#!/bin/sh
printf '429'
SH
chmod +x "$probe_tmp/curl429"
mkdir -p "$probe_tmp/bin429"
ln -s "$probe_tmp/curl429" "$probe_tmp/bin429/curl"
MAG_FREE_MCP_PROBE_BOUNDED=1 MAG_FREE_MCP_CA="$probe_tmp/ca" PATH="$probe_tmp/bin429:$PATH" \
  bash "$here/probe-free-mcp.sh" 2>"$probe_tmp/warn"
grep -Fq 'HTTP 429' "$probe_tmp/warn"
cat > "$probe_tmp/curlhang" <<SH
#!/bin/sh
touch "$probe_tmp/hung"
sleep 30
SH
chmod +x "$probe_tmp/curlhang"
mkdir -p "$probe_tmp/binhang"
ln -s "$probe_tmp/curlhang" "$probe_tmp/binhang/curl"
start=$(date +%s)
set +e
MAG_FREE_MCP_PROBE_TIMEOUT=2 MAG_FREE_MCP_CA="$probe_tmp/ca" PATH="$probe_tmp/binhang:$PATH" \
  bash "$here/probe-free-mcp.sh" >/dev/null 2>&1
hang_rc=$?
set -e
elapsed=$(( $(date +%s) - start ))
[[ -f $probe_tmp/hung && $hang_rc -ne 0 && $elapsed -ge 1 && $elapsed -lt 8 ]]
rm -rf "$probe_tmp"
echo 'trust chain OK'
