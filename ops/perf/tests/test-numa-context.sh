#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Static release contract. Hardware throughput must be measured on MAG itself.
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash -n ops/qwen/start.sh ops/perf/diagnose-ai.sh ops/deploy.sh ops/boot/ensure-boot.sh
python3 -I - <<'PY'
from pathlib import Path
glm = Path('ops/glm/start.sh').read_text()
glm_unit = Path('ops/glm/mag-llm-glm@.service').read_text()
glm_q1 = Path('ops/glm/q1.env').read_text()
glm_q2 = Path('ops/glm/q2.env').read_text()
glm_layout = Path('ops/room/glm.env').read_text()
glm_txn = Path('ops/qwen/layout.sh').read_text()
ds_node = Path('ops/room/start-ds-node.sh').read_text()
qwen = Path('ops/qwen/start.sh').read_text()
unit = Path('ops/qwen/mag-llm-qwen.service').read_text()
conf = Path('ops/mag-120.d/30-dual-numa.conf').read_text()
room = Path('mag-room/native/magroom.cpp').read_text()
mcp = Path('mag-room/native/mcp.hpp').read_text()
deploy = Path('ops/deploy.sh').read_text()
diag = Path('ops/perf/diagnose-ai.sh').read_text()

assert '--physcpubind=14-27 --membind=1' in qwen  # Qwen rollback remains untouched
# GLM has 14 decode and 28 batch threads, all local to its NUMA node.
assert '--physcpubind=0-13,28-41 --membind=0' in glm
assert '--physcpubind=14-27,42-55 --membind=1' in glm
assert '--threads 14 --threads-batch 28' in glm
for option in ('-fa on','-b 4096','-ub 1024','--load-mode dio',
               '--ctx-checkpoints 64','--checkpoint-min-step 1024',
               '--parallel 1','--temp 1.0','--top-p 0.95'):
    assert option in glm, option
for option in ('-ctk q8_0','-ctv q8_0','--cache-reuse'):
    assert option not in glm, option
assert "kv-f16-f16:ctx32768:slot0" in glm
assert 'EnvironmentFile=/etc/mag-glm/%i.env' in glm_unit
assert 'ExecStart=/usr/local/lib/mag-glm/start.sh %i' in glm_unit
assert 'GLM_CPU_TOPOLOGY=0-13,28-41' in glm_q1
assert 'GLM_CPU_TOPOLOGY=14-27,42-55' in glm_q2
assert 'GLM_PORT=8191' in glm_q1 and 'GLM_PORT=8192' in glm_q2
assert 'GLM_SLOT_DIR=/var/lib/mag-llm/slots/glm-q1' in glm_q1
assert 'GLM_SLOT_DIR=/var/lib/mag-llm/slots/glm' in glm_q2
assert 'binding=(--physcpubind=0-13,28-41 --membind=0)' in glm
assert '--mlock' not in glm
assert '--load-mode dio -fa on -b 4096 -ub 1024' in glm  # known-good #231 llama-server flags
assert 'glm-5.3-flash-iq1-m:kv-f16-f16' in glm
assert 'GLM-Q1' in glm_layout and 'GLM-Q2' in glm_layout
assert 'mag-llm-glm@q1.service' in glm_txn and 'mag-llm-glm@q2.service' in glm_txn
assert 'FORMAT=' in glm and '.kv-format' in glm and 'mag-prefix-glm-5.3-flash-slot2.bin' in glm
assert '--no-mmap --mlock --flash-attn' in ds_node and '--load-mode mlock' not in ds_node  # avoid file-cache duplication on NUMA0
assert 'if (slot_is_busy(s)) {' in room  # health probes per NUMA, not globally suppressed
assert 'if (!bot_prefix_ready(secondary)) return ids;' in room
assert 'SLOT_WARM[slot - 1].load() != 1' in room
assert 'kv.first == "description"' in room and 'bot_model_id(bot_idx).rfind("glm-5.3-flash", 0) == 0' in room
assert 'body["id_slot"] = 0;' in room and 'body["cache_prompt"] = true;' in room

assert '--threads 14 --threads-batch 14 --numa numactl' in qwen
assert 'CPUAffinity=14-27' in unit
assert 'AllowedCPUs=14-27' in unit
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
assert 'model.rfind("glm-5.3-flash", 0) != 0 && model_settings_for(model).deterministic' in room
assert 'model.rfind("glm-5.3-flash", 0) == 0) body["cache_prompt"] = true;' in room
assert 'model.rfind("glm-5.3-flash", 0) == 0) body["id_slot"] = 0;' in room
assert 'asst["reasoning_content"] = !CONFIGURED_MODEL && t.reasoning.size() > keep' in room
assert room.count('kwargs["reasoning_effort"] = "low";') == 2
assert room.count('kwargs["clear_thinking"] = true;') == 2
assert 'static void schedule_prewarm()' in room and 'schedule_prewarm();' in room
assert 'MAG_ROOM_PREWARM' in room and 'body["max_tokens"] = 1;' in room
assert 'MAG_ROOM_PREFIX_DISK' in room and 'restore_or_cold(source)' in room and 'restore_or_cold(own)' in room and 'prefix_slot("restore", file)' in room and 'prefix_slot("save", own)' in room
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
room_conf = Path('ops/mag-120.d/20-room-audit.conf').read_text()
assert 'MAG_ROOM_BACKEND=http://127.0.0.1:8080' in room_conf
assert 'Environment=MAG_ROOM_PREWARM=1' in room_conf
assert 'MAG_ROOM_PREWARM=1' in Path('ops/room/glm.env').read_text()
assert 'short_glm_input(TURN_INPUT)' in room
assert 'body["tool_choice"] = "none"' in room
assert 'max_output = std::min(max_output, 128)' in room
assert 'glm-reasoning-limit model=' in room
assert 'tool-failed bot=' in room
assert 'restart_unit["$unit"]=1' in glm_txn
assert 'if [[ $layout == glm && -z ${restart_unit[$unit]:-} ]]; then continue; fi' in glm_txn
assert 'Environment=MAG_ROOM_MAX_TOKENS=8192' in room_conf
assert 'Environment=MAG_ROOM_GLM_MAX_TOKENS=1024' in room_conf
assert 'Environment=MAG_ROOM_PREFILL_TPS=9' in room_conf
assert '/etc/systemd/system/mag-120.service.d/99-glm-fast.conf' in deploy
assert 'ops/qwen/start.sh" /usr/local/lib/mag-qwen/start.sh' in deploy
assert 'MAG_ROOM_CONTEXT_BYTES=52000' not in Path('ops/mag-120.d/20-room-audit.conf').read_text() # legacy unchanged
assert 'mag-llm-qwen' in diag and 'prompt-cache slot=' in diag
for s in (qwen, deploy):
    assert 'systemctl restart mag-llm.service' not in s
    assert 'systemctl stop mag-llm.service' not in s
print('PASS: NUMA1 resident N-gram weights, 32k context, cached long coding without tool loss or live restarts')
PY
