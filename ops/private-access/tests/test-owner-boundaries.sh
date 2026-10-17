#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
grep -Fq 'MAG_OWNER_CANONICAL_LOGIN' "$root/mag-room/native/access.hpp"
grep -Fq 'MAG_TEST_ALLOW_LOCAL_OWNER' "$root/mag-room/native/access.hpp"
grep -Fq 'actor.login.empty() || !ts_proof_valid' "$root/mag-room/native/routes.hpp"
grep -Fq 'Backup repository must be root-owned' "$root/ops/backup/common.sh"
grep -Fq 'tcp dport 22' "$root/ops/private-access/mag-console-only.nft"
for sh in "$root"/ops/private-access/console-lockdown*.sh; do bash -n "$sh"; done
echo 'Owner restriction and console-only stage PASS'
