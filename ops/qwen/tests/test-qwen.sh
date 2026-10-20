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
grep -Fq -- '--cpunodebind=1 --membind=1' ops/qwen/start.sh
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
! grep -Fq 'mag-llm-qwen.service' ops/deploy.sh
grep -Fq 'STAGED ONLY' ops/qwen/install.sh
echo 'Qwen NUMA1 opt-in staging contract PASS'
