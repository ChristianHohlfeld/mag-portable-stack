#!/usr/bin/env bash
# Original pages-native.yml / pages: same tests, one shared native build inside a disposable VM.
set -euo pipefail
export RUNNER_ENVIRONMENT=github-hosted MAG_E2E_SHOTS=/work/screenshots
export GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack
test -f CMakeLists.txt
test -d .git
printf "\n== %s\n" "Native C++ unit tests + MAG compilation"
bash ops/ci/native-build.sh test-pages-native magroom
./build/test-pages-native

printf "\n== %s\n" "Tailnet-only, public opt-in, lego v5 and DNS-01 onboarding tests"
bash ops/pages/tests/test-pages-ops.sh
printf "\n== %s\n" "Full Pages lifecycle and root cleanup regressions"
bash ops/pages/tests/test-pages-lifecycle.sh
bash ops/pages/tests/test-pages-tools.sh
if [ "$RUNNER_ENVIRONMENT" = github-hosted ]; then sudo bash ops/pages/tests/test-pages-sandbox.sh; fi
for script in ops/pages/*.sh ops/pages/tests/*.sh; do
  bash -n "$script"
done

printf "\n== %s\n" "Shell syntax; no new runtime units"
for script in ops/pages/*.sh; do
  bash -n "$script"
done
test -z "$(find ops/pages \( -name '*.py' -o -name '*.timer' \) -print)"
test "$(find ops/pages -name '*.service' -printf '%f\n')" = mag-pages-reconcile.service
grep -F 'ProtectHome=yes' ops/pages/mag-pages-reconcile.service
! grep -R -E 'pages-policy.sh \*|sudo -n /usr/local/lib/mag-pages/pages-gc.sh' ops/pages
! grep -R -E 'systemctl (restart|stop) (mag-llm|deepseek)' ops/pages

