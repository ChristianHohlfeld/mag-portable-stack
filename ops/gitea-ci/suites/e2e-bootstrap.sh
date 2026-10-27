#!/usr/bin/env bash
# Original e2e-bootstrap.yml / e2e-bootstrap: same tests, one shared native build inside a disposable VM.
set -euo pipefail
export RUNNER_ENVIRONMENT=github-hosted MAG_E2E_SHOTS=/work/screenshots
export GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack
test -f CMakeLists.txt
test -d .git
printf "\n== %s\n" "Bootstrap a blank Ubuntu 24.04 container and check /readyz"
sudo env MAG_E2E_LOG=/tmp/mag-e2e-bootstrap.log MAG_E2E_MEMORY=8g bash ops/tests/e2e-bootstrap.sh --jobs 2
