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
| KV marker | Q1's existing format marker | `engine=ik`, pinned engine commit, 15-shard model fingerprint, `ctx=32768` |

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
| context | 32768 | see measured KV budget below |
| `--load-mode` | dio | dio |

## Q2 context budget

Context is deliberately selected from the NUMA1 memory budget, not the model's advertised 1,048,576-token ceiling. The PR records the exact KV bytes/token derivation and leaves headroom for runtime buffers, graph/workspace, allocator fragmentation and the resident model. Do not infer a safe context from the GGUF context limit alone.

## Operator contract

- Q2's KV marker binds engine, engine commit, composite model-shard fingerprint and context; incompatible slot files are not restored.
- `GLM_ENGINE=mainline` is the explicit rollback selector; Q1 remains mainline-only.
- No model GGUF is deleted or rewritten by this change.
- The Q2 switch must smoke-test :8192 and transactionally restore the previous engine on failure.
- nginx and the Mistral/NUMA0 service are outside this change.
