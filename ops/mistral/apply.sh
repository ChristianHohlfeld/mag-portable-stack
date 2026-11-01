#!/usr/bin/env bash
# Apply the staged Mistral change by restarting Mistral only; GLM-Q2 is immutable.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 4; }
ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
fail(){ echo "Mistral apply: $*" >&2; exit 4; }
systemctl is-active --quiet mag-llm-glm@q2.service || fail 'GLM-Q2 must already be active; refusing Mistral restart'
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8192/health >/dev/null || fail 'GLM-Q2 :8192 unhealthy; refusing Mistral restart'
q2_pid=$(systemctl show --property=MainPID --value mag-llm-glm@q2.service)
[[ $q2_pid =~ ^[1-9][0-9]*$ ]] || fail "cannot determine GLM-Q2 MainPID: $q2_pid"
bash "$ROOT/ops/mistral/install.sh" stage
# Staging can take time; recheck the no-touch bot immediately before the only restart.
systemctl is-active --quiet mag-llm-glm@q2.service || fail 'GLM-Q2 became inactive during staging'
[[ $(systemctl show --property=MainPID --value mag-llm-glm@q2.service) == "$q2_pid" ]] || fail 'GLM-Q2 PID changed during staging'
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8192/health >/dev/null || fail 'GLM-Q2 :8192 unhealthy after staging'
systemctl restart mag-llm-mistral-room.service
systemctl is-active --quiet mag-llm-mistral-room.service || fail 'Mistral room service did not become active'
[[ $(systemctl show --property=MainPID --value mag-llm-glm@q2.service) == "$q2_pid" ]] || fail 'GLM-Q2 PID changed; investigate immediately'
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8192/health >/dev/null || fail 'GLM-Q2 :8192 unhealthy after Mistral restart'
echo "Mistral restarted; GLM-Q2 remained active with unchanged PID $q2_pid"
