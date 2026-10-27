#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
grep -Fq 'MAG_OWNER_CANONICAL_LOGIN' "$root/mag-room/native/access.hpp"
grep -Fq 'chris.hohlfeld@gmail.com' "$root/mag-room/native/access.hpp"
grep -Fq 'MAG_TEST_ALLOW_LOCAL_OWNER' "$root/mag-room/native/access.hpp"
grep -Fq 'actor.login.empty() || !ts_proof_valid' "$root/mag-room/native/routes.hpp"
grep -Fq 'Backup repository must be root-owned' "$root/ops/backup/common.sh"
grep -Fq 'tcp dport 22' "$root/ops/private-access/mag-console-only.nft"
! grep -Fq 'console-lockdown' "$root/ops/deploy.sh"
for sh in "$root"/ops/private-access/console-lockdown*.sh; do bash -n "$sh"; done
echo 'Owner restriction and console-only stage PASS'
