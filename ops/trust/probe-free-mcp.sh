#!/usr/bin/env bash
# Prove the exact MAG host trust bundle can reach the live anonymous MCP.
# No -k / insecure TLS bypass. One attempt, no retries, hard total timeout.
# HTTP 429 is upstream rate limiting, not a broken room or a bad CA bundle.
set -euo pipefail
budget="${MAG_FREE_MCP_PROBE_TIMEOUT:-25}"
if [[ -z ${MAG_FREE_MCP_PROBE_BOUNDED:-} ]] && command -v timeout >/dev/null 2>&1; then
  export MAG_FREE_MCP_PROBE_BOUNDED=1
  exec timeout --kill-after=2s "${budget}s" bash "$0" "$@"
fi
url="${MAG_ROOM_MCP_DP_URL:-https://accounts.digitalisierungsplanung.de/mcp/free}"
# The real mag-120 process uses the audited HTTPS proxy and its combined CA.
# Probe the SAME path: direct-host testing missed the original regression.
ca=${MAG_FREE_MCP_CA:-/etc/mag-audit/ca-bundle.pem}
proxy=${MAG_FREE_MCP_PROXY:-http://127.0.0.1:8888}
case "$url" in
  https://*) ;;
  *) echo "free MCP TLS probe: production URL must be HTTPS" >&2; exit 1 ;;
esac
[[ -s "$ca" ]] || { echo "free MCP TLS probe: CA bundle missing: $ca" >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# Same HTTPS audit proxy and CA as mag-room/native/mcp.hpp; TLS verification stays enabled.
# No --retry: a stuck proxy must not multiply --max-time. The timeout(1) wrapper
# above is the hard bound for both requests together.
curl_args=(--silent --show-error --connect-timeout 5 --max-time 10
  --proto '=https' --cacert "$ca" --proxy "$proxy" --noproxy ''
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
http_code() {
  local out=$1 hdr=$2 body=$3 code
  code=$(curl "${curl_args[@]}" -D "$hdr" -o "$out" -w '%{http_code}' -d "$body" "$url" || true)
  code=${code//$'\n'/}
  printf '%s' "${code:-000}"
}
code=$(http_code "$tmp/init.json" "$tmp/h" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"mag-deploy-free-mcp-probe","version":"1"}}}')
if [[ $code == 429 ]]; then
  echo "WARN: free MCP probe HTTP 429 (rate limited); not a deploy failure" >&2
  exit 0
fi
if [[ $code != 200 ]]; then
  echo "free MCP TLS probe failed (HTTP $code). Check ISRG Root YE/YR in /etc/ssl/certs/ca-certificates.crt and the server's TLS fullchain. Never disable peer verification." >&2
  exit 1
fi
sid=$(awk 'tolower($1)=="mcp-session-id:" {gsub(/\r/, "", $2); print $2}' "$tmp/h" | tail -n 1)
[[ -n "$sid" && "$sid" != *$'\r'* ]] || { echo "free MCP TLS probe: missing session ID" >&2; exit 1; }
python3 - "$tmp/init.json" <<'PY'
import json,sys
with open(sys.argv[1],encoding='utf-8') as f: data=json.load(f)
assert data.get("result",{}).get("serverInfo",{}).get("name")=="digitalisierungsplanung-free", "unexpected free MCP initialization response"
PY
code=$(curl "${curl_args[@]}" -o "$tmp/tools.json" -w '%{http_code}' \
  -H "Mcp-Session-Id: $sid" -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' "$url" || true)
code=${code//$'\n'/}
if [[ $code == 429 ]]; then
  echo "WARN: free MCP probe HTTP 429 (rate limited); not a deploy failure" >&2
  exit 0
fi
if [[ ${code:-000} != 200 ]]; then
  echo "free MCP TLS probe failed (HTTP ${code:-000}). Check ISRG Root YE/YR in /etc/ssl/certs/ca-certificates.crt and the server's TLS fullchain. Never disable peer verification." >&2
  exit 1
fi
python3 - "$tmp/tools.json" <<'PY'
import json,sys
with open(sys.argv[1],encoding='utf-8') as f: data=json.load(f)
names={t.get("name") for t in data.get("result",{}).get("tools",[])}
assert {"zustand_contract","zustand_snapshot","zustand_apply","zustand_share"}.issubset(names), "free MCP tools missing"
PY
echo "FREE_MCP_TLS_OK: audited HTTPS proxy certificate chain, initialize and public-share tools verified"
