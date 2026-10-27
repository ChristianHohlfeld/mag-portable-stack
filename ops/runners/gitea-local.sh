#!/usr/bin/env bash
# Local Gitea Actions registration helpers. Sourced by ops/runners/install.sh.
# The runner pings POST /api/actions/ping.v1.PingService/Ping. A 404 there is
# what connect-go reports as "unimplemented: 404 Not Found".
# That route exists only when [actions] ENABLED is true. Token generation uses
# /api/internal and can succeed while the ping 404s.
# ROOT_URL / HTTP_ADDR may be the Funnel (https://mag.tail3e02ae.ts.net/) or
# tailnet serve :8445. nginx serves /api/ for the room, so a ping through that
# proxy is also a 404. Registration stays on the loopback listener.
set -euo pipefail

# Not overridable: a proxy URL or ROOT_URL must not be able to replace this.
MAG_GITEA_RUNNER_INSTANCE=http://127.0.0.1:3000

gitea_setini() { # file section key value
  local file=$1 section=$2 key=$3 value=$4 tmp mode owner
  [[ -f $file ]] || return 1
  mode=$(stat -c %a "$file")
  owner=$(stat -c %u:%g "$file")
  tmp=$(mktemp "$(dirname "$file")/.gitea-ini.XXXXXXXX")
  awk -v section="$section" -v key="$key" -v value="$value" '
    function finish() { if (inside && !written) { print key " = " value; written = 1 } }
    /^\[/ {
      finish()
      inside = ($0 == "[" section "]")
      if (inside) seen = 1
      print
      next
    }
    {
      if (inside && $0 ~ "^[[:space:]]*" key "[[:space:]]*=") {
        if (!written) print key " = " value
        written = 1
      } else print
    }
    END { finish(); if (!seen) print "\n[" section "]\n" key " = " value }
  ' "$file" > "$tmp"
  [[ -s $tmp ]] || { rm -f "$tmp"; return 1; }
  mv -f "$tmp" "$file"
  chmod "$mode" "$file" || true
  if [[ $EUID -eq 0 ]]; then chown "$owner" "$file"; fi
}

# Prints "changed" or "unchanged". Does not print file contents.
gitea_ensure_actions_ini() {
  local file=$1 snap state
  [[ -f $file ]] || return 1
  snap=$(mktemp)
  cp -a "$file" "$snap"
  gitea_setini "$file" server LOCAL_ROOT_URL 'http://127.0.0.1:3000/'
  gitea_setini "$file" actions ENABLED true
  if cmp -s "$snap" "$file"; then state=unchanged; else state=changed; fi
  rm -f "$snap"
  printf '%s\n' "$state"
}

# Runner 5.1.1 needs the connect ping route added in Gitea 1.21.
# 28.x is the pinned current line and is new enough.
gitea_actions_rpc_supported() {
  local ver=$1 major minor
  [[ $ver =~ ^([0-9]+)\.([0-9]+) ]] || return 1
  major=${BASH_REMATCH[1]}
  minor=${BASH_REMATCH[2]}
  (( major > 1 || (major == 1 && minor >= 21) ))
}

gitea_binary_version() {
  local bin=${1:-/usr/local/bin/gitea}
  "$bin" --version 2>/dev/null | awk 'NR==1 { print $3; exit }'
}

# Prints only the instance address stored at registration. Never the token.
gitea_runner_address() {
  python3 - "$1" <<'PY'
import json, pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text()
addr = ""
try:
    data = json.loads(text)
except json.JSONDecodeError:
    data = None
if isinstance(data, dict):
    raw = data.get("address") or ""
    addr = raw if isinstance(raw, str) else ""
else:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("address:"):
            addr = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            break
sys.stdout.write(addr)
PY
}

gitea_runner_address_is_local() {
  local addr
  addr=$(gitea_runner_address "$1") || return 1
  addr=${addr%/}
  [[ $addr == "$MAG_GITEA_RUNNER_INSTANCE" ]]
}

# Exit 0 when the runner file is missing or points anywhere but loopback.
gitea_runner_needs_register() {
  local file=$1
  [[ -s $file ]] || return 0
  if gitea_runner_address_is_local "$file"; then return 1; fi
  return 0
}

# Loopback only. A host HTTP_PROXY must not send 127.0.0.1 to the Funnel.
gitea_local_curl() {
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
    no_proxy='*' NO_PROXY='*' \
    curl --noproxy '*' --max-redirs 0 "$@"
}

# HTTP status of the connect ping, without following a redirect to ROOT_URL.
# 000 means the listener did not answer. Any 404 is the registration failure.
# A non-loopback URL is treated as 404 without contacting the public proxy.
gitea_actions_ping_code() {
  local base=${1%/} code
  [[ $base == http://127.0.0.1:3000 ]] || { printf '404'; return 0; }
  code=$(gitea_local_curl -sS -o /dev/null -w '%{http_code}' --max-time 5 --connect-timeout 2 \
    -X POST -H 'Content-Type: application/json' -H 'Connect-Protocol-Version: 1' \
    --data '{}' "${base}/api/actions/ping.v1.PingService/Ping") || true
  [[ $code =~ ^[0-9]{3}$ ]] || code=000
  printf '%s' "$code"
}

gitea_actions_ping_ok() {
  case $1 in
    200|400|401|405|415|422) return 0 ;;
    *) return 1 ;;
  esac
}

gitea_wait_health() {
  local tries=${MAG_GITEA_HEALTH_TRIES:-30} pause=${MAG_GITEA_HEALTH_SLEEP:-1} i
  for i in $(seq 1 "$tries"); do
    if gitea_local_curl -fsS --max-time 2 --connect-timeout 1 http://127.0.0.1:3000/api/healthz >/dev/null; then
      return 0
    fi
    sleep "$pause"
  done
  return 1
}

# After a gitea restart, healthz can be up before /api/actions is mounted.
# Prints the last ping status. Returns 0 only when that status is usable.
gitea_wait_actions() {
  local tries=${MAG_GITEA_PING_TRIES:-15} pause=${MAG_GITEA_PING_SLEEP:-1} i code=000
  for i in $(seq 1 "$tries"); do
    code=$(gitea_actions_ping_code "$MAG_GITEA_RUNNER_INSTANCE")
    if gitea_actions_ping_ok "$code"; then
      printf '%s' "$code"
      return 0
    fi
    sleep "$pause"
  done
  printf '%s' "$code"
  return 1
}
