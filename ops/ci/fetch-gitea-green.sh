#!/usr/bin/env bash
# Point a checkout at the Gitea green ref (main only when --green-only is absent
# and green does not exist yet). Prints the local ref that was updated.
# GitHub is never contacted. The URL must not carry credentials.
set -euo pipefail
green_only=0
if [[ ${1:-} == --green-only ]]; then
  green_only=1
  shift
fi
root="${1:?checkout}"
url="${MAG_GITEA_FETCH_URL:-http://127.0.0.1:3000/mag-ci/mag-portable-stack.git}"
budget="${MAG_GITEA_FETCH_TIMEOUT:-45}"
case "$url" in
  *github.com*) echo 'fetch-gitea: refusing GitHub' >&2; exit 1 ;;
  *@*) echo 'fetch-gitea: refusing credentials in the URL' >&2; exit 1 ;;
esac
[[ "$budget" =~ ^[0-9]+$ ]] || { echo 'fetch-gitea: bad timeout' >&2; exit 1; }

fetch_ref() { # remote-ref local-ref
  timeout --kill-after=5s "${budget}s" \
    git -C "$root" -c credential.helper= \
    fetch -q --no-tags "$url" "+${1}:${2}"
}

export GIT_TERMINAL_PROMPT=0
if fetch_ref refs/heads/green refs/remotes/gitea/green; then
  printf '%s\n' refs/remotes/gitea/green
  exit 0
fi
if (( green_only )); then
  echo 'fetch-gitea: green not reachable' >&2
  exit 2
fi
if fetch_ref refs/heads/main refs/remotes/gitea/main; then
  printf '%s\n' refs/remotes/gitea/main
  exit 0
fi
echo 'fetch-gitea: green and main not reachable' >&2
exit 2
