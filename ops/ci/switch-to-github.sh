#!/usr/bin/env bash
# One-time, fail-closed cutover from Gitea deployment to GitHub green.
# Usage (on MAG, as checkout owner): bash ops/ci/switch-to-github.sh
# Does not touch model units, VMs, user data or Git remotes.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
url=https://github.com/ChristianHohlfeld/mag-portable-stack.git
cd "$root"
[[ ${EUID} -ne 0 ]] || { echo 'Run as the checkout owner, not root' >&2; exit 1; }
[[ $(git symbolic-ref --short HEAD) == main ]] || { echo 'Checkout is not on main; stopping' >&2; exit 1; }
[[ -z $(git status --porcelain --untracked-files=no) ]] || { echo 'Tracked local modifications; stopping' >&2; exit 1; }
[[ $(systemctl is-active mag-auto-update.service 2>/dev/null || true) != active ]] || {
  echo 'A deploy is already active; not interrupting it' >&2; exit 1;
}
echo 'Checking GitHub main and the GitHub green release…'
export GIT_TERMINAL_PROMPT=0
timeout --kill-after=5s 60s git -c credential.helper= fetch -q --no-tags "$url" \
  '+refs/heads/main:refs/remotes/github/main' \
  '+refs/heads/green:refs/remotes/github/green'
main=$(git rev-parse refs/remotes/github/main)
green=$(git rev-parse refs/remotes/github/green)
[[ "$main" == "$green" ]] || {
  echo "GitHub main is not yet green ($main vs. $green); trigger the 'Green main (GitHub only)' workflow and retry" >&2
  exit 2
}
git merge-base --is-ancestor HEAD refs/remotes/github/main || {
  echo 'Local checkout diverged from GitHub main; stopping before any deployment' >&2; exit 2;
}
echo 'Disabling the obsolete Gitea deployment trigger…'
sudo systemctl stop mag-deploy-request.path mag-auto-update.timer || true
# Never re-enable the old Gitea deployment path if anything below fails.
echo 'Fast-forwarding to verified GitHub green…'
git merge --ff-only refs/remotes/github/green
echo 'Building and deploying with the existing backup and rollback logic…'
bash ops/update.sh --no-pull
sudo systemctl disable --now mag-deploy-request.path >/dev/null 2>&1 || true
sudo systemctl enable --now mag-auto-update.timer
echo 'DONE: GitHub green is the only deployment source; Gitea remains for bot repos.'
