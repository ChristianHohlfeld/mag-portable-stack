#!/usr/bin/env bash
# The ONLY auto-deploy source: GitHub's promoted green branch.
# Never fetch a candidate main commit, a Gitea mirror, or an untrusted origin.
set -euo pipefail
if [[ ${1:-} == --green-only ]]; then shift; fi
root="${1:?checkout directory required}"
budget="${MAG_GITHUB_FETCH_TIMEOUT:-45}"
[[ "$budget" =~ ^[0-9]+$ && "$budget" -gt 0 ]] || { echo 'github-green: invalid timeout' >&2; exit 2; }
url='https://github.com/ChristianHohlfeld/mag-portable-stack.git'
export GIT_TERMINAL_PROMPT=0
if ! timeout --kill-after=5s "${budget}s" \
    git -C "$root" -c credential.helper= fetch -q --no-tags "$url" \
      '+refs/heads/green:refs/remotes/github/green'; then
  echo 'github-green: GitHub green unavailable; keeping the deployed version' >&2
  exit 2
fi
printf '%s\n' refs/remotes/github/green
