#!/usr/bin/env bash
# Original private-nginx.yml / nginx: same tests, one shared native build inside a disposable VM.
set -euo pipefail
export RUNNER_ENVIRONMENT=github-hosted MAG_E2E_SHOTS=/work/screenshots
export GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack
test -f CMakeLists.txt
test -d .git
printf "\n== %s\n" "Shell syntax and transactional TLS tests"
bash -n ops/private-access/*.sh ops/private-access/tests/*.sh
bash ops/private-access/tests/test-deploy-hook.sh
bash ops/private-access/tests/test-rollback.sh

printf "\n== %s\n" "Actual lego 5.5.2 CLI parsing (no ACME traffic)"
bash ops/private-access/tests/test-lego-v5.sh
printf "\n== %s\n" "Real TLS nginx auth, fail-closed, header spoofing"
python3 mag-room/tests/test_private_nginx.py
printf "\n== %s\n" "Disposable deploy/Owner-smoke/rollback regression"
python3 mag-room/tests/test_whois_deploy.py
printf "\n== %s\n" "Cutover guard and rollback contract"
grep -F 'systemd-run --quiet --unit=mag-private-rollback --on-active=180s' ops/private-access/install.sh
grep -F 'systemctl reload nginx.service' ops/private-access/rollback.sh
grep -F 'owner-verified' ops/private-access/confirm.sh
grep -F 'mag-private-cutover/confirmed' ops/private-access/remove-caddy.sh
! grep -E '(systemctl|service) (restart|stop|try-restart) mag-llm' ops/private-access/*.sh
! test -e ops/private-access/tailscale-whois.py
! test -e ops/private-access/mag-whois.service
grep -F 'include /opt/mag-stack/current/ops/nginx/mag-speech*.inc;' ops/private-access/mag-room.nginx.conf
grep -F 'cp -a -- "$site" "$backup"' ops/private-access/install.sh
grep -F 'mode=$(stat -c %a "$backup")' ops/private-access/install.sh
grep -F 'rm -f -- "$backup"' ops/private-access/rollback.sh
! grep -F 'python3 -' ops/private-access/install.sh
grep -F 'proxy_pass http://127.0.0.1:8766/_internal/tailscale/whois;' ops/private-access/mag-room.nginx.conf

