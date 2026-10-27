#!/usr/bin/env bash
# Merge gate for the host Gitea runner. When Gitea has published any of the ten
# required commit statuses, those statuses decide. With none, a local check of
# the deploy scripts and the runner CPU pin decides. GitHub is never contacted.
set -euo pipefail
if [[ -z ${MAG_GATE_BOUNDED:-} ]]; then
  export MAG_GATE_BOUNDED=1
  exec timeout --kill-after=10s "${MAG_GATE_TIMEOUT:-1200}s" bash "$0" "$@"
fi

root=$(cd -- "$(dirname "$0")/../.." && pwd)
log() { echo "gitea-gate: $*" >&2; }
case "${GITHUB_SERVER_URL:-}" in
  *github.com*) log 'GitHub Actions is outside this gate; skipping.'; exit 0 ;;
esac
sha="${MAG_GATE_SHA:-${GITHUB_SHA:-}}"
api="${MAG_GITEA_API:-http://127.0.0.1:3000/api/v1}"
repo="${MAG_GITEA_REPOSITORY:-mag-ci/mag-portable-stack}"
token="${GITEA_TOKEN:-${GITHUB_TOKEN:-}}"

[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { log 'missing sha'; exit 1; }
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { log 'bad repository'; exit 1; }
case "$api" in
  *github.com*) log 'refusing GitHub API'; exit 1 ;;
  *@*) log 'refusing credentials in the API URL'; exit 1 ;;
esac

local_gate() {
  bash -n \
    "$root/ops/auto-update/auto-update.sh" \
    "$root/ops/auto-update/apply.sh" \
    "$root/ops/update.sh" \
    "$root/ops/deploy.sh" \
    "$root/ops/ci/fetch-gitea-green.sh" \
    "$root/ops/ci/promote-gitea.sh" \
    "$root/ops/ci/gitea-gate.sh"
  python3 - "$root" <<'PY'
import pathlib, re, sys
root = pathlib.Path(sys.argv[1])
text = (root / "ops/runners/mag-gitea-runner.service").read_text()
spec = re.search(r"^CPUAffinity=(.+)$", text, re.M)
allowed = re.search(r"^AllowedCPUs=(.+)$", text, re.M)
assert spec and allowed and spec.group(1) == allowed.group(1) == "42-55", text
assert "Nice=15" in text and "IOSchedulingClass=idle" in text
forbid = set(range(0, 14)) | set(range(28, 42))
cpus = set(range(42, 56))
assert cpus.isdisjoint(forbid)
import json
policy = json.loads((root / "ops/gitea-ci/policy.json").read_text())
assert len(policy["contexts"]) == 10
PY
  log 'local gate passed'
}

required=$(jq -c '.contexts' "$root/ops/gitea-ci/policy.json")
[[ $(jq 'length' <<<"$required") == 10 ]] || { log 'check policy mismatch'; exit 1; }
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
auth=()
if [[ -n "$token" ]]; then
  auth=(-H "Authorization: token ${token}")
fi
url="${api}/repos/${repo}/commits/${sha}/status"
if ! code=$(curl -sS --max-time 20 --max-redirs 0 --proto '=http,https' --proto-redir '=http,https' \
  -o "$tmp" -w '%{http_code}' "${auth[@]}" -H 'Accept: application/json' "$url"); then
  log 'status API unreachable; running local gate'
  local_gate
  exit 0
fi
if [[ "$code" != 200 ]]; then
  log "status API HTTP ${code}; running local gate"
  local_gate
  exit 0
fi

decision=$(jq -r --argjson required "$required" '
  def rows:
    if type == "array" then .
    elif type == "object" then (.statuses // [])
    else [] end;
  rows as $all
  | [ $all[] | {context, state: (.state // .status // "")} ] as $norm
  | [$norm[] | select(.context as $c | $required | index($c) != null)] as $seen
  | if ($seen | length) == 0 then "local"
    else
      ([$required[] | . as $c
        | [$norm[] | select(.context == $c)] as $hits
        | if ($hits | length) == 1 and $hits[0].state == "success" then "ok" else "bad" end
      ] | if all(. == "ok") then "pass" else "fail" end)
    end
' "$tmp")

case "$decision" in
  pass) log 'required statuses passed'; exit 0 ;;
  local) local_gate; exit 0 ;;
  fail) log 'required statuses are not green'; exit 1 ;;
  *) log 'could not read statuses'; exit 1 ;;
esac
