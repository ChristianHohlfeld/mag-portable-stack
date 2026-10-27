#!/usr/bin/env bash
# Static guards for the action runners: pinned downloads, unprivileged capped
# units, deploy only through the auto-update path, models never touched.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
r="$root/ops/runners"
bash -n "$r/install.sh" "$r/gitea-local.sh" "$root/ops/auto-update/apply.sh" "$root/ops/auto-update/auto-update.sh"
grep -Eq '^GITEA_RUNNER_SHA=[0-9a-f]{64}$' "$r/install.sh"
grep -Eq '^GH_RUNNER_SHA=[0-9a-f]{64}$' "$r/install.sh"
grep -Fq 'sha256sum -c' "$r/install.sh"
for unit in "$r/mag-gitea-runner.service" "$r/mag-github-runner.service"; do
  for line in 'NoNewPrivileges=yes' 'ProtectSystem=strict' 'ProtectHome=yes' 'MemoryMax=' 'CPUQuota=' 'OOMScoreAdjust=1000' \
    'Nice=15' 'IOSchedulingClass=idle' 'IOSchedulingPriority=7' 'CPUAffinity=42-55' 'AllowedCPUs=42-55' \
    'TimeoutStartSec=30' 'TimeoutStopSec=40' 'NO_PROXY=127.0.0.1,localhost,::1'; do
    grep -Fq "$line" "$unit"
  done
  ! grep -E '^(CPUAffinity|AllowedCPUs)=.*(0-13|28-41)' "$unit"
  grep -Eq '^User=mag-(gh)?runner$' "$unit"
  grep -Eq '^InaccessiblePaths=.*/etc/credstore.*/var/lib/magbot.*/var/lib/mag-llm' "$unit"
done
grep -Fq 'Unit=mag-auto-update.service' "$r/mag-deploy-request.path"
grep -Fq 'PathModified=/var/lib/mag-deploy/request' "$r/mag-deploy-request.path"
! grep -Eq 'sudo|sudoers' "$r/install.sh" "$r"/*.service
! grep -Eq 'mag-llm(\.service|-iq3)|systemctl (stop|restart) mag-llm' "$r"/* "$root/ops/auto-update"/*
grep -Fq 'ops/runners/install.sh' "$root/ops/auto-update/apply.sh"
grep -Fq 'exec bash "$ROOT/ops/auto-update/apply.sh"' "$root/ops/auto-update/auto-update.sh"
grep -Fq "vars.MAG_SELF_HOSTED == 'on'" "$root/.github/workflows/green.yml"
grep -Fq "needs.promote-green.outputs.promoted == 'true'" "$root/.github/workflows/green.yml"
grep -Fq 'mag:host' "$r/gitea-runner.yaml"
grep -Fq 'shutdown_timeout: 30s' "$r/gitea-runner.yaml"
grep -Fq 'fetch_timeout: 5s' "$r/gitea-runner.yaml"
grep -Fq 'MAG_GITEA_RUNNER_INSTANCE=http://127.0.0.1:3000' "$r/gitea-local.sh"
grep -Fq -- '--instance "$MAG_GITEA_RUNNER_INSTANCE"' "$r/install.sh"
grep -Fq 'timeout --kill-after=5s 25s' "$r/install.sh"
grep -Fq 'taskset -c 42-55' "$r/install.sh"
grep -Fq 'systemctl restart gitea.service' "$r/install.sh"
grep -Fq 'gitea_wait_actions' "$r/install.sh"
grep -Fq 'install_github_runner() (' "$r/install.sh"
grep -Fq 'if ! install_github_runner; then' "$r/install.sh"
! grep -Fq 'install_github_runner ||' "$r/install.sh"
! grep -F 'instance="http://${addr' "$r/install.sh"
grep -Fq "curl --noproxy '*'" "$r/gitea-local.sh"
grep -Fq '[[ $base == http://127.0.0.1:3000 ]]' "$r/gitea-local.sh"
! grep -E 'systemctl (restart|stop|reload)[[:space:]]+(nginx|mag-llm|mag-120)' "$r/install.sh" "$r"/*.service
grep -Fq 'LOCAL_ROOT_URL = http://127.0.0.1:3000/' "$root/ops/gitea/app.ini.example"
grep -Fq 'ENABLED = true' "$root/ops/gitea/app.ini.example"
# shellcheck source=../runners/gitea-local.sh
source "$r/gitea-local.sh"
gitea_actions_rpc_supported 28.1.0
gitea_actions_rpc_supported 1.21.0
! gitea_actions_rpc_supported 1.20.5
! gitea_actions_rpc_supported 1.19.0
gitea_actions_ping_ok 200
gitea_actions_ping_ok 415
! gitea_actions_ping_ok 404
! gitea_actions_ping_ok 302
! gitea_actions_ping_ok 000
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/app.ini" <<'INI'
[server]
HTTP_ADDR = mag.tail3e02ae.ts.net
HTTP_PORT = 443
ROOT_URL = https://mag.tail3e02ae.ts.net/
[mailer]
ENABLED = false
[security]
SECRET_KEY = fixture-private-key-do-not-print
INI
out=$(gitea_ensure_actions_ini "$tmp/app.ini")
[[ $out == changed ]]
! grep -Fq 'fixture-private-key-do-not-print' <<<"$out"
grep -Fq 'SECRET_KEY = fixture-private-key-do-not-print' "$tmp/app.ini"
grep -Fq 'ROOT_URL = https://mag.tail3e02ae.ts.net/' "$tmp/app.ini"
grep -Fq 'HTTP_ADDR = mag.tail3e02ae.ts.net' "$tmp/app.ini"
grep -Fq 'LOCAL_ROOT_URL = http://127.0.0.1:3000/' "$tmp/app.ini"
grep -Fq 'ENABLED = true' "$tmp/app.ini"
grep -Fq 'ENABLED = false' "$tmp/app.ini"
[[ $(grep -c '^\[actions\]' "$tmp/app.ini") == 1 ]]
[[ $(grep -c '^\[security\]' "$tmp/app.ini") == 1 ]]
[[ $(gitea_ensure_actions_ini "$tmp/app.ini") == unchanged ]]
printf '%s\n' '{"address":"https://mag.tail3e02ae.ts.net/","token":"super-secret-token"}' > "$tmp/runner.json"
addr=$(gitea_runner_address "$tmp/runner.json")
[[ $addr == https://mag.tail3e02ae.ts.net/ ]]
! grep -Fq 'super-secret-token' <<<"$addr"
! gitea_runner_address_is_local "$tmp/runner.json"
gitea_runner_needs_register "$tmp/runner.json"
printf '%s\n' '{"address":"http://127.0.0.1:3000/","token":"super-secret-token"}' > "$tmp/runner.json"
gitea_runner_address_is_local "$tmp/runner.json"
! gitea_runner_needs_register "$tmp/runner.json"
gitea_runner_needs_register "$tmp/missing-runner"
mkdir -p "$tmp/bin"
printf '#!/bin/sh\nexit 1\n' > "$tmp/bin/curl"
chmod +x "$tmp/bin/curl"
! PATH="$tmp/bin:$PATH" MAG_GITEA_HEALTH_TRIES=2 MAG_GITEA_HEALTH_SLEEP=0 gitea_wait_health
echo 0 > "$tmp/n"
cat > "$tmp/bin/curl" <<EOF
#!/bin/sh
n=\$(cat "$tmp/n")
n=\$((n+1))
printf '%s\n' "\$n" > "$tmp/n"
[ "\$n" -ge 2 ]
EOF
chmod +x "$tmp/bin/curl"
PATH="$tmp/bin:$PATH" MAG_GITEA_HEALTH_TRIES=3 MAG_GITEA_HEALTH_SLEEP=0 gitea_wait_health
printf '#!/bin/sh\nprintf 404\n' > "$tmp/bin/curl"
[[ $(PATH="$tmp/bin:$PATH" gitea_actions_ping_code http://127.0.0.1:3000) == 404 ]]
printf '#!/bin/sh\nprintf 200\n' > "$tmp/bin/curl"
[[ $(PATH="$tmp/bin:$PATH" gitea_actions_ping_code http://127.0.0.1:3000) == 200 ]]
# Public/Funnel URLs must not be contacted; the connect path 404s there.
[[ $(PATH="$tmp/bin:$PATH" gitea_actions_ping_code https://mag.tail3e02ae.ts.net) == 404 ]]
[[ $(PATH="$tmp/bin:$PATH" gitea_actions_ping_code http://127.0.0.1:8445) == 404 ]]
cat > "$tmp/bin/curl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" > "${CURL_ARGS:?}"
printf 200
EOF
chmod +x "$tmp/bin/curl"
CURL_ARGS="$tmp/curl.args" HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 \
  PATH="$tmp/bin:$PATH" gitea_actions_ping_code http://127.0.0.1:3000 >/dev/null
grep -Fq -- '--noproxy *' "$tmp/curl.args"
grep -Fq 'http://127.0.0.1:3000/api/actions/ping.v1.PingService/Ping' "$tmp/curl.args"
! grep -Fq '127.0.0.1:9' "$tmp/curl.args"
echo 0 > "$tmp/n"
cat > "$tmp/bin/curl" <<EOF
#!/bin/sh
n=\$(cat "$tmp/n")
n=\$((n+1))
printf '%s\n' "\$n" > "$tmp/n"
if [ "\$n" -lt 2 ]; then printf 404; exit 0; fi
printf 200
EOF
[[ $(PATH="$tmp/bin:$PATH" MAG_GITEA_PING_TRIES=3 MAG_GITEA_PING_SLEEP=0 gitea_wait_actions) == 200 ]]
# A function on the left of || loses errexit. Checksum failure must not extract
# or stamp .version. The installer uses a set -e subshell plus explicit returns.
printf 'not-a-tarball\n' > "$tmp/r.tgz"
goodsha=$(sha256sum "$tmp/r.tgz" | awk '{print $1}')
fetch() { printf '%s  %s\n' "$2" "$3" | sha256sum -c --quiet -; }
install_github_archive() (
  set -euo pipefail
  fetch unused "$1" "$tmp/r.tgz" || return 1
  tar -xzf "$tmp/r.tgz" -C "$tmp" || return 1
  echo stamped > "$tmp/.version"
)
if ! install_github_archive deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef >/dev/null 2>&1; then
  :
fi
[[ ! -e $tmp/.version ]]
if ! install_github_archive "$goodsha" >/dev/null 2>&1; then
  :
fi
[[ ! -e $tmp/.version ]]
echo 'Runner guards PASS'
