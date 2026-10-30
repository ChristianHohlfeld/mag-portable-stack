# GLM-5.3-Flash on NUMA1 (room layout `glm`)

Owner decision (Chris, 2026-10-29): GLM-5.3-Flash replaces Qwen3.8 Uncensored
as the NUMA1 room model. DeepSeek V4 Flash stays on NUMA0 and is never touched.
Qwen is only removed from the stack: its GGUF, unit file and pinned digest stay
on disk, and `MAG_ROOM_LAYOUT=mixed` brings it back through the same transaction.

| | |
|---|---|
| Model | `GLM-5.3-Flash-IQ2_XS` (15 split GGUFs, 92.1 GB / 85.8 GiB), `/home/chris/models/GLM-5.3-Flash-GGUF-IQ2_XS/` |
| llama.cpp arch | `glm5-next` (hybrid KDA + MLA, DSA indexer, MoE) |
| Runtime | the pinned upstream llama.cpp of Qwen (`ops/qwen/runtime.sha`, `…/qwen4exp/<sha>-mag-cp1`) — it contains `src/models/glm5-next.cpp`; no extra build |
| Unit | `mag-llm-glm.service` → `/usr/local/lib/mag-glm/start.sh`, 127.0.0.1:8192, cores 14-27, memory bound to node1, `--load-mode dio`, 32k ctx, 1 slot, ctx checkpoints |
| Room id / label | `glm-5.3-flash` / `GLM-5.3 Flash` (`ops/room/glm.env`, preset `deepseek-glm-dual-numa`) |
| Integrity | `model.sizes`: exact sizes + GGUF headers; `model.sha256`: 15 HF-LFS SHA256 pins (revision `ecc6c5303921d0a67c698aee7faf2f3577386347`) |

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

## Thinking control (pinned chat template)

The GGUF's embedded template ignores `enable_thinking` and always ends the
prompt with `<|assistant|><think>` under `Reasoning Effort: Max`; with thinking
"off" every answer ended up in `reasoning_content` (diagnosis 2026-10-29,
`ops/glm/diag-chat.sh`). `start.sh` therefore runs llama-server with
`--chat-template-file ops/glm/chat-template.jinja`, the embedded template with
two reviewed changes:

- `enable_thinking=false` prefills `<think></think>` (the form the template
  itself uses for assistant turns without reasoning) → plain `content`;
- default `Reasoning Effort: Low` (the room never sends `reasoning_effort`;
  `low`/`high`/`max` remain selectable via `chat_template_kwargs`). The header
  does not depend on `enable_thinking`, so the cached system prefix is shared.

Gates: `install.sh` refuses a GGUF whose embedded template is not this family
(`--require-glm-template-family`), and `build-runtime.sh` builds and runs
`ops/glm/tests/test-chat-template.cpp` (llama.cpp's own chat/PEG parser:
thinking on/off, content, reasoning, tool calls) before publishing a runtime.

## Integrity and NUMA1 sizing

The default preflight validates all 15 sizes/headers, GLM KDA metadata, chat
template family and elementwise tensor types before NUMA1 is touched.
The metadata gate is not an IQ1_M-specific workaround; an incompatible GGUF
can still pass size/header checks. To audit complete hashes on mag (92.1 GB
of reads; intentionally skipped by normal auto-updates):

```bash
MAG_GLM_VERIFY_SHA256=1 bash /usr/local/lib/mag-glm/verify-model.sh
```

IQ2_XS needs 85.8 GiB for model weights, plus a 16 GiB prompt cache and
32k KV/recurrent state. NUMA1 startup requires 145 GiB reclaimable memory;
the GLM unit uses `MemoryHigh=140G`, `MemoryMax=175G`. `--load-mode dio`
is retained to avoid duplicating/misplacing mmap and locked pages.

## Loading and speed

`--load-mode dio` (O_DIRECT into anonymous memory on node1). `mlock` failed with
ENOMEM because page cache and locked copy both had to fit on node1; swap is off
for the unit, so locking added nothing. The unit runs without `Nice` and
without idle I/O class. Acceptance logs generation/prompt tok/s on the
`PASS chat` line. Measured 2026-10-29 (diag, IQ1_M, 14 cores): ~3 tok/s
generation; IQ1_M CPU kernels, not configuration, are the limit.

## Operator overrides

```bash
MAG_ROOM_LAYOUT=mixed bash ops/update.sh   # back to Qwen, pinned on this host
MAG_ROOM_LAYOUT=glm   bash ops/update.sh   # force GLM, ignores the failure record
MAG_ROOM_LAYOUT=repo  bash ops/update.sh   # drop the pin, follow ops/room/layout again
```

IQ1_M remains on disk but is no longer selected. Qwen remains the rollback
via `MAG_ROOM_LAYOUT=mixed`; neither quant is deleted by this change.
