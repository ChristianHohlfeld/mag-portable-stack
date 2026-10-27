#!/usr/bin/env bash
set +x
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
root=${1:?source checkout}
git -C "$root" diff --quiet && git -C "$root" diff --cached --quiet || { echo 'Refusing unreviewed tracked source changes' >&2;exit 1; }
sha=$(git -C "$root" rev-parse HEAD)
[[ $sha =~ ^[0-9a-f]{40}$ && -f $root/.gitea/workflows/mag-ci.yml ]] || exit 1
tmp=$(mktemp -d /run/mag-ci-control.XXXXXXXX);trap 'rm -rf "$tmp"' EXIT
install -m 600 /etc/credstore/mag-ci-gitea.token "$tmp/gitea"
# Local source copy preserves all tracked files and the exact reviewed commit.
git clone --bare --no-hardlinks -q "$root" "$tmp/repo.git"
export CREDENTIALS_DIRECTORY=$tmp GIT_TERMINAL_PROMPT=0
# Initialize default main only if it is absent, then install the trusted
# control branch. Later source main updates are exclusively GitHub -> Gitea.
if ! git -c credential.helper= -c core.askPass=/usr/local/lib/mag-ci/git-askpass.sh ls-remote \
 http://127.0.0.1:3000/mag-ci/mag-portable-stack.git refs/heads/main | grep -q .; then
 git -C "$tmp/repo.git" -c credential.helper= -c core.askPass=/usr/local/lib/mag-ci/git-askpass.sh push -q \
  http://127.0.0.1:3000/mag-ci/mag-portable-stack.git "$sha:refs/heads/main"
fi
git -C "$tmp/repo.git" -c credential.helper= -c core.askPass=/usr/local/lib/mag-ci/git-askpass.sh push -q \
 http://127.0.0.1:3000/mag-ci/mag-portable-stack.git "+$sha:refs/heads/mag-ci/control"
