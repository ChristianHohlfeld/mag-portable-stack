# Two GLM-5.3-Flash CPU bots (layout `glm`)

Repo deployment replaces **DeepSeek** on NUMA0 with GLM-Q1 (`IQ1_M`) and retains **IQ2_XS exactly as #231** on NUMA1 (GLM-Q2). No weight files are deleted.

| | GLM-Q1 | GLM-Q2 |
|---|---|---|
| model ID | `glm-5.3-flash-iq1-m` | `glm-5.3-flash` |
| GGUF root | `/home/chris/models/GLM-5.3-Flash-GGUF-IQ1_M/` | `/home/chris/models/GLM-5.3-Flash-GGUF-IQ2_XS/` |
| NUMA | 0, CPUs 0–13/28–41 | 1, physical CPUs 14–27 only (SMT 42–55 excluded) |
| endpoint | `127.0.0.1:8191` | `127.0.0.1:8192` |
| unit | `mag-llm-glm@q1.service` | `mag-llm-glm@q2.service` |
| slot dir | `/var/lib/mag-llm/slots/glm-q1` | `/var/lib/mag-llm/slots/glm-ik` |
| KV marker | Q1's existing format marker | `engine=ik`, pinned engine commit, 15-shard model fingerprint, `kv_format=f16-f16`, `ctx=1048576` |

Q1 remains on the pinned mainline llama.cpp runtime (`ops/qwen/runtime.sha`) with the reviewed GLM architecture compatibility patch. Q2 defaults to the separately pinned `ik_llama.cpp` runtime (source commit `8d21687cc7358de10264d4180ba9b863024d5c4`); `GLM_ENGINE=mainline` retains the mainline binary as an explicit rollback. Both keep the existing reviewed official-family chat template; Q1 remains at 32k and Q2 uses the maximum 1,048,576-token context, `--load-mode dio`, `-fa on -b 4096 -ub 1024`, `--ctx-checkpoints 64 --checkpoint-min-step 1024`, `--cache-ram 16384`, no `--cache-reuse`, `temperature=1.0`, `top_p=0.95`, `reasoning_effort=low`, `clear_thinking=true`. Q1 uses `numactl --physcpubind=0-13,28-41 --membind=0`; Q2 uses only physical NUMA1 cores `--physcpubind=14-27 --membind=1` and 14 batch workers. Neither uses `--mlock`.

Q1 and Q2 never share KV files. Q2 restores a saved slot only while its marker exactly matches engine, engine commit, the composite SHA256 fingerprint of all 15 pinned GGUF shards, and context size; a mismatch erases only Q2's slot files before starting. The prior `/var/lib/mag-llm/slots/glm` data is left untouched.

Deploy stages the GLM files and validates both old and new Q2 runtimes before switching. If and only if `GLM_ENGINE` changes, Q2 receives exactly one controlled stop/start and a :8192 chat/cache/tool smoke; failure restores its previous env, launcher, unit drop-in and engine, then restarts the prior Q2. Repeated deploys with the same engine do not restart Q2. The Mistral NUMA0 cutover remains separately transactional; legacy port owners and gateway mappings are handled as before.

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

As of 2026-10-30, mag has **2 x E5-2680 v4**, each 14 physical cores / 28 SMT logical CPUs, **35 MiB L3** per socket, 4 DDR4-2400 channels per socket, AVX2/FMA/F16C and **no AVX-512 or AMX**. Q1 remains on NUMA0. Q2 is deliberately constrained to the 14 physical cores 14–27 only; SMT siblings 42–55 are excluded. This is the requested placement, not a claim that 14 batch workers outperform the previous 28-thread configuration.

| Setting | Q1 / IQ1_M :8191 | Q2 / IQ2_XS :8192 | Reason |
| --- | --- | --- | --- |
| `--threads` / `--threads-batch` | `14 / 28` | `14 / 14` | Q2 batch work stays within the 14 allowed physical cores |
| CPU / memory binding | `--physcpubind=0-13,28-41 --membind=0` | `--physcpubind=14-27 --membind=1` | Q2 excludes SMT siblings and remains local to NUMA1 |
| `--numa` | `numactl` | `numactl` | Respects numactl CPU map; no cross-node distribution |
| `-b / -ub` | `4096 / 1024` | `4096 / 1024` | Proven pinned-runtime configuration; batch sizes are **not** speed guarantees |
| `-ctk / -ctv` | implicit **f16 / f16** | implicit **f16 / f16** | Documented defaults; changing KV dtype invalidates the saved KV format |
| `--ctx-size / --parallel` | `32768 / 1` | `1048576 / 1` | One bot/slot per model; Q2 uses the model's supported maximum after the f16 KV budget calculation below |
| `--cache-ram / ctx-checkpoints / checkpoint-min-step` | `16384 / 64 / 1024` | same | Preserves existing CPU prefix checkpoint behavior |
| `--load-mode` | `dio` | `dio` | Pinned binary's previously working mode; no `--mlock` |
| THP / huge pages | no kernel changes | no kernel changes | `madvise`, no reserved HugeTLB pages; mapping changes need live validation |
| Draft/speculative | disabled | disabled | No validated draft model/token acceptance; extra CPU work and RAM could hurt throughput |

### Q2 context budget (NUMA1)

GLM-5.3-Flash has 45 decoder layers: 34 recurrent KDA layers and 11 MLA/DSA layers. KDA state is fixed per sequence; the per-token cache is in the 11 MLA/DSA layers. Each stores a 512-element MLA latent plus a 128-element indexer key pooled by 4. With the configured f16 KV type (2 bytes/element):

`11 × (512 + 128/4) × 2 = 11,968 bytes/token`.

At 1,048,576 tokens: `11,968 × 1,048,576 = 12,549,357,568 bytes = 11.69 GiB`. The existing `--cache-ram 16384` reserves a further 16 GiB, so KV plus prefix cache is about **27.69 GiB**, before fixed KDA state and runtime/workspace/allocator overhead. With approximately 147 GB (about 136.8 GiB) free on NUMA1, this leaves about **109 GiB gross headroom**. The selected context is therefore the model's supported maximum, 1,048,576; the launcher validates that it never exceeds this ceiling.

Sources: [NVIDIA GLM-5.3-Flash architecture and 1M context](https://build.nvidia.com/z-ai/glm-5-3-flash/modelcard); [KV-cache tensor derivation](https://huggingface.co/zai-org/GLM-5.3-Flash/discussions/55); [GGUF architecture/cache notes](https://huggingface.co/aj9o9/GLM-5.3-Flash-GGUF).

## First turn: avoid repeated *prefix* prefill

**`MAG_ROOM_PREWARM=1`** now applies in both the GLM room env and the systemd room drop-in (the drop-in takes precedence). `MAG_ROOM_PREFIX_DISK=1` stays enabled. Existing native code already attempts `/slots/0?action=restore` from the model-specific slot-save directory at backend readiness and persists `/slots/0?action=save` after successful turns / prewarm. A warm resident prefix is left alone; a **changed** prefix is checked against the room's stored key. The first-ever prefill still costs computation, but can happen while idle; a valid saved prefix should drastically reduce repeated cold turns.

System/tools are still often the dominant prompt size. The GLM-only `tools_json()` already compacts descriptions to ~96 chars **without removing tool parameters, permission checks, required fields or tool names**; leave the schema intact. Keep immutable prefix bytes in front and dynamic transcript/context at the end; cap history using existing `MAG_ROOM_TRANSCRIPT_BYTES=4000`, `MAG_ROOM_TOOL_CONTEXT_BYTES=1000`, `MAG_ROOM_MAX_HISTORY=6`, but never drop tool schema, audit or authorization contracts just for speed.

Measured observation: 3028 uncached tokens / 336 s = ~9.0 tokens/s prefill; CPU decode ~2.7-2.9 tokens/s. We set **`MAG_ROOM_PREFILL_TPS=9` only for UI ETA**, not as a promised speedup. A warm turn should be compared by `tokens_cached`, `tokens_evaluated`, `timings.prompt_per_second` and time to first token. Never infer runtime speedups from these static settings alone.

Source of CLI parameter semantics: [official llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md). GLM sampling/template/effort contract: [official GLM-5.3-Flash model card](https://huggingface.co/zai-org/GLM-5.3-Flash). `reasoning_effort=low` and 32k context remain intentional latency deviations from model-card benchmark defaults; `clear_thinking=true` and the reviewed family template remain unchanged. Speculative decoding would need a compatible draft model and a verified implementation for the **pinned** llama.cpp commit; it is not justified without at least ordinary acceptance/timing data.

## 2026-10-30 14:43–14:52 live regression (PR #236)

**Historical live measurement before the physical-core-only Q2 pin:** Q1 after PR #235 evaluated 913 tokens in 114.7 seconds (**7.96 tokens/s**, previously ~9.0); Q2 on the former mainline/SMT configuration achieved 6.2–6.5 tokens/s. The current Q2 ik configuration intentionally uses only NUMA1 physical CPUs 14–27 and 14 batch workers. No new throughput improvement is claimed until measured on mag.

**Reasoning/tool loop safeguard.** The official [GLM-5.3-Flash model card](https://huggingface.co/zai-org/GLM-5.3-Flash) explicitly specifies `reasoning_effort=low` to avoid the otherwise default `max`, and `clear_thinking=true` for chat. The room already passes both consistently in the pinned official-family Jinja template; no arbitrary effort name is added (unknown values fall back to max). The [llama.cpp server docs](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) describe `max_tokens`, `chat_template_kwargs` and `tool_choice=none`. Because local IQ1_M nevertheless emitted 1024 reasoning tokens for `Hae`, **exact trivial interjections only** are restricted to 128 generated tokens, thinking-off, no tools and one round. `max_tokens` is an output limit, not a guaranteed hard cap on internal reasoning; if the model still consumes its allowance without visible text, MAG displays a short local fallback instead of internal error text. This is an intentional CPU/UI deviation, not GLM model-card default. Real tasks/questions keep existing tool access and 1024-token/model-step limit.

**Journal tool errors:** a bounded `tool-failed bot=… tool=… cause=…` entry now appears whenever dispatch marks a tool unsuccessful. No arguments are logged. The short cause strips line breaks and masks obvious token/password/secret labels. Full tool output already belongs in the authenticated run/room audit record and is **not** dumped into the journal.

**Resident unit contract:** Q1/Mistral are not restarted by the Q2 engine switch. Q2 restarts only on an actual `GLM_ENGINE` transition; the transaction restores its previous selector and runtime on failed smoke. The Q2 slot directory and KV marker are engine-specific.
