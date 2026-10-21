#!/usr/bin/env bash
# Static release contract. Hardware throughput must be measured on MAG itself.
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash -n ops/qwen/start.sh ops/perf/diagnose-ai.sh ops/deploy.sh ops/boot/ensure-boot.sh
python3 -I - <<'PY'
from pathlib import Path
qwen = Path('ops/qwen/start.sh').read_text()
unit = Path('ops/qwen/mag-llm-qwen.service').read_text()
conf = Path('ops/mag-120.d/30-dual-numa.conf').read_text()
room = Path('mag-room/native/magroom.cpp').read_text()
mcp = Path('mag-room/native/mcp.hpp').read_text()
deploy = Path('ops/deploy.sh').read_text()
diag = Path('ops/perf/diagnose-ai.sh').read_text()

assert '--cpunodebind=1 --membind=1' in qwen
assert '--threads 14 --threads-batch 14 --numa numactl' in qwen
assert 'CPUAffinity=14-27 42-55' in unit
assert 'AllowedCPUs=14-27 42-55' in unit
assert 'NUMAPolicy=bind' in unit and 'NUMAMask=1' in unit
assert '--ctx-size 16384 --parallel 1' in qwen
assert '--cache-ram 8192' in qwen and '--jinja --reasoning-format auto' in qwen
assert 'Environment=MAG_ROOM_SINGLE_BOT=' in conf
assert 'Environment=MAG_ROOM_DUAL_NUMA=1' in conf
assert 'Environment=MAG_ROOM_TRANSCRIPT_BYTES=8000' in conf
assert 'Environment=MAG_ROOM_CONTEXT_BYTES=36000' in conf
assert 'Environment=MAG_ROOM_TOOL_CONTEXT_BYTES=3500' in conf
assert 'Environment=MAG_ROOM_REASONING_HISTORY_BYTES=2400' in conf
assert 'Environment=MAG_MCP_TOOL_DESC_BYTES=240' in conf
assert 'Environment=MAG_ROOM_QWEN_MAX_TOKENS=4096' in conf
assert 'MAG_ROOM_QWEN_MAX_TOKENS' in room and 'model == "qwen3.8-flash-next-uncensored"' in room
assert 'std::min(MAX_TOKENS, std::clamp(' in room  # valid for small-token tests
assert 'MAG_ROOM_REASONING_HISTORY_BYTES' in room
assert 'utf8_suffix(t.reasoning, keep)' in room
assert 'report.reasoning += piece' in room  # full reasoning stays in report
assert 'cache_prompt"] = true' in room
assert 'id_slot"] = id_slot' in room and 'if (PROMPT_SLOTS > 1)' in room
assert 'MCP_MAX_BOT_TOOLS = 64' in mcp
assert 'MAG_MCP_TOOL_DESC_BYTES' in mcp and 'fn["parameters"]' in mcp
assert 'for (auto &t : mcp_bot_tools(bot_idx))' in room  # no tool removal
assert 'MAG_ROOM_BACKEND=http://127.0.0.1:8080' in Path('ops/mag-120.d/20-deepseek-audit.conf').read_text()
assert 'ops/qwen/start.sh" /usr/local/lib/mag-qwen/start.sh' in deploy
assert 'MAG_ROOM_CONTEXT_BYTES=36000' not in Path('ops/mag-120.d/20-deepseek-audit.conf').read_text() # legacy unchanged
assert 'mag-llm-qwen' in diag and 'prompt-cache slot=' in diag
for s in (qwen, deploy):
    assert 'systemctl restart mag-llm.service' not in s
    assert 'systemctl stop mag-llm.service' not in s
print('PASS: dual NUMA physical cores, bounded CPU context, all MCP tools preserved, no live model restarts')
PY
