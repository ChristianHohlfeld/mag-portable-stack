# Two GLM-5.3-Flash CPU bots (layout `glm`)

Repo deployment replaces **DeepSeek** on NUMA0 with GLM-Q1 (`IQ1_M`) and retains **IQ2_XS exactly as #231** on NUMA1 (GLM-Q2). No weight files are deleted.

| | GLM-Q1 | GLM-Q2 |
|---|---|---|
| model ID | `glm-5.3-flash-iq1-m` | `glm-5.3-flash` |
| GGUF root | `/home/chris/models/GLM-5.3-Flash-GGUF-IQ1_M/` | `/home/chris/models/GLM-5.3-Flash-GGUF-IQ2_XS/` |
| NUMA | 0, CPUs 0–13/28–41 | 1, CPUs 14–27/42–55 |
| endpoint | `127.0.0.1:8191` | `127.0.0.1:8192` |
| unit | `mag-llm-glm@q1.service` | `mag-llm-glm@q2.service` |
| slot dir | `/var/lib/mag-llm/slots/glm-q1` | `/var/lib/mag-llm/slots/glm` |
| KV tag | `glm-5.3-flash-iq1-m:kv-f16-f16:ctx32768:slot0` | `glm-5.3-flash:kv-f16-f16:ctx32768:slot0` |

Both use one pinned mainline llama.cpp runtime (`ops/qwen/runtime.sha`) with the reviewed GLM architecture compatibility patch, the existing reviewed official-family chat template, 32k ctx, `--load-mode dio`, `-fa on -b 4096 -ub 1024`, `--ctx-checkpoints 64 --checkpoint-min-step 1024`, `--cache-ram 16384`, no `--cache-reuse`, `temperature=1.0`, `top_p=0.95`, `reasoning_effort=low`, `clear_thinking=true`. Q1 additionally uses `--mlock` and `numactl --cpunodebind=0 --membind=0`. Q2 retains its original NUMA1 pinning and startup memory guard.

The two model IDs cannot share KV files: Q1 has its own slot directory, its own `.kv-format` marker and the room prefixes include the model ID and fixed room slot. `MAG_ROOM_PREWARM=0` in the existing GLM room env and room systemd drop-in.

Deploy switches both units transactionally, disables legacy port owners, renames obsolete `:8191` drop-ins to `.glm-inactive` (restored on rollback), removes the legacy gateway `50-two-bots.conf` Ornith mapping and requires each endpoint to answer `/health` and one short synthetic request. Previous units are snapshotted with enabled/active state. The model GGUF directory is verified by header; complete IQ2 SHA256 is opt-in.

## Explicit deviations and rationale (upstream sources)

- **IQ1_M/IQ2_XS GGUFs versus original model weights:** two locally quantized deployments were requested to fit CPU/NUMA memory; quantization can substantially reduce output quality, particularly IQ1_M. This PR does not claim original BF16 accuracy. [GLM model card](https://huggingface.co/zai-org/GLM-5.3-Flash).
- **`reasoning_effort=low` versus model default `max`, `max_tokens=1024` and 32k context versus evaluation settings:** CPU latency budgets; not intended to reproduce benchmark scores. Upstream explicitly documents low/high/max and default max. [GLM model card](https://huggingface.co/zai-org/GLM-5.3-Flash).
- **`clear_thinking=true`:** explicitly recommended by the model card for multi-turn chat; *not* a deviation. [GLM model card](https://huggingface.co/zai-org/GLM-5.3-Flash).
- **Pinned local template:** the GGUF embedded chat template ignored the thinking toggle; the existing `ops/glm/chat-template.jinja` is the official-family template with the two documented fixes for disabled thinking and a stable low-effort header. No additional template change in this PR. [Model card](https://huggingface.co/zai-org/GLM-5.3-Flash), [existing template and parser tests](../../ops/glm/tests/test-chat-template.cpp).
- **`temperature=1.0`, `top_p=0.95`:** model-card evaluation settings for HLE/tools; do not use greedy GLM sampling. [GLM model card](https://huggingface.co/zai-org/GLM-5.3-Flash).
- **`--load-mode dio`, Q1 `--mlock`, short checkpoints and small batches:** NUMA locality, persistent CPU prefix checkpoints and single-slot RAM constraints, not model architectural requirements; Q1's `--mlock` is an explicit operator request. Note the two modes have different memory behavior and may cause an ENOMEM on NUMA0; fail closed. [llama.cpp server flags](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
- **Pinned runtime + existing architecture compatibility patch:** the repo's `ops/glm/arch-compat.patch` targets the pinned `glm5-next` loader; upstream runtime behavior may differ from future llama.cpp releases. [llama.cpp source](https://github.com/ggml-org/llama.cpp), [existing patch](../../ops/glm/arch-compat.patch).

### Operator notes

- Start the new release only with the IQ1_M GGUF shards present under the exact directory. Missing/bad GGUF fails before retiring Q2; health and smoke gates fail closed.
- `MAG_ROOM_LAYOUT=glm` follows the repo when no host pin overrides it. Existing `MAG_ROOM_LAYOUT=mixed` overrides remain operator-controlled.
- Only **short** startup smoke for both ports; no long tool-loop test in the GLM cutover.
- PR #232 (duplicate response UI) stays separate.
