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

assert '--physcpubind=42-55 --membind=1' in qwen  # HT siblings of Q4's 14-27
assert '--threads 14 --threads-batch 14 --numa numactl' in qwen
assert 'CPUAffinity=42-55' in unit
assert 'AllowedCPUs=42-55' in unit
assert 'NUMAPolicy=bind' in unit and 'NUMAMask=1' in unit
assert '--ctx-size 32768 --parallel 1' in qwen
assert '--cache-ram 16384' in qwen and '--jinja --reasoning-format auto' in qwen
assert '--lazy-mode off -ot per_layer_token_embd=CPU' in qwen
assert 'Environment=MAG_ROOM_MAX_STEPS=256' in conf
assert 'Environment=MAG_ROOM_RUN_BUDGET_S=43200' in conf
assert 'Environment=MAG_ROOM_SINGLE_BOT=' in conf
assert 'Environment=MAG_ROOM_DUAL_NUMA=1' in conf
assert 'Environment=MAG_ROOM_TRANSCRIPT_BYTES=16000' in conf
assert 'Environment=MAG_ROOM_CONTEXT_BYTES=52000' in conf
assert 'Environment=MAG_ROOM_TOOL_CONTEXT_BYTES=6000' in conf
assert 'Environment=MAG_ROOM_REASONING_HISTORY_BYTES=4096' in conf
assert 'Environment=MAG_MCP_TOOL_DESC_BYTES=700' in conf
assert 'Environment=MAG_ROOM_QWEN_MAX_TOKENS=8192' in conf
assert 'MAG_ROOM_QWEN_MAX_TOKENS' in room and 'model == "qwen3.8-flash-next-uncensored"' in room
assert 'std::min(MAX_TOKENS, std::clamp(' in room  # valid for small-token tests
assert 'MAG_ROOM_REASONING_HISTORY_BYTES' in room
assert 'static void schedule_prewarm()' in room and 'schedule_prewarm();' in room
assert 'MAG_ROOM_PREWARM' in room and 'body["max_tokens"] = 1;' in room
assert 'MAG_ROOM_PREFIX_DISK' in room and 'prefix_slot("restore", source)' in room and 'prefix_slot("save", own)' in room
assert 'server_mutex(port)' in room and 'kv.first.rfind(port + "/", 0) == 0' in room  # cross-bot prefix only on the same server
assert 'secondary_ready && !was_secondary_ready' in room  # Qwen ready later after reboot
pty = Path('mag-room/native/guest_pty.hpp').read_text()
udp = Path('mag-room/native/browser_udp.hpp').read_text()
assert 'kill(-group, SIGWINCH)' in pty and 'if (running) terminal_.redraw();' in udp  # live repaint, no cache
assert '"MGB5"' in Path('mag-room/native/browser.cpp').read_text() and '== "MGB5"' in room
routes = Path('mag-room/native/routes.hpp').read_text()
ui = Path('mag-room/web/index.html').read_text()
assert 'path == "/api/model-radius"' in routes and 'actor.role != "owner"' in routes
assert 'static Val model_radius(' in room and 'first["n_probs"] = k;' in room
assert 'body["cache_prompt"] = false;' in room and 'body["top_k"] = 1;' in room  # siblings stay greedy, uncached
assert 'Token-ID nicht eindeutig' in room  # ik ids verified via tokenize+detokenize
assert "api('/api/model-radius'" in ui
assert 'if (!JOBS.empty() || AUTO_READY) return;' in room  # never delays a queued turn
assert 'const size_t target = limit * 65 / 100;' in room
assert 'if (prompt_wire_bytes(messages, tools) <= limit) return;' in room
assert 'utf8_suffix(t.reasoning, keep)' in room
assert 'report.reasoning += piece' in room  # full reasoning stays in report
assert 'cache_prompt"] = !deterministic' in room  # cached unless deterministic
assert 'id_slot"] = id_slot' in room and 'if (PROMPT_SLOTS > 1)' in room
assert 'MCP_MAX_BOT_TOOLS = 64' in mcp
assert 'MAG_MCP_TOOL_DESC_BYTES' in mcp and 'fn["parameters"]' in mcp
assert 'for (auto &t : mcp_bot_tools(bot_idx))' in room  # no tool removal
assert 'MAG_ROOM_BACKEND=http://127.0.0.1:8080' in Path('ops/mag-120.d/20-room-audit.conf').read_text()
assert 'ops/qwen/start.sh" /usr/local/lib/mag-qwen/start.sh' in deploy
assert 'MAG_ROOM_CONTEXT_BYTES=52000' not in Path('ops/mag-120.d/20-room-audit.conf').read_text() # legacy unchanged
assert 'mag-llm-qwen' in diag and 'prompt-cache slot=' in diag
for s in (qwen, deploy):
    assert 'systemctl restart mag-llm.service' not in s
    assert 'systemctl stop mag-llm.service' not in s
print('PASS: NUMA1 resident N-gram weights, 32k context, cached long coding without tool loss or live restarts')
PY
