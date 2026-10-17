#!/usr/bin/env bash
# Static guards for the action runners: pinned downloads, unprivileged capped
# units, deploy only through the auto-update path, models never touched.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
r="$root/ops/runners"
bash -n "$r/install.sh" "$root/ops/auto-update/apply.sh" "$root/ops/auto-update/auto-update.sh"
grep -Eq '^GITEA_RUNNER_SHA=[0-9a-f]{64}$' "$r/install.sh"
grep -Eq '^GH_RUNNER_SHA=[0-9a-f]{64}$' "$r/install.sh"
grep -Fq 'sha256sum -c' "$r/install.sh"
for unit in "$r/mag-gitea-runner.service" "$r/mag-github-runner.service"; do
  for line in 'NoNewPrivileges=yes' 'ProtectSystem=strict' 'ProtectHome=yes' 'MemoryMax=' 'CPUQuota=' 'OOMScoreAdjust=1000'; do
    grep -Fq "$line" "$unit"
  done
  grep -Eq '^User=mag-(gh)?runner$' "$unit"
  grep -Eq '^InaccessiblePaths=.*/etc/credstore.*/var/lib/magbot.*/var/lib/mag-llm' "$unit"
done
grep -Fq 'Unit=mag-auto-update.service' "$r/mag-deploy-request.path"
grep -Fq 'PathModified=/var/lib/mag-deploy/request' "$r/mag-deploy-request.path"
! grep -Eq 'sudo|sudoers' "$r/install.sh" "$r"/*.service
! grep -Eq 'mag-llm(\.service|-iq3)|systemctl (stop|restart) mag-llm' "$r"/* "$root/ops/auto-update"/*
grep -Fq 'ops/runners/install.sh' "$root/ops/auto-update/apply.sh"
grep -Fq 'exec bash "$ROOT/ops/auto-update/apply.sh"' "$root/ops/auto-update/auto-update.sh"
grep -Fq "vars.MAG_SELF_HOSTED == 'on'" "$root/.github/workflows/deploy-mag.yml"
grep -Fq "workflow_run.conclusion == 'success'" "$root/.github/workflows/deploy-mag.yml"
grep -Fq 'mag:host' "$r/gitea-runner.yaml"
echo 'Runner guards PASS'
