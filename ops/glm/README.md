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

Both use one pinned mainline llama.cpp runtime (`ops/qwen/runtime.sha`) with the reviewed GLM architecture compatibility patch, the existing reviewed official-family chat template, 32k ctx, `--load-mode dio`, `-fa on -b 4096 -ub 1024`, `--ctx-checkpoints 64 --checkpoint-min-step 1024`, `--cache-ram 16384`, no `--cache-reuse`, `temperature=1.0`, `top_p=0.95`, `reasoning_effort=low`, `clear_thinking=true`. Q1 uses `numactl --physcpubind=0-13 --membind=0`, Q2 uses `--physcpubind=14-27 --membind=1`; neither uses `--mlock` (unsupported by this pinned runtime). Q2 retains its original NUMA1 pinning and startup memory guard.

The two model IDs cannot share KV files: Q1 has its own slot directory, its own `.kv-format` marker and the room prefixes include the model ID and fixed room slot. `MAG_ROOM_PREWARM=0` in the existing GLM room env and room systemd drop-in.

Deploy switches both units transactionally, disables legacy port owners, renames obsolete `:8191` drop-ins to `.glm-inactive` (restored on rollback), removes the legacy gateway `50-two-bots.conf` Ornith mapping and requires each endpoint to answer `/health` and one short synthetic request. Previous units are snapshotted with enabled/active state. The model GGUF directory is verified by header; complete IQ2 SHA256 is opt-in.

## Explicit deviations and rationale (upstream sources)

- **IQ1_M/IQ2_XS GGUFs versus original model weights:** two locally quantized deployments were requested to fit CPU/NUMA memory; quantization can substantially reduce output quality, particularly IQ1_M. This PR does not claim original BF16 accuracy. [GLM model card](https://huggingface.co/zai-org/GLM-5.3-Flash).
- **`reasoning_effort=low` versus model default `max`, `max_tokens=1024` and 32k context versus evaluation settings:** CPU latency budgets; not intended to reproduce benchmark scores. Upstream explicitly documents low/high/max and default max. [GLM model card](https://huggingface.co/zai-org/GLM-5.3-Flash).
- **`clear_thinking=true`:** explicitly recommended by the model card for multi-turn chat; *not* a deviation. [GLM model card](https://huggingface.co/zai-org/GLM-5.3-Flash).
- **Pinned local template:** the GGUF embedded chat template ignored the thinking toggle; the existing `ops/glm/chat-template.jinja` is the official-family template with the two documented fixes for disabled thinking and a stable low-effort header. No additional template change in this PR. [Model card](https://huggingface.co/zai-org/GLM-5.3-Flash), [existing template and parser tests](../../ops/glm/tests/test-chat-template.cpp).
- **`temperature=1.0`, `top_p=0.95`:** model-card evaluation settings for HLE/tools; do not use greedy GLM sampling. [GLM model card](https://huggingface.co/zai-org/GLM-5.3-Flash).
- **`--load-mode dio`, no deprecated/unsupported `--mlock`, short checkpoints and small batches:** NUMA locality, persistent CPU prefix checkpoints and single-slot RAM constraints, not model architectural requirements; This binary rejected the old Q1 `--mlock` option; the tested #231 path uses `--load-mode dio`. Any change of loading mode would need separate acceptance testing. [llama.cpp server flags](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
- **Pinned runtime + existing architecture compatibility patch:** the repo's `ops/glm/arch-compat.patch` targets the pinned `glm5-next` loader; upstream runtime behavior may differ from future llama.cpp releases. [llama.cpp source](https://github.com/ggml-org/llama.cpp), [existing patch](../../ops/glm/arch-compat.patch).

### Operator notes

- Start the new release only with the IQ1_M GGUF shards present under the exact directory. Missing/bad GGUF fails before retiring Q2; health and smoke gates fail closed.
- `MAG_ROOM_LAYOUT=glm` follows the repo when no host pin overrides it. Existing `MAG_ROOM_LAYOUT=mixed` overrides remain operator-controlled.
- Only **short** startup smoke for both ports; no long tool-loop test in the GLM cutover.
- PR #232 (duplicate response UI) stays separate.

## Broadwell dual-NUMA low-latency profile (hardware-derived, no throughput benchmark)

As of 2026-10-30, mag has **2 x E5-2680 v4**, each 14 physical cores / 28 SMT logical CPUs, **35 MiB L3** per socket, 4 DDR4-2400 channels per socket, AVX2/FMA/F16C and **no AVX-512 or AMX**. Each GLM process stays on its own node. The current **candidate** (not a measured optimum) reduces the *batch* workers from 28 to 14 and binds all workers to the 14 **physical** cores: Q1 `0-13`, Q2 `14-27`. The historical 28-thread prefill may win on some kernels, so only change it again using ordinary `timings.prompt_per_second` and `predicted_per_second` from representative requests; no long benchmark is needed.

| Setting | Q1 / IQ1_M :8191 | Q2 / IQ2_XS :8192 | Reason |
| --- | --- | --- | --- |
| `--threads` / `--threads-batch` | `14 / 14` | `14 / 14` | 14 physical cores, no SMT cache/AVX2 execution contention (heuristic only) |
| CPU / memory binding | `--physcpubind=0-13 --membind=0` | `--physcpubind=14-27 --membind=1` | Keeps weights/KV and workers local across NUMA distance 21 |
| `--numa` | `numactl` | `numactl` | Respects numactl CPU map; no cross-node distribution |
| `-b / -ub` | `4096 / 1024` | `4096 / 1024` | Proven pinned-runtime configuration; batch sizes are **not** speed guarantees |
| `-ctk / -ctv` | implicit **f16 / f16** | implicit **f16 / f16** | Documented defaults; changing KV dtype invalidates the saved KV format |
| `--ctx-size / --parallel` | `32768 / 1` | `32768 / 1` | One bot/slot per model; 32k is an intentional latency/RAM tradeoff versus the official GLM context |
| `--cache-ram / ctx-checkpoints / checkpoint-min-step` | `16384 / 64 / 1024` | same | Preserves existing CPU prefix checkpoint behavior |
| `--load-mode` | `dio` | `dio` | Pinned binary's previously working mode; no `--mlock` |
| THP / huge pages | no kernel changes | no kernel changes | `madvise`, no reserved HugeTLB pages; mapping changes need live validation |
| Draft/speculative | disabled | disabled | No validated draft model/token acceptance; extra CPU work and RAM could hurt throughput |

### First turn: avoid repeated *prefix* prefill

**`MAG_ROOM_PREWARM=1`** now applies in both the GLM room env and the systemd room drop-in (the drop-in takes precedence). `MAG_ROOM_PREFIX_DISK=1` stays enabled. Existing native code already attempts `/slots/0?action=restore` from the model-specific slot-save directory at backend readiness and persists `/slots/0?action=save` after successful turns / prewarm. A warm resident prefix is left alone; a **changed** prefix is checked against the room's stored key. The first-ever prefill still costs computation, but can happen while idle; a valid saved prefix should drastically reduce repeated cold turns.

System/tools are still often the dominant prompt size. The GLM-only `tools_json()` already compacts descriptions to ~96 chars **without removing tool parameters, permission checks, required fields or tool names**; leave the schema intact. Keep immutable prefix bytes in front and dynamic transcript/context at the end; cap history using existing `MAG_ROOM_TRANSCRIPT_BYTES=4000`, `MAG_ROOM_TOOL_CONTEXT_BYTES=1000`, `MAG_ROOM_MAX_HISTORY=6`, but never drop tool schema, audit or authorization contracts just for speed.

Measured observation: 3028 uncached tokens / 336 s = ~9.0 tokens/s prefill; CPU decode ~2.7-2.9 tokens/s. We set **`MAG_ROOM_PREFILL_TPS=9` only for UI ETA**, not as a promised speedup. A warm turn should be compared by `tokens_cached`, `tokens_evaluated`, `timings.prompt_per_second` and time to first token. Never infer runtime speedups from these static settings alone.

Source of CLI parameter semantics: [official llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md). GLM sampling/template/effort contract: [official GLM-5.3-Flash model card](https://huggingface.co/zai-org/GLM-5.3-Flash). `reasoning_effort=low` and 32k context remain intentional latency deviations from model-card benchmark defaults; `clear_thinking=true` and the reviewed family template remain unchanged. Speculative decoding would need a compatible draft model and a verified implementation for the **pinned** llama.cpp commit; it is not justified without at least ordinary acceptance/timing data.

## 2026-10-30 14:43–14:52 live regression (PR #236)

**MAG timing is authoritative over our previous 14-thread guess.** Q1 after PR #235 evaluated 913 tokens in 114.7 seconds (**7.96 tokens/s**, previously ~9.0); Q2 achieved only 6.2–6.5 tokens/s. Both GLM servers now use `--threads 14 --threads-batch 28` and retain **both logical thread groups on their own NUMA node** (Q1 `0-13,28-41`, Q2 `14-27,42-55`). SMT is enabled for batch prefill without crossing sockets. These results do not imply a measured decode improvement: it remains ~2.5–2.6 tokens/s.

**Reasoning/tool loop safeguard.** The official [GLM-5.3-Flash model card](https://huggingface.co/zai-org/GLM-5.3-Flash) explicitly specifies `reasoning_effort=low` to avoid the otherwise default `max`, and `clear_thinking=true` for chat. The room already passes both consistently in the pinned official-family Jinja template; no arbitrary effort name is added (unknown values fall back to max). The [llama.cpp server docs](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) describe `max_tokens`, `chat_template_kwargs` and `tool_choice=none`. Because local IQ1_M nevertheless emitted 1024 reasoning tokens for `Hae`, **exact trivial interjections only** are restricted to 128 generated tokens, thinking-off, no tools and one round. `max_tokens` is an output limit, not a guaranteed hard cap on internal reasoning; if the model still consumes its allowance without visible text, MAG displays a short local fallback instead of internal error text. This is an intentional CPU/UI deviation, not GLM model-card default. Real tasks/questions keep existing tool access and 1024-token/model-step limit.

**Journal tool errors:** a bounded `tool-failed bot=… tool=… cause=…` entry now appears whenever dispatch marks a tool unsuccessful. No arguments are logged. The short cause strips line breaks and masks obvious token/password/secret labels. Full tool output already belongs in the authenticated run/room audit record and is **not** dumped into the journal.

**Resident unit contract:** staged `q1.env` or `q2.env` changes restart **only** the corresponding systemd instance. Shared `llama-server` runtime, launcher, service template or GLM Jinja change affects both. Rollback retains the other model in memory and performs no unnecessary smoke call on it. Both model files, separate slot-save paths, nginx, and `MAG_ROOM_PREWARM=1` remain unchanged.
