#!/usr/bin/env bash
# Called by .github/workflows/green.yml on every push to main. Waits until all
# other checks on the newest main commit have finished, then moves `green`
# there if every one succeeded (or was skipped). PROMOTE_WAIT=0 checks once.
#
# Gitea publishes the ten required contexts as GitHub commit statuses. When any
# of those contexts is present, that status set is authoritative for those
# names: a missing, pending, failed or duplicate context is not success, and a
# same-named Actions check-run does not override the status. With no Gitea
# statuses, check-runs alone decide.
set -euo pipefail
out="${GITHUB_OUTPUT:-/dev/null}"
echo "promoted=false" >> "$out"
wait="${PROMOTE_WAIT:-1}"
sha="$(git rev-parse origin/main)"
deadline=$((SECONDS + 5400))
step="${PROMOTE_SLEEP:-30}"
if (( wait )); then sleep $((step * 2)); fi
script_root=$(cd -- "$(dirname "$0")/../.." && pwd)
required=$(jq -c '.contexts' "$script_root/ops/gitea-ci/policy.json")
[[ $(jq 'length' <<<"$required") == 10 ]] || { echo 'check policy mismatch' >&2; exit 1; }
merge='
  def conclusion($state):
    if $state == "success" then "success"
    elif $state == "pending" or $state == null then null
    else "failure" end;
  def runstate($state):
    if $state == "pending" or $state == null then "pending" else "completed" end;
  ($status.statuses // []) as $list
  | ($list | group_by(.context) | map(select(length > 1) | .[0].context)) as $dups
  | ($list | map({key: .context, value: .state}) | from_entries) as $by
  | ($required | map(select($by[.] != null)) | length) as $seen
  | if $seen > 0 then
      [ (.check_runs // [])[]
        | select(.name != "promote-green" and .name != "deploy-live")
        | select(.name as $n | ($required | index($n)) | not) ]
      + [ $required[] | . as $n | {
            name: $n,
            status: (if (($dups | index($n)) != null) then "completed" else runstate($by[$n]) end),
            conclusion: (if (($dups | index($n)) != null) then "failure" else conclusion($by[$n]) end)
          } ]
    else
      [ (.check_runs // [])[] | select(.name != "promote-green" and .name != "deploy-live") ]
    end
'
api_get() {
  local path=$1
  if [[ ${MAG_PROMOTE_NO_GH:-0} != 1 ]] && command -v gh >/dev/null 2>&1; then
    gh api "repos/${GITHUB_REPOSITORY}/${path}"
  else
    [[ -n ${GH_TOKEN:-} ]] || { echo 'GH_TOKEN required without gh' >&2; return 1; }
    curl -fsSL --proto '=https' --max-time 30 -H "Authorization: Bearer ${GH_TOKEN}" \
      -H 'Accept: application/vnd.github+json' \
      "${GITHUB_API_URL:-https://api.github.com}/repos/${GITHUB_REPOSITORY}/${path}"
  fi
}
while :; do
  raw_runs="$(api_get "commits/${sha}/check-runs?per_page=100")"
  raw_status="$(api_get "commits/${sha}/status")"
  runs="$(jq --argjson status "$raw_status" --argjson required "$required" "$merge" <<<"$raw_runs")"
  total="$(jq length <<<"$runs")"
  pending="$(jq '[.[] | select(.status != "completed")] | length' <<<"$runs")"
  red="$(jq '[.[] | select(.status == "completed" and ((.conclusion == "success" or .conclusion == "skipped" or .conclusion == "neutral") | not))] | length' <<<"$runs")"
  echo "main ${sha}: ${total} checks, ${pending} running, ${red} not green"
  (( red == 0 )) || { echo 'red: not promoting'; exit 0; }
  if (( total > 0 && pending == 0 )); then break; fi
  (( wait )) || { echo 'not finished: not promoting'; exit 0; }
  (( SECONDS < deadline )) || { echo 'timed out: not promoting'; exit 0; }
  sleep "$step"
  git fetch -q origin main
  [[ "$(git rev-parse origin/main)" == "$sha" ]] || { echo 'main moved on: the newer push decides'; exit 0; }
done
if [[ "$(git rev-parse -q --verify origin/green || true)" == "$sha" ]]; then
  echo 'green is already there'; exit 0
fi
git push origin "${sha}:refs/heads/green"
echo "promoted=true" >> "$out"
echo "green -> ${sha}"
