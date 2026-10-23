#!/usr/bin/env bash
# Dry-run only. Proves the blank-host script lists every phase and stays idle
# on lockdown and mag-llm.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
bash -n "$root/ops/bootstrap.sh" "$root/mag" "$root/ops/gitea/install.sh"
out=$(bash "$root/ops/bootstrap.sh" --dry-run)
for phase in packages restic users restore place-state llm-env tailscale gitea \
  build backup-install gateway tailscale-egress models deploy start-llm \
  console-lockdown-skipped; do
  grep -Fq "PHASE $phase" <<<"$out"
done
grep -Fq 'install-restic-0181.sh' <<<"$out"
grep -Fq 'ops/gitea/install.sh' <<<"$out"
grep -Fq 'ops/deploy.sh' <<<"$out"
grep -Fq 'bootstrap-models.sh' "$root/ops/bootstrap.sh"
! grep -Eq 'bash .*console-lockdown' "$root/ops/bootstrap.sh"
! grep -Eq 'systemctl (restart|stop) mag-llm' "$root/ops/bootstrap.sh"
grep -Fq 'does not restart mag-llm' "$root/ops/bootstrap.sh" || grep -Fq 'not started or restarted' "$root/ops/bootstrap.sh"
# Dry-run must not require root and must not create the staging directory.
[[ ! -d /var/tmp/mag-bootstrap-restore ]]
# Every binary deploy.sh requires must be in the bootstrap/update build list.
source "$root/ops/build-targets.sh"
for binary in "${MAG_RELEASE_BINARIES[@]}"; do
  printf '%s\n' "${MAG_BUILD_TARGETS[@]}" | grep -Fxq "$binary"
done
grep -Fq 'mag-det' <<<"$out"
grep -Fq 'MAG_RELEASE_BINARIES' "$root/ops/deploy.sh"
grep -Fq 'MAG_BUILD_TARGETS' "$root/ops/update.sh"
echo 'Bootstrap dry-run PASS'
