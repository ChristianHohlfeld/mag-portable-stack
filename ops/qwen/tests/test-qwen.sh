#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
set -euo pipefail
set -x
cd "$(dirname "$0")/../../.."
bash -n ops/qwen/*.sh
bash -n ops/mistral/*.sh
python3 - <<'PY'
import ast, pathlib
for name in ("ops/qwen/pin-model.py", "ops/mistral/fetch-hf-sha256.py", "ops/mistral/smoke.py"):
    ast.parse(pathlib.Path(name).read_text(), filename=name)
PY
# The new model requires a separate pinned upstream runtime, not the existing
# DeepSeek ik_llama.cpp binary or a live deploy swap.
grep -Fq '4a45de4f2c68f3d144f6c2431ad7c1da7e53c99e' ops/qwen/runtime.sha
grep -Fq "CPU_RANGE='0-13,28-41'" ops/qwen/start.sh
grep -Fq 'NUMAMask=0' ops/qwen/mag-llm-qwen-room.service
grep -Fq 'CPUAffinity=0-13 28-41' ops/qwen/mag-llm-qwen-room.service
grep -Fq 'MISTRAL_THREADS=14' ops/qwen/layout.sh
grep -Fq 'MISTRAL_MIN_RAM_GIB=225' ops/qwen/layout.sh
grep -Fq 'selected=mag-llm-mistral-room.service' ops/qwen/layout.sh
grep -Fq 'mag-llm-qwen-room.service' ops/qwen/layout.sh
grep -Fq 'GLM-Q1 still running' ops/qwen/start.sh
! grep -Eq '^[[:space:]]*(sudo[[:space:]]+)?rm[[:space:]].*(Qwen3.8-Flash-Next-Uncensored|IQ1_M)' ops/qwen/layout.sh
grep -Fq -- '--numa numactl' ops/qwen/start.sh
grep -Fq "systemctl is-active --quiet mag-llm-iq3.service && fail 'IQ3 still running; explicit operator stop required'" ops/qwen/start.sh
grep -Fq 'verify-model.sh' ops/qwen/start.sh
# Boot skips the 98 GB SHA read only via a root-owned stamp bound to inode/ctime.
grep -Fq 'STAMP=/etc/mag-qwen/model.verified' ops/qwen/verify-model.sh
grep -Fq '$(stat -c %u "$STAMP") == 0' ops/qwen/verify-model.sh
grep -Fq "%d %i %s %y %z" ops/qwen/verify-model.sh
grep -Fq 'ops/qwen/verify-model.sh" /usr/local/lib/mag-qwen/verify-model.sh' ops/deploy.sh
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
grep -Fq 'ops/qwen/layout.sh" apply' ops/deploy.sh
grep -Fq 'mag-llm-qwen-room.service' ops/boot/ensure-boot.sh
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
! grep -Fq 'preserve_thinking' ops/qwen/smoke.py
grep -Fq '"reasoning_effort": "none"' ops/qwen/smoke.py
grep -Fq '"top_k": 20' ops/qwen/smoke.py
grep -Fq '"presence_penalty": 1.5' ops/qwen/smoke.py
grep -Fq 'MAG_ROOM_MODEL_ID=mistral-large4' ops/room/glm.env
grep -Fq 'MAG_ROOM_THINKING=on' ops/room/glm.env
grep -Fq 'body["reasoning_effort"] = "low"' mag-room/native/magroom.cpp
grep -Fq 'body["top_k"] = 0' mag-room/native/magroom.cpp
grep -Fq 'body["min_p"] = 0.0' mag-room/native/magroom.cpp
grep -Fq 'room_chat_only_model' mag-room/native/magroom.cpp
grep -Fq 'LLM_ARCH_MISTRAL4' ops/mistral/build-runtime.sh
grep -Fq 'fetch-hf-sha256.py' ops/mistral/install.sh
grep -Fq 'operator may have recorded only shard 1' ops/mistral/fetch-hf-sha256.py
python3 - <<'PY'
from pathlib import Path
rows = Path("ops/mistral/SHA256SUMS").read_text().splitlines()
assert len(rows) == 6
assert rows[0].split() == ["cbfc979f130a16684be1e590009e1e7af2503b55a20ddd019c3882ff96f3beee",
                           "Mistral-Large-4-1T-A52B-UD-IQ1_S-00001-of-00006.gguf"]
assert all(len(row.split()) == 2 and len(row.split()[0]) == 64 for row in rows)
PY
grep -Fq 'MemoryMax=244G' ops/mistral/mag-llm-mistral-room.service
grep -Fq -- '--ctx-size 16384' ops/mistral/start.sh
grep -Fq -- '--mmap --jinja' ops/mistral/start.sh
grep -Fq 'mistral_large4' ops/mistral/README.md
grep -Fq 'body["temperature"] = 0.7' mag-room/native/magroom.cpp
grep -Fq 'body["top_p"] = 0.80' mag-room/native/magroom.cpp
grep -Fq 'body["top_k"] = 20' mag-room/native/magroom.cpp
grep -Fq 'body["presence_penalty"] = 1.5' mag-room/native/magroom.cpp
grep -Fq 'chat_forbidden_cut' mag-room/native/magroom.cpp
grep -Fq 'stops.push("<tool_call")' mag-room/native/magroom.cpp
! grep -Fq 'stops.push("</think>")' mag-room/native/magroom.cpp
grep -Fq 'full seven-hour service window' ops/deploy.sh
grep -Fq 'body["reasoning_effort"] = "none"' mag-room/native/magroom.cpp
grep -Fq 'GLM-Q2 PID changed during Mistral cold start' ops/qwen/layout.sh
grep -Fq 'GLM-Q2 must already be enabled' ops/qwen/layout.sh
grep -Fq 'QWEN_MIN_RAM_GIB=150' ops/qwen/layout.sh
grep -Fq 'Mistral NUMA0 + GLM-Q2 NUMA1' ops/qwen/layout.sh
grep -Fq 'MAG_SECONDARY_MODEL=qwen' ops/qwen/cutover.sh
grep -Fq 'Q4 NUMA0 unchanged' ops/qwen/cutover.sh
! grep -Eq 'systemctl (stop|restart|kill).*mag-llm[.]service' ops/qwen/cutover.sh
bash -n ops/qwen/cutover.sh
python3 - <<'PY'
import ast,pathlib
ast.parse(pathlib.Path("ops/qwen/smoke.py").read_text())
PY
bash ops/perf/tests/test-numa-context.sh
python3 ops/qwen/tests/test_layout.py
# Exercise GLM smoke quality assertions in the actual CI contract.
python3 ops/qwen/tests/test_smoke_glm.py
echo 'Mistral NUMA0 + GLM-Q2 NUMA1 room layout + chat-only Jinja contract PASS'

