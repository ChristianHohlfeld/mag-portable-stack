# Mistral Large 4 room bot — NUMA0 / :8191

The GLM room's slot 1 changes from Qwen to **Mistral**. Slot 2 remains the
already-running GLM-Q2 on NUMA1 / :8192. The old Qwen GGUF and Qwen runtime
remain on disk; this layout does not delete them.

## Model and policy

- Model: `mistralai/Mistral-Large-4-1T-A52B`, GGUF from
  `unsloth/Mistral-Large-4-1T-A52B-GGUF/UD-IQ1_S`.
- Six shards, 233 GB total; SHA256 values are the HF file-page/LFS SHA256
  values, committed in `SHA256SUMS`. The user-supplied first-shard digest
  is retained verbatim.
- The GGUF's embedded Jinja chat template is used via `--jinja`; no custom
  template is passed.
- Temperature 1.0, top_p 0.95, top_k 0, min_p 0.0, reasoning_effort low,
  following the [official Mistral model card](https://huggingface.co/mistralai/Mistral-Large-4-1T-A52B)
  and [Unsloth's llama.cpp guide](https://unsloth.ai/docs/models/mistral-large-4).
  Mistral says low is for fast chat; Unsloth's llama.cpp example disables
  top-k/min-p filters and passes reasoning_effort in chat-template kwargs.
  The official card lists the architecture as `mistral_large4`.
- Chat-only room integration: no KVM, browser or terminal; every turn and
  prewarm ends in a user message. The Mistral template therefore never sees
  a system-only completion. Failed prewarm uses the existing exponential
  backoff and does not block real turns.
- Context 16K (below the 1M model limit because NUMA0 has 252 GiB total).
  mmap is explicit; mlock is deliberately absent. KV cache stays
  at llama.cpp's default FP16; q8_0 is not enabled without measured evidence.

## llama.cpp architecture support

MAG's previous Qwen runtime was pinned to llama.cpp
`4a45de4f2c68f3d144f6c2431ad7c1da7e53c99e` (2026-10-20), before the Mistral
Large 4 architecture loader. This room model therefore gets a separate,
dedicated upstream build pinned to
`8642541b255bfb5dc39cd351a178be09013a55d6` (2026-10-28). That exact source
contains `LLM_ARCH_MISTRAL4` in `src/llama-arch.h`, maps it to
`mistral4` in `src/llama-arch.cpp`, and dispatches the architecture in
`src/llama-model.cpp`. The build script checks all three before compiling.
It enables AVX2 and explicitly disables AVX-512, matching MAG's E5-2680 v4
CPUs. This proves loader/build support, not a real 16K inference benchmark;
host-side chat smoke and memory/NUMA measurement remain mandatory.

## Memory and activation gate

NUMA0 has 252 GiB. The 233 GB (about 217 GiB) quant leaves limited headroom.
The service binds CPU and memory to node 0, requires at least 225 GiB
reclaimable NUMA0 memory before load, uses mmap, 14 physical CPU threads,
16K context, batch 128 / ubatch 64, no swap, and a 244 GiB cgroup maximum.
The Qwen room service must be stopped before Mistral starts. GLM-Q2 is checked
healthy on :8192 but is never stopped, started, reloaded or restarted.
A load or health failure rolls back to Qwen without touching GLM-Q2.

## Download and activation

Download the six shard files into
`/home/chris/models/Mistral-Large-4-1T-A52B-UD-IQ1_S/`. The stage script queries the Hugging Face Hub tree API with `expand=true`,
extracts all six `lfs.oid` SHA256 values, and compares them with the reviewed
`SHA256SUMS` committed here. It completes a correct partial manifest already
in the model directory (including the operator-supplied shard-1 hash), refuses
any conflicting hash, then verifies every full shard before installing the unit. The layout transaction
calls stage before it stops Qwen; missing/incomplete shards therefore leave
Qwen active. Deployment never downloads or deletes model weights.

Build runtime as chris:
```sh
bash ops/mistral/build-runtime.sh
```

After all six shards have finished downloading:
```sh
sudo bash ops/mistral/install.sh stage
```

The layout transaction then performs the guarded Qwen-to-Mistral switch.
The service's ExecStartPre checks the six shards against the pinned SHA256s
again (or the root-owned inode/size/mtime/ctime verification stamp). Rollback
restores the prior Qwen service/config. Qwen model files remain untouched.

## Sources

- [Mistral Large 4 official model card](https://huggingface.co/mistralai/Mistral-Large-4-1T-A52B)
- [Unsloth GGUF card, shard listing and 233 GB quant](https://huggingface.co/unsloth/Mistral-Large-4-1T-A52B-GGUF)
- [Unsloth llama.cpp guide: memory budget, CPU build, context and sampling](https://unsloth.ai/docs/models/mistral-large-4)
- [HF UD-IQ1_S shard listing and sizes](https://huggingface.co/unsloth/Mistral-Large-4-1T-A52B-GGUF/tree/main/UD-IQ1_S)
- [HF Hub tree API used by the stage gate](https://huggingface.co/api/models/unsloth/Mistral-Large-4-1T-A52B-GGUF/tree/main/UD-IQ1_S?recursive=true&expand=true)
- [llama.cpp architecture loader source at pinned revision](https://github.com/ggml-org/llama.cpp/tree/8642541b255bfb5dc39cd351a178be09013a55d6)
