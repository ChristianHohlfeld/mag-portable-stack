#!/usr/bin/env bash
# GitHub Actions is the sole promoter of GitHub green.
# The calling workflow completed the bounded release checks first.
set -euo pipefail
[[ "${GITHUB_REPOSITORY:-}" == 'ChristianHohlfeld/mag-portable-stack' ]] || {
  echo 'promote-github: unexpected repository' >&2; exit 1;
}
[[ "${GITHUB_SERVER_URL:-}" == 'https://github.com' ]] || {
  echo 'promote-github: only github.com may promote' >&2; exit 1;
}
sha="${GITHUB_SHA:?}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
out="${GITHUB_OUTPUT:-/dev/null}"
printf 'promoted=false\n' >> "$out"
# Recheck main: a superseded workflow must never roll green backwards.
git fetch -q --no-tags origin main
main="$(git rev-parse FETCH_HEAD)"
if [[ "$main" != "$sha" ]]; then
  echo "promote-github: main moved to $main; keeping green"
  exit 0
fi
# GitHub rejects a non-fast-forward update; never force a promotion.
git push origin "$sha:refs/heads/green"
printf 'promoted=true\n' >> "$out"
echo "promote-github: green -> $sha"
