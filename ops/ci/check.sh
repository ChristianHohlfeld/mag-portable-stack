#!/usr/bin/env bash
# Minimal static gate for CI and before a deploy: shell syntax of everything a
# deploy runs, valid model config, and the two deploy contracts that must
# never regress (no model gating, no drain).
set -euo pipefail
cd "$(dirname "$0")/../.."
scripts=(ops/deploy.sh ops/rollback.sh ops/health.sh ops/build-targets.sh ops/prune-releases.sh
  ops/boot/ensure-boot.sh ops/models/*.sh ops/idol/*.sh ops/stt/*.sh ops/scene/*.sh ops/private-access/deploy-nginx.sh
  ops/magbot-guest-run ops/magbot-agent-ctl ops/ci/check.sh)
for f in "${scripts[@]}"; do bash -n "$f"; done
python3 -m py_compile ops/idol/worker.py ops/idol/runner.py ops/idol/mesh.py ops/scene/worker.py ops/scene/scene.py ops/scene/rig.py
# A restart of the Idol worker must leave a running stage alone, and one of
# the scene worker a film that is being rendered.
grep -qx 'KillMode=process' ops/idol/mag-idol.service
grep -qx 'KillMode=process' ops/scene/mag-scene.service
# Scenes are rendered on the CPU and cannot reach a GPU: the MI50 belongs to photo-to-3D.
grep -Fq 'scene.cycles.device = "CPU"' ops/scene/scene.py
grep -qx 'PrivateDevices=true' ops/scene/mag-scene.service
grep -qx 'AllowedCPUs=28-55' ops/scene/mag-scene.service
jq -e '.bots | to_entries | all(.value.port and .value.model.path and (.value.numa == 0 or .value.numa == 1))' \
  ops/models/models.json >/dev/null
# CPU only: no bot may be launched with a GPU offload flag.
if jq -r '.bots[] | (.args + (.draft.args // []))[]' ops/models/models.json | grep -Eq '^(--device|-ngl|--n-gpu-layers|--gpu-layers)'; then
  echo 'models.json: GPU flags are not allowed (CPU-only bots)' >&2; exit 1
fi
grep -Fq -- '--device none --n-gpu-layers 0' ops/models/run.sh
# The bots' sites stay inside the tailnet and are read-only.
grep -Fq 'deny all;' ops/bots/mag-bots.nginx.in
grep -Fq 'limit_except GET { deny all; }' ops/bots/mag-bots.nginx.in
! grep -Eq '^[[:space:]]*listen[[:space:]]+(0\.0\.0\.0|\[::\]|100\.)' ops/bots/mag-bots.nginx.in
# The vision projector stays on the CPU as well.
grep -Fq -- '--mmproj "$mmproj" --no-mmproj-offload' ops/models/run.sh
# A deploy never probes, starts, stops or waits for a model, and never drains.
if grep -nE '819[12]|mag-model@|drain_room|/api/state' ops/deploy.sh; then
  echo 'deploy.sh must not depend on model or room-busy state' >&2; exit 1
fi
# Package updates never restart the stack: the needrestart rule parses and
# names the models, the room and the Idol worker.
if command -v perl >/dev/null; then
  perl -e 'our %nrconf; do "./ops/boot/needrestart-mag.conf" or die "needrestart rule: $@$!";
           for my $unit ("mag-model\@noah.service", "mag-120.service", "mag-idol.service", "mag-scene.service", "mag-stt.service") {
             grep { $unit =~ $_ } @{$nrconf{blacklist_rc}} or die "needrestart rule misses $unit\n" }'
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
# The VM panel stays on the realtime link: the browser picture over WebRTC/UDP
# and xterm.js on the PTY. Pictures over HTTPS and single commands are only
# its fallback; xterm.js stays the published 5.5.0, byte for byte.
for word in '/api/browser-live/offer' "createDataChannel('control'" 'terminal_input' 'new Terminal('; do
  grep -Fq -- "$word" mag-room/web/app/live.js || { echo "live.js: the realtime VM view is gone ($word)" >&2; exit 1; }
done
sha256sum --quiet -c <<'SUMS'
1f991ac3b4b283ebf96e60ae23a00a52765dd3a2e46fa6fdda9f1aab032f7495  mag-room/web/static/xterm.js
ba8e6985669488981ccf40c0cefe3aba80722cb6c92de7ad628b0bd717faf2b6  mag-room/web/static/xterm.css
bdaefa370b1bfc42ee88d46fe6072400902a4d4b2d45cd93438dda9b23c97089  mag-room/web/static/xterm-addon-fit.js
SUMS
echo 'static checks ok'
