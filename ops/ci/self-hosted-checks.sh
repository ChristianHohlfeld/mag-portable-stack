#!/usr/bin/env bash
# CI checks that need no root, no apt and no browser: what the self-hosted
# runners on MAG (Gitea runner `mag`, GitHub runner `[self-hosted, mag]`) can
# run. Used by .gitea/workflows/ci.yml; the GitHub workflows run the same
# commands as separate steps. Build dependencies come from ops/bootstrap.sh.
#   bash ops/ci/self-hosted-checks.sh [--jobs N]
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
cd "$ROOT"
JOBS=2
[[ ${1:-} == --jobs ]] && JOBS=${2:?}
step() { printf '\n== %s\n' "$*"; }
step build
MAG_CI_JOBS=$JOBS bash ops/ci/native-build.sh
step 'native integration checks'
./build/test-guest-pty
./build/test-browser-recovery
./build/test-jobs-mcp
./build/test-pages-native
./build/test-guest-description
./build/test-guest-curl
./build/test-large-stdin
./build/test-gitea-account
python3 mag-room/tests/test_large_tools.py
python3 ops/ci/run-native-suite.py --emit-shards --jobs "$JOBS" --file mag-room/tests/test_native.py \
  | bash ops/ci/run-parallel.sh --jobs "$JOBS" --timeout 30
python3 mag-room/tests/test_agent_core.py
python3 ops/numa/tests/test_numa_swap.py
python3 mag-room/tests/test_tailscale_auth.py
python3 ops/audit/tests/test_native_gateway.py
bash ops/qwen/tests/test-qwen.sh
bash ops/ornith/tests/test-ornith.sh
python3 mag-room/tests/test_whois_deploy.py
step 'model ops regressions'
bash ops/deepseek/tests/test-ops-38.sh
bash ops/deepseek/tests/test-numa-isolation.sh
bash ops/deepseek/tests/test-node0-available.sh
bash ops/deepseek/tests/test-iq3-parallel.sh
bash ops/deepseek/tests/test-model-preservation.sh
step 'ops scripts'
bash -n ops/*.sh ops/magbot-guest-run ops/magbot-agent-ctl ops/bots/*.sh ops/private-access/*.sh
bash ops/bots/tests/test-bots.sh
bash ops/trust/test-trust.sh
bash ops/private-access/tests/test-owner-boundaries.sh
bash ops/tests/test-bootstrap.sh
bash ops/tests/test-update.sh
python3 ops/tests/test-deploy-drain-state.py
bash ops/tests/test-cleanup-legacy.sh
bash ops/tests/test-runners.sh
bash ops/tests/test-green.sh
bash ops/tests/test-boot.sh
bash ops/tests/test-clear-failed-units.sh
python3 ops/tailscale/tests/test-gitea-funnel.py
python3 ops/tailscale/tests/test-sni-map.py
bash ops/tests/test-retire-deepseek.sh
bash ops/tests/test-gitea-mirror.sh
if bash ops/ci/backup-affected.sh; then
  bash ops/backup/tests/test-backup.sh
else
  echo 'backup restore e2e skipped (no backup/ path change)'
fi
bash ops/backup/tests/test-gitea-root.sh
step 'browser WebRTC gateway'
(cd mag-room/webrtc && go test -mod=mod ./... && go build -mod=mod -o /tmp/mag-browser-webrtc-ci .)
if command -v node >/dev/null; then
  step 'frontend script syntax'
  python3 -c 'from pathlib import Path; s=Path("mag-room/web/index.html").read_text(); Path("/tmp/mag-room-web-ci.js").write_text(s.split("<script>")[-1].split("</script>")[0])'
  node --check /tmp/mag-room-web-ci.js
  node mag-room/tests/test_composer.cjs
fi
echo 'self-hosted checks PASS'
