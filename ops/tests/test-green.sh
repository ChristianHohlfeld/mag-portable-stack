#!/usr/bin/env bash
# promote-green.sh moves `green` only when every check on main is green, and
# the host follows `green`, not main.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/w" 2>/dev/null
cd "$tmp/w"
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m one
git push -q origin HEAD:main
git fetch -q origin
mkdir "$tmp/bin"
cat > "$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
# fake: prints $FAKE_RUNS filtered by the --jq expression
jqexpr=""; while (( $# )); do [[ $1 == --jq ]] && { jqexpr=$2; shift; }; shift; done
jq "$jqexpr" <<<"$FAKE_RUNS"
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" GITHUB_REPOSITORY=o/r
promote() { FAKE_RUNS="$1" bash "$root/ops/ci/promote-green.sh" >/dev/null; git fetch -q origin; git rev-parse -q --verify origin/green || true; }
run() { printf '{"name":"%s","status":"%s","conclusion":%s}' "$1" "$2" "$3"; }
sha=$(git rev-parse origin/main)
[[ -z $(promote '{"check_runs":[]}') ]]
[[ -z $(promote "{\"check_runs\":[$(run native completed '"success"'),$(run nginx in_progress null)]}") ]]
[[ -z $(promote "{\"check_runs\":[$(run native completed '"success"'),$(run nginx completed '"failure"')]}") ]]
[[ $(promote "{\"check_runs\":[$(run native completed '"success"'),$(run speech completed '"skipped"'),$(run promote-green in_progress null)]}") == "$sha" ]]
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m two && git push -q origin HEAD:main && git fetch -q origin
[[ $(promote "{\"check_runs\":[$(run native completed '"cancelled"')]}") == "$sha" ]]
[[ $(promote "{\"check_runs\":[$(run native completed '"success"')]}") == "$(git rev-parse origin/main)" ]]
grep -Fq 'fetch -q origin green' "$root/ops/auto-update/auto-update.sh"
grep -Fq 'merge -q --ff-only origin/green' "$root/ops/auto-update/auto-update.sh"
! grep -Eq 'origin/main' "$root/ops/auto-update/auto-update.sh"
grep -Fq 'promote-green.sh' "$root/.github/workflows/green.yml"
echo 'Green promotion PASS'
