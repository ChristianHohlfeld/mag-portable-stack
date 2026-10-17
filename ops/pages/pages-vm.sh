#!/usr/bin/env bash
# Runs ONLY inside a bot VM as unprivileged bot. Never print credentials.
# Every target repository is owned by the VM's own pre-provisioned Gitea user.
set -euo pipefail
umask 077
action="$1"; repo="$2"; expected="$3"
[[ "$repo" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$ && "$repo" != *".."* ]] || exit 2
[[ "$expected" =~ ^magbot-[a-z][a-z0-9-]{0,31}$ ]] || exit 2
[[ "$(git config --global user.name)" == "$expected" ]] || { echo 'Owner mismatch'; exit 3; }
server=http://192.168.122.1:3001
creds="$(printf 'protocol=http\nhost=192.168.122.1:3001\n\n' | git credential fill)"
username="$(printf '%s\n' "$creds" | sed -n 's/^username=//p')"
password="$(printf '%s\n' "$creds" | sed -n 's/^password=//p')"
[[ "$username" == "$expected" && -n "$password" ]] || { echo 'Gitea identity mismatch'; exit 3; }
authfile="$(mktemp)"
work="$(mktemp -d)"
trap 'rm -f "$authfile"; rm -rf "$work"' EXIT
printf 'user = "%s:%s"\n' "$username" "$password" >"$authfile"
unset password creds
api() {
 local method="$1" endpoint="$2"; shift 2
 curl --silent --show-error --fail-with-body -K "$authfile" \
      --connect-timeout 5 --max-time 35 -X "$method" \
      -H 'Content-Type: application/json' "$@" "$server/api/v1$endpoint"
}
# This is the actual authenticated Gitea identity, not an untrusted caller field.
actual="$(api GET /user | jq -r .login)"
[[ "$actual" == "$expected" ]] || { echo 'Authenticated account is not the VM owner'; exit 3; }
if [[ "$action" == list ]]; then
  api GET '/user/repos?limit=100&page=1' |
    jq -c --arg owner "$expected" '[.[] | select(.owner.login==$owner) |
       {repo:.name,owner:.owner.login,private:.private}]'
  exit
fi
endpoint="/repos/$expected/$repo"
exists=yes
api GET "$endpoint" >"$work/repo.json" 2>/dev/null || exists=no
if [[ "$exists" == yes ]]; then
  jq -e --arg who "$expected" '.owner.login==$who' "$work/repo.json" >/dev/null ||
    { echo 'Not the repository owner'; exit 3; }
fi
if [[ "$action" == create && "$exists" == no ]]; then
  payload="$(jq -nc --arg n "$repo" '{name:$n,private:true,auto_init:true,default_branch:"main"}')"
  api POST '/user/repos' --data "$payload" >"$work/repo.json"
  exists=yes
fi
[[ "$exists" == yes ]] || { echo 'Owner repository does not exist'; exit 4; }
if [[ "$action" == verify ]]; then echo "Owner repository verified"; exit; fi
git -c credential.helper=store clone --quiet "$server/$expected/$repo.git" "$work/checkout"
dir="$work/checkout"
default="$(jq -r '.default_branch // "main"' "$work/repo.json")"
[[ "$default" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,100}$ && "$default" != *".."* ]] || exit 3
if [[ "$action" == delete ]]; then
  if git -C "$dir" ls-remote --exit-code --heads origin gh-pages >/dev/null 2>&1; then
    git -C "$dir" push --quiet origin :refs/heads/gh-pages
  fi
  git -C "$dir" checkout --quiet "$default"
  if [[ -d "$dir/docs" ]]; then
    rm -rf -- "$dir/docs"
    git -C "$dir" add -A docs
    git -C "$dir" diff --cached --quiet ||
      { git -C "$dir" commit --quiet -m 'MAG Pages: remove docs publication'; git -C "$dir" push --quiet origin "$default"; }
  fi
  echo 'Pages source deleted for owner repository'
  exit
fi
if git -C "$dir" ls-remote --exit-code --heads origin gh-pages >/dev/null 2>&1; then
  git -C "$dir" fetch --quiet origin gh-pages
  git -C "$dir" checkout --quiet -B gh-pages FETCH_HEAD
else
  git -C "$dir" checkout --quiet --orphan gh-pages
  git -C "$dir" rm --quiet -rf --ignore-unmatch .
  git -C "$dir" clean -qfd
fi
if [[ "$action" == domain ]]; then
  hostname="$4"
  [[ "$hostname" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$hostname" == *.* &&
    "$hostname" != *".."* ]] || exit 2
  printf '%s\n' "$hostname" >"$dir/CNAME"
  git -C "$dir" add CNAME
elif [[ "$action" == create || "$action" == update ]]; then
  relative="$4"
  [[ "$relative" =~ ^[a-zA-Z0-9][a-zA-Z0-9_./-]{0,170}$ &&
     "$relative" != *".."* && "$relative" != */.* && "$relative" != .* ]] || exit 2
  input="/home/bot/.mag-pages-input/$repo"
  [[ -s "$input" && ! -L "$input" ]] || { echo 'Missing staged content'; exit 5; }
  [[ "$(wc -c <"$input")" -le 1048576 ]] || exit 5
  mkdir -p "$(dirname "$dir/$relative")"
  cp "$input" "$dir/$relative"
  git -C "$dir" add -- "$relative"
else
  echo 'Unsupported Pages operation' >&2; exit 2
fi
if ! git -C "$dir" diff --cached --quiet; then
 git -C "$dir" commit --quiet -m "MAG Pages $action"
 git -C "$dir" push --quiet origin HEAD:refs/heads/gh-pages
else
 echo 'Pages already up to date'
 exit
fi
echo 'Pages source updated successfully; publisher will sync'
