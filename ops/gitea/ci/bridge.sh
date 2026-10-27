#!/usr/bin/env bash
# Trusted controller, never executed from a PR checkout. Credentials are
# systemd LoadCredential files; none are supplied to workflow processes.
set -euo pipefail
umask 077
GH=https://api.github.com/repos/ChristianHohlfeld/mag-portable-stack
GT=http://127.0.0.1:3000/api/v1/repos/chris/mag-portable-stack-ci
STATE=${STATE_DIRECTORY:-/var/lib/mag-ci-bridge}
: "${CREDENTIALS_DIRECTORY:?systemd credentials required}"
mkdir -p "$STATE"
exec 9>"$STATE/lock"; flock -n 9 || exit 0
tmp=$(mktemp -d); trap 'rm -rf -- "$tmp"' EXIT
printf 'Authorization: Bearer %s\n' "$(tr -d '\r\n' < "$CREDENTIALS_DIRECTORY/github")" > "$tmp/gh"
printf 'Authorization: token %s\n' "$(tr -d '\r\n' < "$CREDENTIALS_DIRECTORY/gitea")" > "$tmp/gt"
api() {
 local host=$1 method=$2 path=$3; shift 3
 local base=$GH hdr=$tmp/gh
 [[ $host == gt ]] && { base=$GT; hdr=$tmp/gt; }
 curl -fsS --max-time 30 -X "$method" -H @"$hdr" -H 'Content-Type: application/json' "$base$path" "$@"
}
# Git HTTP headers are files supplied to git via a temporary config. They
# cannot appear in ps, remotes, repository config or error messages.
printf '[http "https://github.com/"]\n extraHeader = Authorization: Basic %s\n' "$(printf 'x-access-token:%s' "$(tr -d '\r\n' < "$CREDENTIALS_DIRECTORY/github")" | base64 -w0)" > "$tmp/gitconfig"
printf '[http "http://127.0.0.1:3000/"]\n extraHeader = Authorization: Basic %s\n' "$(printf 'chris:%s' "$(tr -d '\r\n' < "$CREDENTIALS_DIRECTORY/gitea")" | base64 -w0)" >> "$tmp/gitconfig"
export GIT_CONFIG_GLOBAL="$tmp/gitconfig" GIT_TERMINAL_PROMPT=0
[[ -d $STATE/mirror.git ]] || git init -q --bare "$STATE/mirror.git"
git -C "$STATE/mirror.git" fetch -q --no-tags https://github.com/ChristianHohlfeld/mag-portable-stack.git '+refs/heads/*:refs/heads/*' '+refs/pull/*/head:refs/mag-pull/*'
# Only this fixed GitHub repository is ever mirrored. Exclude green so that
# moving the release marker cannot cause recursive CI.
api gh GET /branches/main > "$tmp/main"
main=$(jq -er .commit.sha "$tmp/main")
printf 'main %s\n' "$main" > "$tmp/refs"
page=1
while :; do
 api gh GET "/pulls?state=open&per_page=100&page=$page" > "$tmp/prs"
 jq -r '.[] | select(.head.repo.full_name=="ChristianHohlfeld/mag-portable-stack") | "ci-pr-\(.number) \(.head.sha)"' "$tmp/prs" >> "$tmp/refs"
 [[ $(jq length "$tmp/prs") == 100 ]] || break
 page=$((page+1))
done
while read -r branch sha; do
 [[ $sha =~ ^[a-f0-9]{40}$ && $branch =~ ^(main|ci-pr-[0-9]+)$ ]] || exit 1
 git -C "$STATE/mirror.git" cat-file -e "$sha^{commit}"
 git -C "$STATE/mirror.git" push -q --force http://127.0.0.1:3000/chris/mag-portable-stack-ci.git "$sha:refs/heads/$branch"
 # Every copied validation workflow runs on every mirrored ref. Missing,
 # queued, cancelled, skipped and unknown results never produce green.
 state=pending
 expected=(backup.yml e2e-bootstrap.yml image-paste.yml native.yml pages-native.yml private-nginx.yml room-e2e.yml speech.yml)
 count=0; failed=0
 for workflow in "${expected[@]}"; do
  api gt GET "/actions/workflows/$workflow/runs?head_sha=$sha&limit=100&page=1" > "$tmp/runs"
  result=$(jq -r --arg sha "$sha" '[.workflow_runs[] | select(.head_sha==$sha)] | sort_by(.id) | last | .conclusion // "pending"' "$tmp/runs")
  case $result in success) count=$((count+1));; pending) :;; *) failed=1;; esac
 done
 if ((failed)); then state=failure; elif ((count==${#expected[@]})); then state=success; fi
 jq -n --arg state "$state" --arg url 'https://git.mag.stardenburg.de/chris/mag-portable-stack-ci/actions' '{state:$state,context:"gitea/ci",description:"MAG Gitea validation suite",target_url:$url}' > "$tmp/status"
 api gh POST "/statuses/$sha" --data-binary @"$tmp/status" >/dev/null
 if [[ $branch == main && $state == success ]]; then
  # Compare current main again immediately before promotion. Never promote a
  # PR, an old main, or an incomplete suite; auto-update still follows green.
  latest=$(api gh GET /branches/main | jq -er .commit.sha)
  [[ $latest == "$sha" ]] || continue
  jq -n --arg sha "$sha" '{sha:$sha,force:false}' > "$tmp/ref"
  if ! api gh GET /git/ref/heads/green >/dev/null; then
   jq -n --arg sha "$sha" '{ref:"refs/heads/green",sha:$sha}' > "$tmp/newref"
   api gh POST /git/refs --data-binary @"$tmp/newref" >/dev/null
  else
   api gh PATCH /git/refs/heads/green --data-binary @"$tmp/ref" >/dev/null
  fi
 fi
done < "$tmp/refs"
