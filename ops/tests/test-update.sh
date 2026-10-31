#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Dry-run only: update builds before it deploys and never touches mag-llm.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
bash -n "$root/ops/update.sh"
out=$(bash "$root/ops/update.sh" --dry-run)
for phase in pull build deploy auto-update runners; do grep -Fq "PHASE $phase" <<<"$out"; done
grep -Fq 'cmake --build' <<<"$out"
grep -Fq 'ops/deploy.sh' <<<"$out"
[[ $(grep -n 'PHASE build' <<<"$out" | cut -d: -f1) -lt $(grep -n 'PHASE deploy' <<<"$out" | cut -d: -f1) ]]
! grep -Eq 'mag-llm' "$root/ops/update.sh"
! grep -Eq 'systemctl (restart|stop|reload).*mag-llm' "$root/ops/deploy.sh"
forced=$(MAG_DEPLOY_FORCE=1 MAG_DEPLOY_DRAIN_S=5 MAG_DEPLOY_DEADLINE_S=30 MAG_ROOM_LAYOUT=mixed bash "$root/ops/update.sh" --dry-run)
grep -Fq 'sudo env' <<<"$forced"
grep -Fq 'MAG_DEPLOY_FORCE=1' <<<"$forced"
grep -Fq 'MAG_DEPLOY_DRAIN_S=5' <<<"$forced"
grep -Fq 'MAG_DEPLOY_DEADLINE_S=30' <<<"$forced"
grep -Fq 'MAG_ROOM_LAYOUT=mixed' <<<"$forced"
! grep -Fq 'MAG_DEPLOY_FORCE' <<<"$out"
grep -Fq 'deploy aborted' "$root/ops/update.sh"
grep -Fq 'deploy_rc' "$root/ops/update.sh"
bash -n "$root"/ops/auto-update/*.sh
grep -Fq 'auto-update/install.sh' <<<"$out"
grep -Fq -- '--ff-only' "$root/ops/auto-update/auto-update.sh"
grep -Fq 'failure-state.sh' "$root/ops/auto-update/auto-update.sh"
! grep -Fq 'mag_retry_remaining' "$root/ops/auto-update/auto-update.sh"
! grep -Eq 'mag-llm' "$root"/ops/auto-update/*
grep -Fq 'OnUnitInactiveSec' "$root/ops/auto-update/mag-auto-update.timer"
# deploy.sh runs as root from the timer (no SUDO_UID): every git call on the
# user-owned checkout must allow it explicitly.
! grep -E '(^|[^-])\bgit -C' "$root/ops/deploy.sh" | grep -vF 'safe.directory=' | grep -q .
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git init -q "$tmp/repo" && git -C "$tmp/repo" -c user.email=t@t -c user.name=t -c commit.gpgsign=false commit -q --allow-empty -m t
line=$(grep -E '\bgit .*rev-parse HEAD' "$root/ops/deploy.sh")
[[ $(env -u SUDO_UID GIT_TEST_ASSUME_DIFFERENT_OWNER=1 SOURCE="$tmp/repo" bash -c "${line%%>*}") =~ ^[0-9a-f]{40}$ ]]
# Post-restart local health checks wait for the listener (Type=simple units
# return from `systemctl restart` before they listen): no bare top-level curl,
# and wait_http survives a listener that comes up late.
! grep -nE '^curl .*127\.0\.0\.1:(8766|18784)' "$root/ops/deploy.sh"
grep -Fxq 'wait_http http://127.0.0.1:18784/health' "$root/ops/deploy.sh"
# First-install gateway gets the same bounded readiness wait: immediately
# curling :8080 after restarting a Type=simple unit races the port bind.
grep -Fq 'gateway_models=$(wait_http http://127.0.0.1:8080/v1/models)' "$root/ops/deploy.sh"
[[ $(grep -n 'gateway_models=$(wait_http http://127.0.0.1:8080/v1/models)' "$root/ops/deploy.sh" | cut -d: -f1) -gt \
   $(grep -n '^mag_systemctl restart mag-audit-gateway.service' "$root/ops/deploy.sh" | tail -1 | cut -d: -f1) ]]
[[ $(grep -n 'wait_http http://127.0.0.1:18784/health' "$root/ops/deploy.sh" | cut -d: -f1) -gt \
   $(grep -n 'mag_systemctl restart mag-browser-webrtc.service' "$root/ops/deploy.sh" | tail -1 | cut -d: -f1) ]]
[[ $(tail -2 "$root/ops/rollback.sh" | head -1) == 'for i in {1..30}; do curl '*'/healthz 2>/dev/null && break; sleep 1; done' ]]
mkdir "$tmp/bin"
printf '#!/bin/sh\nn=$(($(cat "%s/n" 2>/dev/null || echo 0) + 1)); echo $n > "%s/n"\n[ $n -ge 4 ] || exit 7\ncase " $* " in *" -o "*) ;; *) echo ok ;; esac\n' "$tmp" "$tmp" > "$tmp/bin/curl"
printf '#!/bin/sh\n' > "$tmp/bin/sleep"; chmod +x "$tmp/bin/curl" "$tmp/bin/sleep"
fn=$(sed -n '/^wait_http() {/,/^}/p' "$root/ops/deploy.sh")
# The room script runs curls through deploy_run (own process group). This
# harness only checks the retry, so the stub execs the command directly.
runstub='deploy_run() { shift; [[ ${1:-} == -- ]] && shift; "$@"; }'
[[ $(PATH="$tmp/bin:$PATH" bash -euc "$runstub"$'\n'"$fn"$'\nwait_http http://x/health') == ok ]]
echo 0 > "$tmp/n"; printf '#!/bin/sh\nexit 7\n' > "$tmp/bin/curl"
! PATH="$tmp/bin:$PATH" bash -euc "$runstub"$'\n'"$fn"$'\nwait_http http://x/health'
# A postponed deploy (exit 75) must fail update.sh. bash `set -e` does not
# abort on a failing command in an `if/else` list; capture deploy_rc instead.
grep -Fq '|| deploy_rc=$?' "$root/ops/update.sh"
post_rc=0
bash -c 'set -euo pipefail
deploy_rc=0
if true; then
  false || deploy_rc=75
  if (( deploy_rc )); then
    echo "deploy aborted (rc=$deploy_rc)" >&2
    exit "$deploy_rc"
  fi
fi
echo "update done"' || post_rc=$?
[[ $post_rc == 75 ]]

# Stuck busy (no token/text progress) continues; a moving reply still defers.
# Self-Talk without a hold confirmation must not sit out MAG_DEPLOY_DRAIN_S.
mkdir -p "$tmp/room"
printf 'on\n' >"$tmp/room/selftalk"
drain=$(sed -n '/^hold_selftalk_for_deploy() {/,/^# deploy_exit lives/p' "$root/ops/deploy.sh" | sed '$d')
printf '#!/bin/sh\nprintf %%s '"'"'{"busy":true,"queue":0,"activities":[{"processed":0,"total":0,"phase":"think","steps":[]}],"room":[{"role":"assistant","content":""}]}'"'"'\n' > "$tmp/bin/curl"
printf '#!/bin/sh\n' > "$tmp/bin/sleep"
chmod +x "$tmp/bin/curl" "$tmp/bin/sleep"
drain_env=(PATH="$tmp/bin:$PATH" MAG_DEPLOY_STALE_S=5 MAG_DEPLOY_DRAIN_S=900 RELEASE_ID=t
  MAG_ROOM_STATE="$tmp/room" MAG_DEPLOY_DRAIN_STATE="$root/ops/deploy-drain-state.py")
stale_rc=0
env "${drain_env[@]}" bash -euc "$drain"$'\ndrain_room' || stale_rc=$?
[[ $stale_rc == 0 ]]
cat > "$tmp/bin/curl" <<EOF
#!/bin/sh
n=\$(cat "$tmp/p" 2>/dev/null || echo 0)
n=\$((n+1))
printf '%s\n' "\$n" > "$tmp/p"
N=\$n python3 -c 'import json,os; n=int(os.environ["N"]); print(json.dumps({"busy":True,"queue":0,"user_busy":True,"selftalk_busy":False,"selftalk_held":False,"activities":[{"processed":n,"total":9,"phase":"prompt","steps":[]}],"room":[{"role":"assistant","content":"x"*n}]}))'
EOF
defer_rc=0
env "${drain_env[@]}" MAG_DEPLOY_STALE_S=60 MAG_DEPLOY_DRAIN_S=10 \
  bash -euc "$drain"$'\ndrain_room' || defer_rc=$?
[[ $defer_rc == 75 ]]
[[ ! -e $tmp/room/deploy-hold ]]
[[ ! -e $tmp/room/selftalk-resume ]]
force_rc=0
printf 'on\n' >"$tmp/room/selftalk"
env "${drain_env[@]}" MAG_DEPLOY_STALE_S=60 MAG_DEPLOY_DRAIN_S=10 MAG_DEPLOY_FORCE=1 \
  bash -euc "$drain"$'\ndrain_room' || force_rc=$?
[[ $force_rc == 0 ]]
# Self-Talk only (old room, no hold ack): drain must return 0 before the 900s budget.
cat > "$tmp/bin/curl" <<'EOF'
#!/bin/sh
printf '%s\n' '{"busy":true,"queue":2,"selftalk":true,"pending":[],"activities":[{"processed":3,"total":9,"phase":"prompt","steps":[]}],"room":[{"role":"assistant","content":"talk"}]}'
EOF
talk_rc=0
env "${drain_env[@]}" MAG_DEPLOY_STALE_S=60 MAG_DEPLOY_DRAIN_S=10 \
  bash -euc "$drain"$'\ndrain_room' || talk_rc=$?
[[ $talk_rc == 0 ]]
[[ -f $tmp/room/deploy-hold ]]
[[ -f $tmp/room/selftalk-resume ]]
# Held Self-Talk finishes at the turn boundary (second poll is idle).
cat > "$tmp/bin/curl" <<EOF
#!/bin/sh
n=\$(cat "$tmp/h" 2>/dev/null || echo 0)
n=\$((n+1))
printf '%s\n' "\$n" > "$tmp/h"
if [ "\$n" -le 2 ]; then
  printf '%s\n' '{"busy":true,"queue":0,"user_busy":false,"selftalk_busy":true,"selftalk_held":true,"pending":[],"activities":[{"processed":1,"total":2,"phase":"prompt","steps":[]}],"room":[{"role":"assistant","content":"x"}]}'
else
  printf '%s\n' '{"busy":false,"queue":0,"user_busy":false,"selftalk_busy":false,"selftalk_held":true,"pending":[],"activities":[],"room":[{"role":"assistant","content":"x"}]}'
fi
EOF
held_rc=0
env "${drain_env[@]}" MAG_DEPLOY_STALE_S=60 MAG_DEPLOY_DRAIN_S=10 \
  bash -euc "$drain"$'\ndrain_room' || held_rc=$?
[[ $held_rc == 0 ]]
talk_line=$(python3 -I "$root/ops/deploy-drain-state.py" <<'JSON'
{"busy":true,"selftalk":true,"pending":[],"queue":1}
JSON
)
[[ $talk_line == 0\ 0\ 1\ 0\ * ]]
# A running detached subagent is user work even while the main turn is idle.
task_line=$(printf '%s\n' '{"user_busy":false,"selftalk_busy":false,"selftalk_held":false,"tasks":[{"id":"t1","status":"running"}]}' | python3 -I "$root/ops/deploy-drain-state.py")
[[ $task_line == 1\ 0\ 0\ 0\ *:task-active ]]
# Deferred drain must preserve its real code (75), never invert it with '!'.
grep -Fq 'if drain_room; then' "$root/ops/deploy.sh"
! grep -Fq 'if ! drain_room; then' "$root/ops/deploy.sh"
# Exercise the *actual* embedded room-readiness gate with a stubbed room API
# and isolated active-layout file; no systemd or live model is touched.
python3 -I - "$root/ops/deploy.sh" <<'PY'
import io
import json
import pathlib
import re
import subprocess
import sys
import tempfile

deploy = pathlib.Path(sys.argv[1]).read_text()
match = re.search(
    r"""  if ! python3 - /etc/mag-stack/room-layout "\$require_deepseek" <<'PY'\n(.*?)\nPY\n""",
    deploy, re.S,
)
assert match, "embedded room readiness gate missing"
gate = match.group(1)

def bot(model, name=None, enabled=True, bot_id=None, slot=None, numa=None):
    return {"id": bot_id or model, "name": name or model, "model": model,
            "enabled": enabled, "slot": slot, "numa": numa}

ds = bot("deepseek-v4-flash-0731", "DeepSeek V4 Flash")
q1 = bot("glm-5.3-flash-iq1-m", "GLM-Q1", bot_id="deepseek-chat", slot=1, numa=0)
q2 = bot("glm-5.3-flash", "GLM-Q2", bot_id="qwen-numa1", slot=2, numa=1)
qwen = bot("qwen3.8-flash-next-uncensored", "Qwen", bot_id="qwen-numa0", slot=1, numa=0)

with tempfile.TemporaryDirectory() as directory:
    layout_path = pathlib.Path(directory) / "room-layout"
    def probe(layout, agents, accepted, expected_label, require_deepseek=True):
        layout_path.write_text(layout + "\n")
        payload = repr(json.dumps({"agents": agents}))
        stub = ("import io,json,urllib.request\n"
                "urllib.request.urlopen = lambda *args, **kwargs: "
                "io.BytesIO(" + payload + ".encode())\n")
        result = subprocess.run(
            [sys.executable, "-I", "-c", stub + gate, str(layout_path),
             "1" if require_deepseek else "0"],
            capture_output=True, text=True, timeout=15)
        assert (result.returncode == 0) is accepted, (
            layout, [(a["name"], a["model"]) for a in agents],
            result.stdout, result.stderr)
        assert expected_label in (result.stdout if accepted else result.stderr), (
            result.stdout, result.stderr)
    probe("glm", [qwen, q2], True,
          "Release ready: Qwen NUMA0 (:8191) + GLM-Q2 NUMA1 (:8192); Q1 disabled", False)
    probe("glm", [q1, q2], False, "expected Qwen NUMA0 slot 1", False)
    probe("glm", [ds, q2], False, "expected Qwen NUMA0 slot 1", False)
    probe("glm", [q1], False, "expected exactly two room bots", False)
    probe("glm", [q1, q2, qwen], False, "expected exactly two room bots", False)
    parked_q1 = dict(q1, enabled=False)
    probe("glm", [qwen, q2, parked_q1], True,
          "Release ready: Qwen NUMA0 (:8191) + GLM-Q2 NUMA1 (:8192); Q1 disabled", False)
    probe("mixed", [ds, qwen], True, "Release ready: DeepSeek and Qwen")
    probe("qwen", [ds, qwen], True, "Release ready: DeepSeek and Qwen")
    probe("mixed", [ds, q2], False, "expected live DeepSeek and Qwen")
    probe("unexpected", [q1,q2], False, "unknown active NUMA1 room layout")

PY
echo 'Update dry-run PASS'
