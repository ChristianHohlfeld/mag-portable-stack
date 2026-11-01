# MAG: faster CPU coding candidates (stage + measure, **no production cutover**)

Goal: replace the CPU-heavy Mistral Large 4 / GLM-Q2 room pair with
* **NUMA0** Qwen3.6-35B-A3B, Unsloth UD-Q5_K_M (26.5 GB), strong project/coding generalist.
* **NUMA1** Qwen3-Coder-30B-A3B-Instruct, LM Studio Community Q4_K_M (18.6 GB), coding-only non-thinking.

Both are sparse ~3B-active MoEs with much smaller resident weight footprints.
The choice favors throughput, sufficient code quality and a coding budget
of **16,384 output tokens per turn** (not just 256/1,024/8,192).
This is an *intended room budget*, not yet enabled: production
`mag-120.service` currently has `MAG_ROOM_MAX_TOKENS=8192`.
GGUFs fit the host RAM, but **no MAG Broadwell CPU tok/s measurement exists
yet**. Do not confuse GPU/Mac performance figures with this host.

## Stage models, preserving every currently running service

As user `chris`, from the checkout:

```bash
python3 -I ops/coding/stage.py --model both --download
python3 -I ops/coding/stage.py --model both
python3 -m unittest discover -s ops/coding/tests -p 'test_*.py'
```

Downloads about 45 GB from the two documented Hugging Face sources.
The exact GGUF SHA256s from the upstream file pages are pinned in
`models.json`. File hash mismatches abort and leave originals untouched.
A download may still compete for disk I/O/page cache. No unit is changed,
no previous model weight is deleted, and no deployment is run.

## Localhost pilot, after deliberately making room on the relevant NUMA node

`run-candidate.sh` does **not** stop a production model. It refuses if
the existing model service is active on the target node.
Provide a **separately verified, pinned** `llama-server` executable supporting
both `qwen35moe` and `qwen3moe`, with flags checked against its `--help`.
Do not reuse an old binary without checking the architecture and sampling
template. The candidate is bound to the node's 14 *physical* cores and
dedicated localhost ports (8291, 8292); it cannot accidentally claim
8191/8192. Keep candidate slots independent.

```bash
MAG_CODING_LLAMA_BIN=/path/to/verified/llama-server bash ops/coding/run-candidate.sh general
# In a second session, once /health is ready:
python3 -I ops/coding/smoke.py general --max-tokens 16384

MAG_CODING_LLAMA_BIN=/path/to/verified/llama-server bash ops/coding/run-candidate.sh coder
python3 -I ops/coding/smoke.py coder --max-tokens 16384
```

Record TTFT, wall time, actual throughput from server timing logs,
`finish_reason` and whether emitted code passes tests. Smoke aborts for
empty, short or length-truncated text. Neither low prompt size nor a 16k
requested maximum guarantees code quality or speed.

## Live cutover is **NOT part of this PR**

Before selecting these models in MAG, a separate transactional integration
must: build and pin a compatible CPU runtime; verify the staged GGUFs;
set up correct NUMA-bound systemd units on 8191/8192; wire the audit gateway
and the room model IDs together; change both host/room max-output budgets
to 16384 without a model-specific 256/1024 cap; preserve slot/cache/
Jinja/tool-call semantics; verify long streamed code and Stop; preserve
the two bot IDs/VMs/history; verify performance and code correctness;
**only then** promote and auto-update with a one-shot rollback of all
units/mappings/config on failure. Leave `ops/room/layout=glm` unchanged
until then. A green syntax or fake-backend CI result does not prove a
successful host cutover.

Sources:
- https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/blob/main/Qwen3.6-35B-A3B-UD-Q5_K_M.gguf
- https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct
- https://huggingface.co/lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF/blob/main/Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf
- https://github.com/ggml-org/llama.cpp/issues/19480 (why *not* Coder-Next on CPU without a measured fix)
