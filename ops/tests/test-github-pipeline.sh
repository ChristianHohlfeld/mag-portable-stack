#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# GitHub-only promotion/fetch/auto-update against a disposable local git origin.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/co" 2>/dev/null
git -C "$tmp/co" config user.name fixture
git -C "$tmp/co" config user.email fixture@local.invalid
git -C "$tmp/co" config commit.gpgsign false
git -C "$tmp/co" config url."$tmp/origin.git".insteadOf https://github.com/ChristianHohlfeld/mag-portable-stack.git
git -C "$tmp/co" commit -q --allow-empty -m one
git -C "$tmp/co" push -q origin HEAD:main
git -C "$tmp/co" push -q origin HEAD:green
one=$(git -C "$tmp/co" rev-parse HEAD)
bash -n "$root/ops/ci/fetch-github-green.sh" "$root/ops/ci/promote-github.sh" \
  "$root/ops/update.sh" "$root/ops/auto-update/auto-update.sh"
ref=$(bash "$root/ops/ci/fetch-github-green.sh" "$tmp/co")
[[ "$ref" == refs/remotes/github/green ]]
[[ $(git -C "$tmp/co" rev-parse "$ref") == "$one" ]]
# GitHub main is not a deploy candidate until green advances.
git -C "$tmp/co" commit -q --allow-empty -m two
git -C "$tmp/co" push -q origin HEAD:main
two=$(git -C "$tmp/co" rev-parse HEAD)
mkdir "$tmp/state"
MAG_AUTO_UPDATE_TEST=1 MAG_AUTO_UPDATE_ROOT="$tmp/co" MAG_AUTO_UPDATE_STATE="$tmp/state" \
  bash "$root/ops/auto-update/auto-update.sh" > "$tmp/output" 2> "$tmp/err"
[[ ! -s $tmp/output ]]
grep -Fq 'ahead of green' "$tmp/err"
# Superseded workflow cannot promote old main.
(cd "$tmp/co"; GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack \
 GITHUB_SERVER_URL=https://github.com GITHUB_SHA="$one" \
 bash "$root/ops/ci/promote-github.sh")
[[ $(git -C "$tmp/origin.git" rev-parse refs/heads/green) == "$one" ]]
# New SHA gets promoted by GitHub after the workflow release guard.
(cd "$tmp/co"; GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack \
 GITHUB_SERVER_URL=https://github.com GITHUB_SHA="$two" \
 bash "$root/ops/ci/promote-github.sh")
[[ $(git -C "$tmp/origin.git" rev-parse refs/heads/green) == "$two" ]]
[[ $(MAG_AUTO_UPDATE_TEST=1 MAG_AUTO_UPDATE_ROOT="$tmp/co" MAG_AUTO_UPDATE_STATE="$tmp/state" \
  bash "$root/ops/auto-update/auto-update.sh") == "$two" ]]
grep -Fq 'fetch-github-green.sh' "$root/ops/update.sh"
grep -Fq 'fetch-github-green.sh' "$root/ops/auto-update/auto-update.sh"
grep -Fq 'promote-github.sh' "$root/.github/workflows/green.yml"
! grep -Fq 'fetch-gitea-green.sh' "$root/ops/update.sh" "$root/ops/auto-update/auto-update.sh"
grep -Fq 'if: false' "$root/.gitea/workflows/green.yml"
grep -Fq 'if: false' "$root/.gitea/workflows/host-runner.yml"
# Retry state is scoped to the failed SHA, with bounded backoff. A new
# promoted green cannot be blocked by the previous SHA's failure marker.
source "$root/ops/auto-update/failure-state.sh"
STATE="$tmp/state"
mag_record_failure "$one" build
[[ $(mag_retry_count) == 1 ]]
[[ $(mag_retry_seconds 1) == 120 ]]
[[ $(mag_retry_seconds 2) == 300 ]]
[[ $(mag_retry_seconds 3) == 900 ]]
[[ $(mag_retry_seconds 4) == 3600 ]]
[[ $(mag_retry_remaining "$one") -gt 0 ]]
[[ $(mag_retry_remaining "$two") == 0 ]]
mag_record_failure "$one" deploy
[[ $(mag_retry_count) == 2 ]]
[[ $(cat "$STATE/failed-stage") == deploy ]]
mag_record_failure "$two" build
[[ $(mag_retry_count) == 1 ]]
[[ $(mag_retry_remaining "$two") -gt 0 ]]
# A formerly failed green can retry after 2 minutes, without waiting an
# unconditional 60 minutes, but not on every two-minute timer tick.
touch -d '3 minutes ago' "$STATE/failed-sha"
[[ $(mag_retry_remaining "$two") == 0 ]]
[[ $(MAG_AUTO_UPDATE_TEST=1 MAG_AUTO_UPDATE_ROOT="$tmp/co" MAG_AUTO_UPDATE_STATE="$STATE" \
  bash "$root/ops/auto-update/auto-update.sh") == "$two" ]]
grep -Fq 'deploy_rc == 75' "$root/ops/auto-update/apply.sh"
grep -Fq 'mag_record_failure' "$root/ops/auto-update/apply.sh"
grep -Fq 'rm -f "$STATE/failed-sha" "$STATE/failed-count" "$STATE/failed-stage"' "$root/ops/auto-update/apply.sh"
echo 'GitHub-only pipeline PASS'
