#!/usr/bin/env bash
# Original native.yml / native: same tests, one shared native build inside a disposable VM.
# Fast contracts, binaries, the sharded room suite and companion checks share
# one pool so a single machine still finishes the merge gate. nginx, WebRTC
# and the gitea shell contracts stay after that pool.
set -euo pipefail
export RUNNER_ENVIRONMENT=github-hosted MAG_E2E_SHOTS=/work/screenshots
export GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack
test -f CMakeLists.txt
test -d .git
jobs=$(nproc 2>/dev/null || echo 2)
printf "\n== %s\n" "Build"
launch=()
if command -v ccache >/dev/null; then
  export CCACHE_DIR="${CCACHE_DIR:-/var/tmp/mag-ci-ccache}"
  mkdir -p "$CCACHE_DIR"
  launch+=(-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
fi
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release "${launch[@]}"
cmake --build build -j "$jobs"
printf "\n== %s\n" "Test and workflow deadlines"
python3 ops/ci/check-timeouts.py
printf "\n== %s\n" "Tailscale API nft IPv4/v6 egress regression without modifying host firewall"
sudo bash ops/audit/tests/test-tail-egress.sh
printf "\n== %s\n" "Native merge gate"
chmod +x build/test-* build/magroom build/mag-audit-gateway build/mag-numa-swap build/s23ctl
MAG_CI_JOBS=$jobs bash ops/ci/native-gate.sh --parallel
printf "\n== %s\n" "nginx syntax without a real Tailnet listener"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -keyout /tmp/mag-ci.key -out /tmp/mag-ci.crt -subj '/CN=mag.stardenburg.de' 2>/dev/null
sed -e 's|/etc/mag-nginx/tls/cert.pem|/tmp/mag-ci.crt|' -e 's|/etc/mag-nginx/tls/key.pem|/tmp/mag-ci.key|' -e 's|127.0.0.1:4443 ssl http2 proxy_protocol|127.0.0.1:18443 ssl http2|' ops/private-access/mag-room.nginx.conf > /tmp/mag-ci-site.conf
grep -Fq 'listen 127.0.0.1:18443 ssl http2;' /tmp/mag-ci-site.conf
printf 'pid /tmp/mag-ci-nginx.pid;\nerror_log /tmp/mag-ci-nginx.err;\nevents {}\nhttp { access_log off; include /tmp/mag-ci-site.conf; }\n' > /tmp/mag-ci-nginx.conf
sudo nginx -t -c /tmp/mag-ci-nginx.conf -p /tmp
python3 ops/tailscale/tests/test-sni-map.py
printf "\n== %s\n" "Browser WebRTC UDP gateway"
# Subshell: this suite is one shell, and the next step uses a repo-relative path.
( cd mag-room/webrtc && go test -mod=mod -timeout 60s ./... && go build -mod=mod -o /tmp/mag-browser-webrtc . )
printf "\n== %s\n" "Shell syntax"
bash -n ops/*.sh ops/magbot-guest-run ops/magbot-agent-ctl
bash -n ops/bots/*.sh ops/bots/tests/*.sh
bash ops/bots/tests/test-bots.sh
python3 ops/gitea-ci/tests/test_migration.py
bash ops/gitea-ci/tests/test_shell.sh
python3 ops/gitea-ci/tests/test_bot_auth.py
python3 ops/gitea-ci/tests/test_permissions.py
