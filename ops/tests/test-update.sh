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
forced=$(MAG_DEPLOY_DEADLINE_S=30 MAG_ROOM_LAYOUT=mixed bash "$root/ops/update.sh" --dry-run)
grep -Fq 'sudo env' <<<"$forced"
grep -Fq 'MAG_DEPLOY_DEADLINE_S=30' <<<"$forced"
grep -Fq 'MAG_ROOM_LAYOUT=mixed' <<<"$forced"
! grep -Fq 'MAG_DEPLOY_DRAIN_S' "$root/ops/update.sh"
! grep -Fq 'MAG_DEPLOY_FORCE' "$root/ops/update.sh"
! grep -Fq 'drain_room()' "$root/ops/deploy.sh"
! grep -Fq 'exit 75' "$root/ops/deploy.sh"
grep -Fq 'Deploy: sofortiger Room-Release-Wechsel' "$root/ops/deploy.sh"
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
# Immediate deploy: no room polling, no deferred exit code; queue/workers are
# interrupted by the service switch rather than delaying an update.
grep -Fq 'deploy_rc' "$root/ops/update.sh"
! grep -Fq 'if drain_room; then' "$root/ops/deploy.sh"
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
mistral = bot("mistral-large4", "Mistral", bot_id="qwen-numa0", slot=1, numa=0)

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
    probe("glm", [mistral, q2], True,
          "Release ready: Mistral NUMA0 (:8191) + GLM-Q2 NUMA1 (:8192); Q1 disabled", False)
    probe("glm", [q1, q2], False, "expected Mistral NUMA0 slot 1", False)
    probe("glm", [ds, q2], False, "expected Mistral NUMA0 slot 1", False)
    probe("glm", [q1], False, "expected exactly two room bots", False)
    probe("glm", [q1, q2, qwen], False, "expected exactly two room bots", False)
    parked_q1 = dict(q1, enabled=False)
    probe("glm", [mistral, q2, parked_q1], True,
          "Release ready: Mistral NUMA0 (:8191) + GLM-Q2 NUMA1 (:8192); Q1 disabled", False)
    probe("mixed", [ds, qwen], True, "Release ready: DeepSeek and Qwen")
    probe("qwen", [ds, qwen], True, "Release ready: DeepSeek and Qwen")
    probe("mixed", [ds, q2], False, "expected live DeepSeek and Qwen")
    probe("unexpected", [q1,q2], False, "unknown active NUMA1 room layout")

PY
echo 'Update dry-run PASS'
