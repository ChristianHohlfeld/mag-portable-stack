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
| KV marker | Q1's existing format marker | `engine=ik`, pinned engine commit, 15-shard model fingerprint, `ctx=1048576` |

Q1 remains on pinned mainline llama.cpp. Q2 defaults to the separately pinned `ik_llama.cpp` runtime (source commit `8d21687cc7358de10264d4180ba9b863024d5c4`); `GLM_ENGINE=mainline` retains the mainline binary as an explicit rollback. Both keep the reviewed official-family chat template, `--load-mode dio`, `-fa on -b 4096 -ub 1024`, `--ctx-checkpoints 64 --checkpoint-min-step 1024`, `--cache-ram 16384`, no `--cache-reuse`, `temperature=1.0`, `top_p=0.95`, `reasoning_effort=low`, `clear_thinking=true`. Q1 uses `numactl --physcpubind=0-13,28-41 --membind=0`; Q2 uses only physical NUMA1 cores `--physcpubind=14-27 --membind=1` and 14 batch workers. Neither uses `--mlock`.

Q1 and Q2 never share KV files. Q2 restores a saved slot only while its marker exactly matches engine, engine commit, the composite SHA256 fingerprint of all 15 pinned GGUF shards, and context size; a mismatch erases only Q2's slot files before starting. The prior `/var/lib/mag-llm/slots/glm` data is left untouched.

Deploy stages both engines before switching. If and only if `GLM_ENGINE` changes, Q2 receives exactly one controlled stop/start and a :8192 chat/cache/tool smoke; failure restores its previous env, launcher, unit drop-in and engine, then restarts the prior Q2. Repeated deploys with the same engine do not restart Q2. Mistral NUMA0 remains separately transactional and unchanged.

## Broadwell NUMA placement

The host has 2 × Xeon E5-2680 v4, each 14 physical cores / 28 SMT logical CPUs, AVX2/FMA/F16C, no AVX-512/AMX. Q2 is pinned only to NUMA1 physical CPUs 14–27; SMT siblings 42–55 are excluded. This is a placement requirement, not a throughput claim.

| Setting | Q1 / :8191 | Q2 / :8192 |
|---|---:|---:|
| `--threads / --threads-batch` | 14 / 28 | 14 / 14 |
| CPU / memory binding | `0-13,28-41 / node 0` | `14-27 / node 1` |
| `-b / -ub` | 4096 / 1024 | 4096 / 1024 |
| KV type | f16 / f16 | f16 / f16 |
| context | 32768 | 1,048,576 |
| `--load-mode` | dio | dio |

## Q2 context budget

For GLM-5.3-Flash, 34 of 45 layers use recurrent KDA state (fixed per sequence); 11 use MLA/DSA. The MLA latent is 512 elements and the pooled indexer key contributes 128/4 elements per full-attention layer. With the configured f16 KV type (2 bytes/element): 11 × (512 + 128/4) × 2 = **11,968 bytes/token**. At 1,048,576 tokens: 11,968 × 1,048,576 = **12,549,357,568 bytes = 11.69 GiB**. KDA recurrent state, graph/workspace and allocator overhead are additional but not linear per token. The existing `--cache-ram 16384` reserves a further 16 GiB for prefix/cache data, so KV plus that cache is about **27.69 GiB**. With approximately 147 GB (about 136.8 GiB) free on NUMA1, this leaves about **109 GiB gross headroom** for KDA state, graph/workspace and allocator overhead. Therefore the model's supported maximum of **1,048,576** is the safe ceiling and the runtime must not exceed it. Sources: [NVIDIA GLM-5.3-Flash architecture](https://build.nvidia.com/z-ai/glm-5-3-flash/modelcard) (45 layers, 34 KDA + 11 DSA, 1M context); [KV-cache tensor derivation](https://huggingface.co/zai-org/GLM-5.3-Flash/discussions/55) (512-wide MLA latent, pooled indexer key); [GGUF architecture notes](https://huggingface.co/aj9o9/GLM-5.3-Flash-GGUF) (f16 32K/131K/300K cache estimates).

## Operator contract

- Q2's KV marker binds engine, engine commit, composite model-shard fingerprint and context; incompatible slot files are not restored.
- `GLM_ENGINE=mainline` is the explicit rollback selector; Q1 remains mainline-only.
- No model GGUF is deleted or rewritten by this change.
- The Q2 switch must smoke-test :8192 and transactionally restore the previous engine on failure.
- nginx and the Mistral/NUMA0 service are outside this change.
