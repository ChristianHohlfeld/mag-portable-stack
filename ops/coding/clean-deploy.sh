#!/usr/bin/env bash
# Operator deploy of the coding room from the main checkout (~/mag-stack), as chris.
# Used by the live cutovers of #263-#267 (formerly ~/clean-run.sh / ~/run-numa0.sh).
#   bash ops/coding/clean-deploy.sh          full: rebuild pinned runtime, stage GGUFs, deploy
#   bash ops/coding/clean-deploy.sh --fast   GGUFs/runtime already staged: build room, deploy
# Never sets MAG_DEPLOY_FORCE; ops/coding/layout.sh switches each NUMA node only
# once its model is SHA-verified (NUMA1 keeps its current model until Flint is; NUMA0 Qwen3.8 is left resident).
set -euo pipefail
[[ $(id -un) == chris ]] || { echo "clean-deploy: run as chris" >&2; exit 4; }
# Builds, staging and hashing stay off the model cores 0-27 (HT siblings
# 28-55 only). Services restarted by deploy.sh are spawned by systemd and do
# not inherit this affinity.
if [[ -z ${MAG_BUILD_PINNED:-} ]] && command -v taskset >/dev/null; then
  MAG_BUILD_PINNED=1 exec taskset -c "${MAG_BUILD_CPUS:-28-55}" nice -n 19 bash "$0" "$@"
fi
fast=0; [[ ${1:-} == --fast ]] && fast=1
cd "$(dirname -- "$(readlink -f "$0")")/../.."
git fetch origin main && git merge --ff-only origin/main
git log -1 --oneline
(( fast )) || nice -n 19 ionice -c 3 bash ops/qwen/build-runtime.sh
(( fast )) || nice -n 19 ionice -c 3 bash ops/coding/build-runtime.sh
bash ops/update.sh --no-pull --no-deploy --no-auto
(( fast )) || nice -n 19 ionice -c 3 bash ops/coding/prepare.sh
sudo -n env MAG_ROOM_LAYOUT=coding MAG_DEPLOY_STALE_S=0 bash ops/deploy.sh
echo CLEAN_RUN_DONE
