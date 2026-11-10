#!/usr/bin/env bash
# Minimal static gate for CI and before a deploy: shell syntax of everything a
# deploy runs, valid model config, and the two deploy contracts that must
# never regress (no model gating, no drain).
set -euo pipefail
cd "$(dirname "$0")/../.."
scripts=(ops/deploy.sh ops/rollback.sh ops/health.sh ops/build-targets.sh ops/prune-releases.sh
  ops/boot/ensure-boot.sh ops/models/*.sh ops/idol/*.sh ops/private-access/deploy-nginx.sh
  ops/magbot-guest-run ops/magbot-agent-ctl ops/ci/check.sh)
for f in "${scripts[@]}"; do bash -n "$f"; done
python3 -m py_compile ops/idol/worker.py ops/idol/runner.py ops/idol/mesh.py
jq -e '.bots | to_entries | all(.value.port and .value.model.path and (.value.numa == 0 or .value.numa == 1))' \
  ops/models/models.json >/dev/null
# CPU only: no bot may be launched with a GPU offload flag.
if jq -r '.bots[] | (.args + (.draft.args // []))[]' ops/models/models.json | grep -Eq '^(--device|-ngl|--n-gpu-layers|--gpu-layers)'; then
  echo 'models.json: GPU flags are not allowed (CPU-only bots)' >&2; exit 1
fi
grep -Fq -- '--device none --n-gpu-layers 0' ops/models/run.sh
# The vision projector stays on the CPU as well.
grep -Fq -- '--mmproj "$mmproj" --no-mmproj-offload' ops/models/run.sh
# A deploy never probes, starts, stops or waits for a model, and never drains.
if grep -nE '819[12]|mag-model@|drain_room|/api/state' ops/deploy.sh; then
  echo 'deploy.sh must not depend on model or room-busy state' >&2; exit 1
fi
# The MI50 power cap may only ever be lowered.
grep -Fq 'if (( now > LIMIT )); then' ops/idol/power-cap.sh
# The page loads nothing from outside the release, and every script parses.
if grep -rnE 'https?://[a-z0-9.-]+\.[a-z]{2,}' mag-room/web/index.html mag-room/web/app; then
  echo 'web: external URL in the page or its scripts' >&2; exit 1
fi
if command -v node >/dev/null; then
  for f in mag-room/web/app/*.js mag-room/web/sw.js; do node --check "$f"; done
fi
echo 'static checks ok'
