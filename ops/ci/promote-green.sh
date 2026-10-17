#!/usr/bin/env bash
# Called by .github/workflows/green.yml. Moves `green` to the newest main
# commit only if every check run on it finished successfully (or was skipped).
set -euo pipefail
sha="$(git rev-parse origin/main)"
out="${GITHUB_OUTPUT:-/dev/null}"
echo "promoted=false" >> "$out"
runs="$(gh api "repos/${GITHUB_REPOSITORY}/commits/${sha}/check-runs?per_page=100" \
  --jq '[.check_runs[] | select(.name != "promote-green" and .name != "deploy-live")]')"
total="$(jq length <<<"$runs")"
pending="$(jq '[.[] | select(.status != "completed")] | length' <<<"$runs")"
red="$(jq '[.[] | select(.status == "completed" and ((.conclusion == "success" or .conclusion == "skipped" or .conclusion == "neutral") | not))] | length' <<<"$runs")"
echo "main ${sha}: ${total} checks, ${pending} running, ${red} not green"
(( total > 0 && pending == 0 && red == 0 )) || { echo 'not promoting'; exit 0; }
if [[ "$(git rev-parse -q --verify origin/green || true)" == "$sha" ]]; then
  echo 'green is already there'; exit 0
fi
git push origin "${sha}:refs/heads/green"
echo "promoted=true" >> "$out"
echo "green -> ${sha}"
