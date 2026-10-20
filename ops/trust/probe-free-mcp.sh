#!/usr/bin/env bash
# Prove the exact MAG host trust bundle can reach the live anonymous MCP.
# No -k / insecure TLS bypass. A missing ISRG YE/YR root or wrong fullchain
# must fail this probe BEFORE the MAG room is restarted/deployed.
set -euo pipefail
url="${MAG_ROOM_MCP_DP_URL:-https://accounts.digitalisierungsplanung.de/mcp/free}"
# The real mag-120 process uses the audited HTTPS proxy and its combined CA.
# Probe the SAME path: direct-host testing missed the original regression.
ca=/etc/mag-audit/ca-bundle.pem
proxy=http://127.0.0.1:8888
case "$url" in
  https://*) ;;
  *) echo "free MCP TLS probe: production URL must be HTTPS" >&2; exit 1 ;;
esac
[[ -s "$ca" ]] || { echo "free MCP TLS probe: CA bundle missing: $ca" >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# Same HTTPS audit proxy and CA as mag-room/native/mcp.hpp; TLS verification stays enabled.
curl_args=(--silent --show-error --fail-with-body --connect-timeout 8 --max-time 20
  --retry 2 --retry-delay 1 --retry-all-errors --proto '=https' --cacert "$ca" --proxy "$proxy" --noproxy ''
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
if ! curl "${curl_args[@]}" -D "$tmp/h" -o "$tmp/init.json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"mag-deploy-free-mcp-probe","version":"1"}}}' \
    "$url"; then
  echo "free MCP TLS probe failed. Check ISRG Root YE/YR in /etc/ssl/certs/ca-certificates.crt and the server's TLS fullchain. Never disable peer verification." >&2
  exit 1
fi
sid=$(awk 'tolower($1)=="mcp-session-id:" {gsub(/\r/, "", $2); print $2}' "$tmp/h" | tail -n 1)
[[ -n "$sid" && "$sid" != *$'\r'* ]] || { echo "free MCP TLS probe: missing session ID" >&2; exit 1; }
python3 - "$tmp/init.json" <<'PY'
import json,sys
with open(sys.argv[1],encoding='utf-8') as f: data=json.load(f)
assert data.get("result",{}).get("serverInfo",{}).get("name")=="digitalisierungsplanung-free", "unexpected free MCP initialization response"
PY
curl "${curl_args[@]}" -o "$tmp/tools.json" \
  -H "Mcp-Session-Id: $sid" -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' "$url"
python3 - "$tmp/tools.json" <<'PY'
import json,sys
with open(sys.argv[1],encoding='utf-8') as f: data=json.load(f)
names={t.get("name") for t in data.get("result",{}).get("tools",[])}
assert {"zustand_contract","zustand_snapshot","zustand_apply","zustand_share"}.issubset(names), "free MCP tools missing"
PY
echo "FREE_MCP_TLS_OK: audited HTTPS proxy certificate chain, initialize and public-share tools verified"
