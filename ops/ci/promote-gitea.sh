#!/usr/bin/env bash
# Move refs/heads/green on the local Gitea repo to this main SHA, then ask the
# host to deploy by updating /var/lib/mag-deploy/request. The request is written
# only after Gitea confirms the ref. GitHub is never contacted.
set -euo pipefail
if [[ -z ${MAG_PROMOTE_BOUNDED:-} ]]; then
  export MAG_PROMOTE_BOUNDED=1
  exec timeout --kill-after=5s "${MAG_PROMOTE_TIMEOUT:-240}s" bash "$0" "$@"
fi

log() { echo "promote-gitea: $*" >&2; }
case "${GITHUB_SERVER_URL:-}" in
  *github.com*) log 'GitHub Actions is outside this gate; skipping.'; exit 0 ;;
esac
out="${GITHUB_OUTPUT:-/dev/null}"
echo "promoted=false" >> "$out"

sha="${MAG_PROMOTE_SHA:-${GITHUB_SHA:-}}"
api="${MAG_GITEA_API:-http://127.0.0.1:3000/api/v1}"
repo="${MAG_GITEA_REPOSITORY:-mag-ci/mag-portable-stack}"
token="${GITEA_TOKEN:-${GITHUB_TOKEN:-}}"
request="${MAG_DEPLOY_REQUEST:-/var/lib/mag-deploy/request}"

[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { log 'missing sha'; exit 1; }
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { log 'bad repository'; exit 1; }
case "$api" in
  *github.com*) log 'refusing GitHub API'; exit 1 ;;
  *@*) log 'refusing credentials in the API URL'; exit 1 ;;
  http://*|https://*) ;;
  *) log 'API URL must be http or https'; exit 1 ;;
esac

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
auth=()
if [[ -n "$token" ]]; then
  auth=(-H "Authorization: token ${token}")
fi

api_call() { # method path [json]
  local method=$1 path=$2 body=${3:-}
  local args=(-sS --max-time 20 --max-redirs 0 --proto '=http,https' --proto-redir '=http,https'
    -o "$tmp/body" -w '%{http_code}' -X "$method" "${auth[@]}"
    -H 'Accept: application/json')
  if [[ -n "$body" ]]; then
    args+=(-H 'Content-Type: application/json' --data "$body")
  fi
  curl "${args[@]}" "${api}/repos/${repo}${path}"
}

main_code=$(api_call GET /branches/main) || { log 'main lookup failed'; exit 1; }
[[ "$main_code" == 200 ]] || { log "main lookup HTTP ${main_code}"; exit 1; }
main_sha=$(jq -r '.commit.id // .commit.sha // empty' "$tmp/body")
if [[ "$main_sha" != "$sha" ]]; then
  log 'main moved on; not promoting'
  exit 0
fi

green_code=$(api_call GET /git/refs/heads/green) || { log 'green lookup failed'; exit 1; }
green_sha=$(jq -r '.object.sha // .sha // empty' "$tmp/body" 2>/dev/null || true)
if [[ "$green_code" == 200 && "$green_sha" == "$sha" ]]; then
  :
elif [[ "$green_code" == 404 ]]; then
  create=$(jq -nc --arg sha "$sha" '{ref:"refs/heads/green",sha:$sha}')
  code=$(api_call POST /git/refs "$create") || { log 'create failed'; exit 1; }
  [[ "$code" == 201 || "$code" == 200 ]] || { log "create HTTP ${code}"; exit 1; }
else
  [[ "$green_code" == 200 ]] || { log "green lookup HTTP ${green_code}"; exit 1; }
  update=$(jq -nc --arg sha "$sha" '{sha:$sha}')
  code=$(api_call PATCH /git/refs/heads/green "$update") || { log 'update failed'; exit 1; }
  [[ "$code" == 200 ]] || { log "update HTTP ${code}"; exit 1; }
fi

confirm=$(api_call GET /git/refs/heads/green) || { log 'confirm failed'; exit 1; }
[[ "$confirm" == 200 ]] || { log "confirm HTTP ${confirm}"; exit 1; }
got=$(jq -r '.object.sha // .sha // empty' "$tmp/body")
[[ "$got" == "$sha" ]] || { log 'green sha mismatch'; exit 1; }

dir=$(dirname "$request")
if [[ ! -e "$request" ]]; then
  [[ -w "$dir" ]] || { log 'deploy request missing'; exit 1; }
  : > "$request"
fi
[[ -w "$request" ]] || { log 'deploy request not writable'; exit 1; }
printf '%s %s\n' "$(date -u +%FT%TZ)" "$sha" > "$request"
echo "promoted=true" >> "$out"
log "green -> ${sha}"
