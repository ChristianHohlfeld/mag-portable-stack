#!/usr/bin/env bash
# Original native.yml / native: same tests, one shared native build inside a disposable VM.
set -euo pipefail
export RUNNER_ENVIRONMENT=github-hosted MAG_E2E_SHOTS=/work/screenshots
export GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack
test -f CMakeLists.txt
test -d .git
printf "\n== %s\n" "Build"
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build -j 2
printf "\n== %s\n" "Prevent CI room-clear from invoking production backup"
grep -Fq "MAG_TEST_NO_CLEAR_BACKUP='1'" mag-room/tests/test_native.py
! grep -Fq 'mag-backup-clear.timer' mag-room/native/routes.hpp
! grep -Fq 'start mag-backup-clear.service' mag-room/native/routes.hpp

printf "\n== %s\n" "Tailscale API nft IPv4/v6 egress regression without modifying host firewall"
sudo bash ops/audit/tests/test-tail-egress.sh
printf "\n== %s\n" "Owner-only backup and console access contract"
bash ops/private-access/tests/test-owner-boundaries.sh
printf "\n== %s\n" "Native integration checks"
./build/test-guest-pty
./build/test-browser-recovery
./build/test-jobs-mcp
./build/test-pages-native
./build/test-browser-events
./build/test-guest-description
./build/test-guest-curl
./build/test-large-stdin
./build/test-gitea-account
python3 mag-room/tests/test_large_tools.py
python3 mag-room/tests/test_native.py
python3 mag-room/tests/test_agent_core.py
python3 ops/numa/tests/test_numa_swap.py
python3 mag-room/tests/test_room_control.py
python3 mag-room/tests/test_tailscale_auth.py
python3 ops/audit/tests/test_native_gateway.py
bash ops/qwen/tests/test-qwen.sh
bash ops/ornith/tests/test-ornith.sh
python3 mag-room/tests/test_whois_deploy.py
bash -n ops/deploy.sh ops/rollback.sh ops/private-access/*.sh

printf "\n== %s\n" "IK prompt cache regression and rollback shell syntax"
bash ops/deepseek/tests/test-ops-38.sh
bash ops/deepseek/tests/test-numa-isolation.sh
bash ops/deepseek/tests/test-node0-available.sh
bash ops/deepseek/tests/test-iq3-parallel.sh
bash ops/deepseek/tests/test-model-preservation.sh
bash -n ops/deepseek/verify-iq3.sh ops/deepseek/start-iq3.sh ops/deepseek/iq3-parallel.sh ops/deepseek/hotswap.sh
bash -n ops/deepseek/numa-isolation.sh ops/deepseek/numa-bench.sh
bash -n ops/deepseek/prompt-cache-switch.sh

printf "\n== %s\n" "nginx syntax without a real Tailnet listener"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -keyout /tmp/mag-ci.key -out /tmp/mag-ci.crt -subj '/CN=mag.stardenburg.de' 2>/dev/null
sed -e 's|/etc/mag-nginx/tls/cert.pem|/tmp/mag-ci.crt|' -e 's|/etc/mag-nginx/tls/key.pem|/tmp/mag-ci.key|' -e 's|100[.]123[.]114[.]16:443|127.0.0.1:18443|' ops/private-access/mag-room.nginx.conf > /tmp/mag-ci-site.conf
grep -Fq 'listen 127.0.0.1:18443 ssl http2;' /tmp/mag-ci-site.conf
printf 'pid /tmp/mag-ci-nginx.pid;\nerror_log /tmp/mag-ci-nginx.err;\nevents {}\nhttp { access_log off; include /tmp/mag-ci-site.conf; }\n' > /tmp/mag-ci-nginx.conf
sudo nginx -t -c /tmp/mag-ci-nginx.conf -p /tmp

printf "\n== %s\n" "Browser WebRTC UDP gateway"
# Subshell: this suite is one shell, and the next step uses a repo-relative path.
( cd mag-room/webrtc && go test -mod=mod ./... && go build -mod=mod -o /tmp/mag-browser-webrtc . )
printf "\n== %s\n" "Vendor terminal and verify UI assets"
bash ops/vendor-terminal-assets.sh /tmp/mag-terminal-assets
test -s /tmp/mag-terminal-assets/xterm.js
test -s /tmp/mag-terminal-assets/xterm-addon-fit.js
test -s /tmp/mag-terminal-assets/xterm.css

printf "\n== %s\n" "Frontend script syntax"
python3 -c 'from pathlib import Path; s=Path("mag-room/web/index.html").read_text(); Path("/tmp/mag-room-web.js").write_text(s.split("<script>")[-1].split("</script>")[0])'
node --check /tmp/mag-room-web.js
node mag-room/tests/test_composer.cjs

printf "\n== %s\n" "Shell syntax"
bash -n ops/*.sh ops/magbot-guest-run ops/magbot-agent-ctl
bash -n ops/bots/*.sh ops/bots/tests/*.sh
bash ops/bots/tests/test-bots.sh

printf "\n== %s\n" "ISRG trust chain verifies without the system store"
bash ops/trust/test-trust.sh
printf "\n== %s\n" "Blank-host bootstrap dry run"
bash ops/tests/test-bootstrap.sh
printf "\n== %s\n" "Gitea pull mirror against a fake API"
bash ops/tests/test-gitea-mirror.sh
printf "\n== %s\n" "Gitea bot rights against a fake API"
bash ops/tests/test-gitea-bot-rights.sh
printf "\n== %s\n" "New ops scripts pass shellcheck"
if ! command -v shellcheck >/dev/null; then echo 'shellcheck not installed on this runner; skipped'; exit 0; fi
shellcheck ops/vm/build-golden.sh ops/bootstrap-voice.sh ops/deepseek/bootstrap-runtimes.sh ops/tailscale/install.sh ops/private-access/install-lego.sh ops/gitea/mirror-github.sh ops/tests/e2e-bootstrap.sh ops/ci/promote-green.sh ops/ci/self-hosted-checks.sh ops/deploy-watchdog.sh

printf "\n== %s\n" "Update script dry run"
bash ops/tests/test-update.sh
printf "\n== %s\n" "Deploy deadline releases the lock"
bash ops/tests/test-deploy-watchdog.sh
printf "\n== %s\n" "One-shot legacy cleanup in a sandbox"
bash ops/tests/test-cleanup-legacy.sh
printf "\n== %s\n" "Unreferenced bot VM drift check"
bash ops/tests/test-agent-drift.sh
printf "\n== %s\n" "S23 control API"
python3 devices/s23/tests/test_s23ctl.py
printf "\n== %s\n" "Action runner guards"
bash ops/tests/test-runners.sh
printf "\n== %s\n" "Green promotion"
bash ops/tests/test-green.sh
printf "\n== %s\n" "Boot persistence"
bash ops/tests/test-boot.sh
python3 ops/gitea-ci/tests/test_migration.py
if test -x build/test-pho-codec; then ./build/test-pho-codec; fi
bash ops/gitea-ci/tests/test_shell.sh
python3 ops/gitea-ci/tests/test_bot_auth.py
python3 ops/gitea-ci/tests/test_permissions.py
