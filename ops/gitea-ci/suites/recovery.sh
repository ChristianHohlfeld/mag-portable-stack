#!/usr/bin/env bash
# Original backup.yml / recovery: same tests, one shared native build inside a disposable VM.
set -euo pipefail
export RUNNER_ENVIRONMENT=github-hosted MAG_E2E_SHOTS=/work/screenshots
export GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack
test -f CMakeLists.txt
test -d .git
printf "\n== %s\n" "shell syntax, executable hook and no destructive model action"
bash -n ops/backup/*.sh ops/backup/tests/*.sh ops/deploy.sh
test "$(grep -Ec '^[a-f0-9]{64}[[:space:]]+' ops/backup/models.sha256)" -eq 5
test "$(grep -Ec '^[a-f0-9]{64}[[:space:]]+' ops/backup/models-iq3.sha256)" -eq 4
grep -Fq 'sha256sum --check' ops/backup/bootstrap-models.sh
grep -Fq '/var/lib/tailscale' ops/backup/backup.sh
grep -Fq '/var/spool/cron/crontabs' ops/backup/backup.sh
grep -Fq 'mag_systemctl start mag-backup-deploy.service' ops/deploy.sh
! grep -Fq 'mag-backup-clear.timer' mag-room/native/routes.hpp
grep -Fq 'actor.role != "owner"' mag-room/native/routes.hpp
! grep -R -E '(stop|restart) mag-llm.service' ops/backup
! grep -R -q 'openssl rand -hex 32' ops/backup/install.sh
grep -Fq -- '--insecure-no-password' ops/backup/common.sh
test -f ops/backup/migrate-passwordless.sh

printf "\n== %s\n" "Validate calendar and systemd units"
for f in ops/backup/units/*.timer; do
  expr=$(sed -n 's/^OnCalendar=//p' "$f")
  [[ -z "$expr" ]] || systemd-analyze calendar "$expr" >/dev/null
done
for f in ops/backup/units/*.service ops/backup/units/*.timer; do
  systemd-analyze verify "$PWD/$f" 2>&1 | tee /tmp/mag-unit-verify.out
  ! grep -E 'Failed to parse calendar|Unknown key|Invalid argument' /tmp/mag-unit-verify.out
done
grep -Fq 'NUMAMask=1' ops/backup/units/mag-backup-daily.service
for f in ops/backup/units/mag-backup-{daily,deploy,clear,weekly,monthly,restore-test}.service; do
  grep -Fq 'CPUQuota=15%' "$f"
  grep -Fq 'MemoryMax=2G' "$f"
  grep -Fq 'MemorySwapMax=0' "$f"
  grep -Fq 'Nice=19' "$f"
done
grep -Fq 'CPUAffinity=14-27 42-55' ops/backup/units/mag-backup-clear.service

printf "\n== %s\n" "Migrate actual password-protected repo to no-password without data loss"
bash ops/backup/tests/test-passwordless-migrate.sh
printf "\n== %s\n" "Disposable repository with Gitea SQLite, room and Pages restore"
if bash ops/ci/backup-affected.sh; then
  bash ops/backup/tests/test-backup.sh
else
  echo 'backup restore e2e skipped (no backup/ path change)'
fi
printf "\n== %s\n" "Empty legacy Gitea directory does not hide real repositories"
bash ops/backup/tests/test-gitea-root.sh
