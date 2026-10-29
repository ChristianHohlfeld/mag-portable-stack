# GLM-5.3-Flash on NUMA1 (room layout `glm`)

Owner decision (Chris, 2026-10-29): GLM-5.3-Flash replaces Qwen3.8 Uncensored
as the NUMA1 room model. DeepSeek V4 Flash stays on NUMA0 and is never touched.
Qwen is only removed from the stack: its GGUF, unit file and pinned digest stay
on disk, and `MAG_ROOM_LAYOUT=mixed` brings it back through the same transaction.

| | |
|---|---|
| Model | `GLM-5.3-Flash-IQ1_M` (15 split GGUFs, ~66 GB), `/home/chris/models/GLM-5.3-Flash-GGUF-IQ1_M/` |
| llama.cpp arch | `glm5-next` (hybrid KDA + MLA, DSA indexer, MoE) |
| Runtime | the pinned upstream llama.cpp of Qwen (`ops/qwen/runtime.sha`, `…/qwen4exp/<sha>-mag-cp1`) — it contains `src/models/glm5-next.cpp`; no extra build |
| Unit | `mag-llm-glm.service` → `/usr/local/lib/mag-glm/start.sh`, 127.0.0.1:8192, cores 14-27, memory bound to node1, `--load-mode mlock`, 32k ctx, 1 slot, ctx checkpoints |
| Room id / label | `glm-5.3-flash` / `GLM-5.3 Flash` (`ops/room/glm.env`, preset `deepseek-glm-dual-numa`) |
| Integrity | `model.sizes`: exact byte size of all 15 parts + GGUF header (no upstream SHA pinned yet) |

## How it goes live

`ops/room/layout` in the repository declares the desired layout (`glm`). Every
deploy (auto-update from GitHub green, or `ops/update.sh`) runs
`ops/qwen/layout.sh apply`, which:

1. stages and verifies GLM (`ops/glm/install.sh refresh`) before NUMA1 is touched,
2. stops the other NUMA1 units (Qwen / DeepSeek-NUMA1), starts GLM, waits for `/health`,
3. runs the live acceptance `ops/qwen/smoke.py --port 8192 --model glm-5.3-flash`
   (chat, prompt-cache reuse, auto/required/streaming tool calls, tool result
   round trip, `reasoning_content`),
4. on any failure restores the previous NUMA1 unit, config and boot state.

A failed repo-declared switch is recorded in `/var/lib/mag-stack/layout-failed`
(layout + hash of runtime and GLM files). The next timer tick keeps the previous
layout instead of restarting NUMA1 again; a commit that changes `ops/glm/*`,
`ops/room/glm.env` or the runtime retries once. Logs: `journalctl -u mag-llm-glm`
and the auto-update journal.

## Operator overrides

```bash
MAG_ROOM_LAYOUT=mixed bash ops/update.sh   # back to Qwen, pinned on this host
MAG_ROOM_LAYOUT=glm   bash ops/update.sh   # force GLM, ignores the failure record
MAG_ROOM_LAYOUT=repo  bash ops/update.sh   # drop the pin, follow ops/room/layout again
```

IQ1_M is a 1-bit-class quantisation. If tool calls or German quality are not
good enough, put a larger GLM quant on disk, update `model.sizes`/`start.sh`
and commit; the transaction applies it the same way.
