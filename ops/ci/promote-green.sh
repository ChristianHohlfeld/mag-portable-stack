#!/usr/bin/env bash
# Called by .github/workflows/green.yml on every push to main. Waits until all
# other check runs on the newest main commit have finished, then moves `green`
# there if every one succeeded (or was skipped). PROMOTE_WAIT=0 checks once.
set -euo pipefail
out="${GITHUB_OUTPUT:-/dev/null}"
echo "promoted=false" >> "$out"
wait="${PROMOTE_WAIT:-1}"
sha="$(git rev-parse origin/main)"
deadline=$((SECONDS + 5400))
step="${PROMOTE_SLEEP:-30}"
if (( wait )); then sleep $((step * 2)); fi
filter='[.check_runs[] | select(.name != "promote-green" and .name != "deploy-live")]'
# gh on GitHub-hosted runners; plain curl + GH_TOKEN on a self-hosted runner
# (the MAG host) where gh may not be installed.
check_runs() {
  if [[ ${MAG_PROMOTE_NO_GH:-0} != 1 ]] && command -v gh >/dev/null 2>&1; then
    gh api "repos/${GITHUB_REPOSITORY}/commits/${sha}/check-runs?per_page=100" --jq "$filter"
  else
    [[ -n ${GH_TOKEN:-} ]] || { echo 'GH_TOKEN required without gh' >&2; return 1; }
    curl -fsSL --proto '=https' --max-time 30 -H "Authorization: Bearer ${GH_TOKEN}" \
      -H 'Accept: application/vnd.github+json' \
      "${GITHUB_API_URL:-https://api.github.com}/repos/${GITHUB_REPOSITORY}/commits/${sha}/check-runs?per_page=100" |
      jq "$filter"
  fi
}
while :; do
  runs="$(check_runs)"
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
