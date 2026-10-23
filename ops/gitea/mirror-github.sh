#!/usr/bin/env bash
# Pull mirror of the GitHub repo into the local Gitea (idempotent).
#   sudo bash ops/gitea/mirror-github.sh [--dry-run]
# Credentials, read from files only (never printed, never on a command line):
#   /etc/credstore/gitea-mirror.token   Gitea access token of the mirror owner
#                                       (scopes write:repository, read:user)
#   /etc/credstore/github-mirror.token  GitHub fine-grained PAT, Contents: read
#                                       (needed because the repo is private)
# Settings (env): MAG_MIRROR_OWNER (Gitea user/org, default chris),
#   MAG_MIRROR_NAME (default mag-portable-stack), MAG_MIRROR_SOURCE
#   (default https://github.com/ChristianHohlfeld/mag-portable-stack.git),
#   MAG_MIRROR_INTERVAL (default 10m), MAG_GITEA_URL (default from app.ini,
#   http://127.0.0.1:3000).
# An existing mirror is only synced; an existing non-mirror repo with the same
# name is never touched.
set -euo pipefail
OWNER=${MAG_MIRROR_OWNER:-chris}
NAME=${MAG_MIRROR_NAME:-mag-portable-stack}
SOURCE=${MAG_MIRROR_SOURCE:-https://github.com/ChristianHohlfeld/mag-portable-stack.git}
INTERVAL=${MAG_MIRROR_INTERVAL:-10m}
GITEA_TOKEN_FILE=${MAG_GITEA_TOKEN_FILE:-/etc/credstore/gitea-mirror.token}
GITHUB_TOKEN_FILE=${MAG_GITHUB_TOKEN_FILE:-/etc/credstore/github-mirror.token}
if [[ -z ${MAG_GITEA_URL:-} ]]; then
  addr=$(sed -n 's/^HTTP_ADDR *= *//p' /etc/gitea/app.ini 2>/dev/null | head -1 || true)
  port=$(sed -n 's/^HTTP_PORT *= *//p' /etc/gitea/app.ini 2>/dev/null | head -1 || true)
  MAG_GITEA_URL="http://${addr:-127.0.0.1}:${port:-3000}"
fi
API="${MAG_GITEA_URL%/}/api/v1"
[[ $OWNER =~ ^[A-Za-z0-9._-]+$ && $NAME =~ ^[A-Za-z0-9._-]+$ ]] || { echo 'mirror: invalid owner/name' >&2; exit 2; }
[[ $INTERVAL =~ ^[0-9]+[hm]$ ]] || { echo 'mirror: interval must look like 10m or 1h' >&2; exit 2; }
if [[ ${1:-} == --dry-run ]]; then
  echo "+ ensure Gitea pull mirror $OWNER/$NAME <- $SOURCE (interval $INTERVAL) via $API"
  echo "+ tokens from $GITEA_TOKEN_FILE and $GITHUB_TOKEN_FILE (not printed)"
  exit 0
fi
for f in "$GITEA_TOKEN_FILE" "$GITHUB_TOKEN_FILE"; do
  [[ -s $f && ! -L $f ]] || { echo "mirror: credential file $f missing" >&2; exit 1; }
  if [[ ${MAG_MIRROR_ALLOW_ANY_OWNER:-0} != 1 ]]; then
    [[ $(stat -c %u "$f") == 0 ]] || { echo "mirror: $f must be root-owned" >&2; exit 1; }
  fi
  case $(stat -c %a "$f") in 600|400) ;; *) echo "mirror: $f must be mode 0600 or 0400" >&2; exit 1 ;; esac
done
command -v jq >/dev/null && command -v curl >/dev/null
tmp=$(mktemp -d)
chmod 0700 "$tmp"
trap 'rm -rf -- "$tmp"' EXIT
# Header file keeps the Gitea token off the process list.
printf 'Authorization: token %s\n' "$(tr -d '\r\n' < "$GITEA_TOKEN_FILE")" > "$tmp/auth"
api() { # method path [body-file] -> prints HTTP code, body in $tmp/out
  local args=(-sS -o "$tmp/out" -w '%{http_code}' -X "$1" -H @"$tmp/auth" -H 'Accept: application/json')
  [[ -n ${3:-} ]] && args+=(-H 'Content-Type: application/json' --data-binary @"$3")
  curl "${args[@]}" "$API$2"
}

code=$(api GET "/repos/$OWNER/$NAME")
case $code in
  200)
    if [[ $(jq -r '.mirror' "$tmp/out") != true ]]; then
      echo "mirror: $OWNER/$NAME exists and is not a mirror; left unchanged" >&2
      exit 1
    fi
    code=$(api POST "/repos/$OWNER/$NAME/mirror-sync")
    [[ $code == 200 ]] || { echo "mirror: sync request failed (HTTP $code)" >&2; exit 1; }
    echo "mirror: $OWNER/$NAME exists; sync requested"
    ;;
  404)
    owner_id=$(api GET "/users/$OWNER")
    [[ $owner_id == 200 ]] || { echo "mirror: Gitea owner $OWNER not found (HTTP $owner_id)" >&2; exit 1; }
    jq -n --rawfile gh "$GITHUB_TOKEN_FILE" --arg src "$SOURCE" --arg owner "$OWNER" \
      --arg name "$NAME" --arg interval "$INTERVAL" '{
        clone_addr: $src, repo_owner: $owner, repo_name: $name, service: "github",
        mirror: true, mirror_interval: $interval, private: true,
        auth_token: ($gh | gsub("[\r\n]"; "")),
        wiki: false, issues: false, pull_requests: false, releases: true, labels: false, milestones: false, lfs: false
      }' > "$tmp/body"
    code=$(api POST /repos/migrate "$tmp/body")
    rm -f "$tmp/body"
    # The API message is not printed: it may quote the clone address.
    [[ $code == 201 ]] || { echo "mirror: migrate failed (HTTP $code); see journalctl -u gitea" >&2; exit 1; }
    echo "mirror: created pull mirror $OWNER/$NAME <- $SOURCE (every $INTERVAL)"
    ;;
  401|403) echo "mirror: Gitea token rejected (HTTP $code)" >&2; exit 1 ;;
  *) echo "mirror: Gitea API unavailable at $API (HTTP $code)" >&2; exit 1 ;;
esac
