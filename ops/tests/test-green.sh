#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
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
url=""; for a in "$@"; do case "$a" in repos/*) url=$a ;; esac; done
if [[ $url == *"/status" ]]; then
  if [[ -n ${FAKE_STATUS:-} ]]; then printf '%s\n' "$FAKE_STATUS"; else printf '%s\n' '{"statuses":[]}'; fi
  exit 0
fi
printf '%s\n' "$FAKE_RUNS"
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" GITHUB_REPOSITORY=o/r
promote() { PROMOTE_WAIT=0 FAKE_RUNS="$1" FAKE_STATUS="${2-}" bash "$root/ops/ci/promote-green.sh" >/dev/null; git fetch -q origin; git rev-parse -q --verify origin/green || true; }
run() { printf '{"name":"%s","status":"%s","conclusion":%s}' "$1" "$2" "$3"; }
sha=$(git rev-parse origin/main)
[[ -z $(promote '{"check_runs":[]}') ]]
[[ -z $(promote "{\"check_runs\":[$(run native completed '"success"'),$(run nginx in_progress null)]}") ]]
[[ -z $(promote "{\"check_runs\":[$(run native completed '"success"'),$(run nginx completed '"failure"')]}") ]]
[[ $(promote "{\"check_runs\":[$(run native completed '"success"'),$(run speech completed '"skipped"'),$(run promote-green in_progress null)]}") == "$sha" ]]
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m two && git push -q origin HEAD:main && git fetch -q origin
[[ $(promote "{\"check_runs\":[$(run native completed '"cancelled"')]}") == "$sha" ]]
[[ $(promote "{\"check_runs\":[$(run native completed '"success"')]}") == "$(git rev-parse origin/main)" ]]
# Waiting mode: running first, then all green -> promoted after polling.
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m three && git push -q origin HEAD:main && git fetch -q origin
cat > "$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
url=""; for a in "$@"; do case "$a" in repos/*) url=$a ;; esac; done
if [[ $url == *"/status" ]]; then printf '%s\n' '{"statuses":[]}'; exit 0; fi
n=$(cat "$COUNTER" 2>/dev/null || echo 0); echo $((n + 1)) > "$COUNTER"
if (( n < 2 )); then st=in_progress; c=null; else st=completed; c='"success"'; fi
printf '%s\n' "{\"check_runs\":[{\"name\":\"native\",\"status\":\"$st\",\"conclusion\":$c}]}"
GH
COUNTER="$tmp/n" PROMOTE_SLEEP=0 bash "$root/ops/ci/promote-green.sh" >/dev/null
git fetch -q origin; [[ $(git rev-parse origin/green) == "$(git rev-parse origin/main)" && $(cat "$tmp/n") == 3 ]]
# Self-hosted runner without gh: same decision through curl + GH_TOKEN.
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m four && git push -q origin HEAD:main && git fetch -q origin
cat > "$tmp/bin/curl" <<'CURL'
#!/usr/bin/env bash
ok=0; url=""
for a in "$@"; do
  [[ $a == "Authorization: Bearer fake-token" ]] && ok=1
  case "$a" in *github.com*) url=$a ;; esac
done
[[ $ok == 1 ]] || exit 22
if [[ $url == */status ]]; then
  if [[ -n ${FAKE_STATUS:-} ]]; then printf '%s\n' "$FAKE_STATUS"; else printf '%s\n' '{"statuses":[]}'; fi
  exit 0
fi
if [[ -n ${FAKE_RUNS:-} ]]; then printf '%s\n' "$FAKE_RUNS"; else printf '%s\n' '{"check_runs":[{"name":"native","status":"completed","conclusion":"success"}]}'; fi
CURL
chmod +x "$tmp/bin/curl"
MAG_PROMOTE_NO_GH=1 GH_TOKEN=fake-token PROMOTE_WAIT=0 bash "$root/ops/ci/promote-green.sh" >/dev/null
git fetch -q origin; [[ $(git rev-parse origin/green) == "$(git rev-parse origin/main)" ]]
rm -f "$tmp/bin/curl"
grep -Fq "fromJSON(vars.MAG_RUNS_ON" "$root/.github/workflows/green.yml"
grep -Fq 'statuses: read' "$root/.github/workflows/green.yml"
grep -Fq 'fetch -q origin green' "$root/ops/auto-update/auto-update.sh"
grep -Fq 'merge -q --ff-only origin/green' "$root/ops/auto-update/auto-update.sh"
! grep -Eq 'origin/main' "$root/ops/auto-update/auto-update.sh"
grep -Fq 'promote-green.sh' "$root/.github/workflows/green.yml"
! grep -Fq 'workflow_run' "$root/.github/workflows/green.yml"
# Restore the URL-aware gh stub. Status calls must not advance the wait counter.
cat > "$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
url=""; for a in "$@"; do case "$a" in repos/*) url=$a ;; esac; done
if [[ $url == *"/status" ]]; then
  if [[ -n ${FAKE_STATUS:-} ]]; then printf '%s\n' "$FAKE_STATUS"; else printf '%s\n' '{"statuses":[]}'; fi
  exit 0
fi
printf '%s\n' "$FAKE_RUNS"
GH
chmod +x "$tmp/bin/gh"
statuses() { jq -c --arg state "$1" '{statuses:[.contexts[]|{context:.,state:$state}]}' "$root/ops/gitea-ci/policy.json"; }
advance() { git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "$1" && git push -q origin HEAD:main && git fetch -q origin; }
advance five
sha=$(git rev-parse origin/main)
# Statuses alone promote when every required context succeeded.
[[ $(promote '{"check_runs":[]}' "$(statuses success)") == "$sha" ]]
advance six
prev=$sha
sha=$(git rev-parse origin/main)
[[ $(promote '{"check_runs":[]}' "$(statuses failure)") == "$prev" ]]
[[ $(promote '{"check_runs":[]}' "$(statuses pending)") == "$prev" ]]
[[ $(promote '{"check_runs":[]}' "$(jq -c '{statuses:[.contexts[:-1][]|{context:.,state:"success"}]}' "$root/ops/gitea-ci/policy.json")") == "$prev" ]]
[[ $(promote '{"check_runs":[]}' "$(jq -c '{statuses:([.contexts[]|{context:.,state:"success"}]+[{context:"speech",state:"success"}])}' "$root/ops/gitea-ci/policy.json")") == "$prev" ]]
[[ $(promote '{"check_runs":[]}' "$(jq -c --arg bad speech '{statuses:[.contexts[]|{context:.,state:(if .==$bad then "failure" else "success" end)}]}' "$root/ops/gitea-ci/policy.json")") == "$prev" ]]
# A red Actions run for a context Gitea already reported does not block promotion.
[[ $(promote "{\"check_runs\":[$(run native completed '"failure"')]}" "$(statuses success)") == "$sha" ]]
advance seven
prev=$sha
sha=$(git rev-parse origin/main)
# An unrelated red check-run still blocks, even when every Gitea status passed.
[[ $(promote "{\"check_runs\":[$(run codex completed '"failure"')]}" "$(statuses success)") == "$prev" ]]
echo 'Green promotion PASS'
