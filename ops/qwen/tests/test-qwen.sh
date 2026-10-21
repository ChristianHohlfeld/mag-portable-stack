#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash -n ops/qwen/*.sh
python3 - <<'PY'
import ast, pathlib
ast.parse(pathlib.Path("ops/qwen/pin-model.py").read_text())
PY
# The new model requires a separate pinned upstream runtime, not the existing
# DeepSeek ik_llama.cpp binary or a live deploy swap.
grep -Fq '4a45de4f2c68f3d144f6c2431ad7c1da7e53c99e' ops/qwen/runtime.sha
grep -Fq -- '--physcpubind=42-55 --membind=1' ops/qwen/start.sh
grep -Fq -- '--numa numactl' ops/qwen/start.sh
grep -Fq 'mag-llm-iq3.service && fail' ops/qwen/start.sh
grep -Fq 'verify-model.sh' ops/qwen/start.sh
grep -Fq 'MAG_SECONDARY_MODEL=qwen' ops/boot/ensure-boot.sh
grep -Fq 'EnvironmentFile=-/etc/mag-stack/secondary-model.env' ops/mag-120.service
grep -Fq 'EnvironmentFile=-/etc/mag-stack/secondary-model.env' ops/audit/systemd/mag-audit-gateway.service
grep -Fq 'qwen3.8-flash-next-uncensored' mag-room/native/mag_audit_gateway.cpp
grep -Fq 'qwen3.8-flash-next-uncensored' mag-room/native/magroom.cpp
grep -Fq 'unknown or inactive secondary model' mag-room/native/mag_audit_gateway.cpp
! grep -Eq 'systemctl (stop|restart|kill).*mag-llm[.]service' ops/qwen/*.sh
! grep -Eq 'systemctl (stop|start|restart|kill).*mag-llm-iq3[.]service' ops/qwen/*.sh
! grep -Fq 'ops/qwen/install.sh' ops/deploy.sh
# Qwen is installed only on an already-cut-over Qwen host; loaded model not restarted.
grep -Fq 'ops/qwen/mag-llm-qwen.service' ops/deploy.sh
grep -Fq 'if [[ -r /etc/mag-stack/secondary-model.env ]]' ops/deploy.sh
grep -Fq 'STAGED ONLY' ops/qwen/install.sh
grep -Fq -- '--reasoning-format auto' ops/qwen/start.sh
grep -Fq -- '--cache-ram 16384' ops/qwen/start.sh
grep -Fq -- '--lazy-mode off -ot per_layer_token_embd=CPU' ops/qwen/start.sh
grep -Fq -- '--load-mode mlock' ops/qwen/start.sh
grep -Fq 'body["tool_choice"] = choice' ops/qwen/smoke.py
grep -Fq 'choice="auto"' ops/qwen/smoke.py
grep -Fq 'choice="required"' ops/qwen/smoke.py
grep -Fq 'tool result roundtrip' ops/qwen/smoke.py
grep -Fq 'cutover.sh" apply' ops/qwen/go-live.sh
bash -n ops/qwen/go-live.sh
grep -Fq 'enable_thinking' ops/qwen/smoke.py
grep -Fq 'MAG_SECONDARY_MODEL=qwen' ops/qwen/cutover.sh
grep -Fq 'Q4 NUMA0 unchanged' ops/qwen/cutover.sh
! grep -Eq 'systemctl (stop|restart|kill).*mag-llm[.]service' ops/qwen/cutover.sh
bash -n ops/qwen/cutover.sh
python3 - <<'PY'
import ast,pathlib
ast.parse(pathlib.Path("ops/qwen/smoke.py").read_text())
PY
bash ops/perf/tests/test-numa-context.sh
echo 'Qwen NUMA1 operator cutover + Jinja/Thinking/tools contract PASS'
