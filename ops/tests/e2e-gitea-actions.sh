#!/usr/bin/env bash
# End-to-end: Gitea Actions only for bot orgs. Disposable root CI host only
# (GitHub-hosted ubuntu-24.04): installs the pinned Gitea with
# ops/gitea/install.sh (production layout, Actions still off), runs
# ops/runners/install.sh (enables Actions, pinned gitea-runner, units, nft),
# creates two bots with ops/gitea/bot-rights.sh and asserts:
#  - each bots-<agent> org gets an org-scoped runner that comes online, reruns
#    keep the registration, a rejected registration is replaced
#  - a workflow in a bot repo runs and succeeds inside the sandbox (checkout
#    from 127.0.0.1:3000, no mag-deploy, no host state, loopback narrowed)
#  - a workflow in a repo of another (non-bot) owner gets no runner
#  - no token in the output; the status helper reports everything healthy.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required (disposable CI host only)' >&2; exit 1; }
[[ ${CI:-} == true ]] || { echo 'refusing to run outside CI' >&2; exit 1; }
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
listener=
trap '[[ -n $listener ]] && kill "$listener" 2>/dev/null; rm -rf -- "$tmp"' EXIT
fail() { echo "FAIL: $*" >&2
  journalctl -u gitea -u 'mag-gitea-botrunner@*' --no-pager -n 80 >&2 || true; exit 1; }
G=(runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini)
A=http://127.0.0.1:3000/api/v1

command -v nft >/dev/null || { apt-get update -qq && apt-get install -y -qq nftables >/dev/null; }
bash "$root/ops/gitea/install.sh" >/dev/null
# Target of mag-deploy-request.path (the real one is ops/auto-update).
printf '[Service]\nType=oneshot\nExecStart=/bin/true\n' > /etc/systemd/system/mag-auto-update.service
for _ in $(seq 1 60); do curl -fsS -o /dev/null "$A/../healthz" 2>/dev/null && break; sleep 1; done
grep -q '^\[actions\]' /etc/gitea/app.ini && fail 'fresh app.ini already has [actions]'

# Bots exist before the runner install (as on mag), plus the owner chris.
"${G[@]}" admin user create --username chris --email chris@mag.local --random-password --admin --must-change-password=false >/dev/null
for b in magbot-agent1 magbot-agent3; do
  "${G[@]}" admin user create --username "$b" --email "$b@mag.local" --random-password --must-change-password=false >/dev/null
done
bash "$root/ops/gitea/bot-rights.sh" >/dev/null

bash "$root/ops/runners/install.sh" > "$tmp/install.out" 2> "$tmp/install.err" || fail "runners/install.sh exit $?"
cat "$tmp/install.out" "$tmp/install.err"
grep -Fxq 'ENABLED = true' /etc/gitea/app.ini || fail 'Actions not enabled'
grep -Fxq 'DEFAULT_ACTIONS_URL = github' /etc/gitea/app.ini || fail 'DEFAULT_ACTIONS_URL not set'
grep -q 'WARN' "$tmp/install.err" && fail 'install printed WARN'
for o in bots-agent1 bots-agent3; do
  grep -Fxq "OK: Gitea runner $o (org scope, label mag-bot:host)" "$tmp/install.out" || fail "no OK line for $o"
  systemctl is-active --quiet "mag-gitea-botrunner@$o.service" || fail "$o runner not active"
  id -nG "magrun-$o" | tr ' ' '\n' | grep -Fxq mag-deploy && fail "magrun-$o in mag-deploy"
done
[[ ! -e /etc/systemd/system/mag-gitea-runner.service && ! -s /var/lib/mag-runner/.runner ]] || fail 'host runner without opt-in'
uuid1=$(jq -r .uuid /var/lib/mag-botrunner/bots-agent1/.runner)

# Rerun: registrations kept, still only OK lines.
/usr/local/sbin/mag-gitea-bot-runners > "$tmp/rerun" 2>&1 || fail "rerun: $(cat "$tmp/rerun")"
[[ $(grep -vc '^OK: ' "$tmp/rerun") == 0 ]] || fail "rerun printed more than OK lines: $(cat "$tmp/rerun")"
[[ $(jq -r .uuid /var/lib/mag-botrunner/bots-agent1/.runner) == "$uuid1" ]] || fail 'rerun re-registered a valid runner'

# A registration Gitea no longer accepts is replaced.
f=/var/lib/mag-botrunner/bots-agent3/.runner
uuid3=$(jq -r .uuid "$f")
jq '.token = "0000000000000000000000000000000000000000"' "$f" > "$tmp/r" && cat "$tmp/r" > "$f"
/usr/local/sbin/mag-gitea-bot-runners magbot-agent3 > "$tmp/re" 2>&1 || fail "re-register: $(cat "$tmp/re")"
[[ $(jq -r .uuid "$f") != "$uuid3" ]] || fail 'rejected runner not re-registered'
systemctl restart mag-gitea-botrunner@bots-agent3.service

# Repos: one per bot org and one of chris, same trivial workflow shape.
adm=e2e-admin
"${G[@]}" admin user create --username "$adm" --email e2e@mag.local --random-password --admin --must-change-password=false >/dev/null
"${G[@]}" admin user generate-access-token --username "$adm" --token-name e2e --raw \
  --scopes write:admin,write:organization,write:repository | sed 's/^/Authorization: token /' > "$tmp/auth"
api() { curl -sS -f -H @"$tmp/auth" -H 'Content-Type: application/json' "$@"; }
python3 -m http.server 8799 --bind 127.0.0.1 --directory "$tmp" >/dev/null 2>&1 &
listener=$!
wf_bot=$(cat <<'YML'
on: [push]
jobs:
  sandbox:
    runs-on: mag-bot
    steps:
      - uses: builtin:checkout
      - run: |
          set -eux
          test -f README.md
          echo "server=${{ github.server_url }}"
          # `! cmd` alone does not trip set -e; every negation ends in || exit 1.
          ! id -nG | tr ' ' '\n' | grep -Fxq mag-deploy || exit 1
          for p in /var/lib/gitea /var/lib/mag-deploy /var/lib/mag-botrunner/bots-agent3 /etc/gitea /etc/credstore /opt; do
            ! ls "$p" >/dev/null 2>&1 || exit 1
          done
          test ! -e /dev/kvm
          curl -fsS -m 5 http://127.0.0.1:3000/api/healthz >/dev/null
          ! curl -sS -m 5 -o /dev/null http://127.0.0.1:8799/ || exit 1
          echo sandbox-ok
YML
)
api -d '{"name":"demo","auto_init":true,"private":true}' "$A/orgs/bots-agent1/repos" >/dev/null
api -d '{"name":"own","auto_init":true,"private":true}' "$A/admin/users/chris/repos" >/dev/null
for r in bots-agent1/demo:"$wf_bot" chris/own:"$wf_bot"; do
  jq -n --arg c "$(base64 -w0 <<<"${r#*:}")" '{content: $c, message: "ci"}' |
    api -d @- "$A/repos/${r%%:*}/contents/.gitea/workflows/ci.yml" >/dev/null
done
job() { api "$A/repos/$1/actions/jobs" | jq -r '.jobs[0] // {} | "\(.status) \(.conclusion) \(.runner_name)"'; }
state=
for _ in $(seq 1 90); do
  state=$(job bots-agent1/demo); [[ $state == completed* ]] && break; sleep 2
done
[[ $state == 'completed success mag-bots-agent1' ]] || {
  api "$A/repos/bots-agent1/demo/actions/jobs" | jq -c '.jobs[0]' >&2
  id=$(api "$A/repos/bots-agent1/demo/actions/jobs" | jq -r '.jobs[0].id')
  api "$A/repos/bots-agent1/demo/actions/jobs/$id/logs" >&2 || true
  fail "bot job: $state"; }
sleep 15
other=$(job chris/own)
[[ $other == 'queued null null' || $other == 'waiting null null' ]] || fail "chris job got picked up: $other"

# Status helper: healthy, no runner outside the bot orgs, no secrets.
install -m 0755 "$root/ops/runners/status.sh" /usr/local/sbin/mag-gitea-runner-status
/usr/local/sbin/mag-gitea-runner-status > "$tmp/status" || { cat "$tmp/status"; fail 'status helper unhealthy'; }
cat "$tmp/status"
grep -Eq '^bots-agent1: unit=active registered=yes gitea=(online|idle|active) name=mag-bots-agent1 labels=mag-bot$' "$tmp/status" || fail 'status line bots-agent1'
grep -Fxq 'runners outside the bot orgs: none' "$tmp/status" || fail 'foreign runners reported'
"${G[@]}" admin user list | grep -q 'mag-status-' && fail 'status helper left its admin behind'
for o in bots-agent1 bots-agent3; do
  tok=$(jq -r .token "/var/lib/mag-botrunner/$o/.runner")
  grep -rFq "$tok" "$tmp/install.out" "$tmp/install.err" "$tmp/rerun" "$tmp/re" "$tmp/status" && fail "runner token of $o printed"
done
systemd-analyze security --no-pager mag-gitea-botrunner@bots-agent1.service | tail -1
echo 'Gitea Actions bot-org e2e PASS'
