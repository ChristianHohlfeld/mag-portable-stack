# Qwen3.8 Flash Next Uncensored — standalone fallback and room NUMA0

> **Stand 31.10.2026:** Die alten Q4/IQ3-Cutover-Schritte unten sind historisch
> und nicht der Weg für den GLM-Raum. Qwen bleibt zusätzlich als separater
> Operator-Rollback für die vier Ornith-Bots verfügbar:
> `sudo bash ops/ornith/rollback.sh qwen`. Dieser alternative NUMA1-Rollback
> ist unabhängig vom neuen Raum-Bot auf NUMA0.

Status: **PR implementation**. Im `glm`-Raumlayout wird beim Deploy das
bereits vorhandene Qwen-IQ4_XS-GGUF auf NUMA0/:8191 gestartet; dafür wird
GLM-Q1 gestoppt und disabled. GLM-Q2 auf NUMA1/:8192 bleibt aktiv, enabled
und PID-identisch. Kein Download, keine Löschung. Das separate
`mag-llm-qwen.service` bleibt der NUMA1-Fallback für das Ornith-Rollback.

Architecture: `qwen4exp` (Gated DeltaNet + Qwen Sparse Attention + MoE +
51B PLE n-gram table), not DeepSeek4. The old
`/home/chris/.local/ik/8d21687cc7-mag-c96826ab/bin/llama-server` is
not reused; upstream llama.cpp has supported qwen4exp since PR #27742
(merged August 27, 2026). Separate isolated build pinned by
`ops/qwen/runtime.sha` (2026-10-20). It is a CPU-only *candidate*:
do not promise throughput/tool support until real generation tests pass.

Source GGUF: [mradermacher/Qwen3.8-Flash-Next-Uncensored-GGUF](https://huggingface.co/mradermacher/Qwen3.8-Flash-Next-Uncensored-GGUF),
`Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf`, ~98.4 GB. No model weight downloaded by deploy.
Keep optional mmproj separate; no image input/vision is promised in MAG.

## Current GLM-room integration: NUMA0 (:8191)

The repository's `glm` room layout now runs **Qwen Uncensored** as slot 1
(`qwen-numa0`, NUMA0, :8191) beside the already-running **GLM-Q2**
(`qwen-numa1`, NUMA1, :8192). This is a separate room unit,
`mag-llm-qwen-room.service`, which reuses the pinned `qwen4exp` runtime,
the existing GGUF and `/usr/local/lib/mag-qwen/start.sh`. The standalone
`mag-llm-qwen.service` remains the NUMA1 fallback for its other layout.

The cutover is repository/deploy-owned and idempotent:
- It verifies the already-present GGUF against the pinned upstream SHA; it
  never downloads or deletes model files.
- It stops and disables only `mag-llm-glm@q1.service`; the IQ1_M file and
  parked `deepseek-chat` identity, VM and memory remain; room history is untouched.
- It requires GLM-Q2 to be active, healthy and enabled before changing
  anything, snapshots its PID, and verifies the same PID and :8192 health
  after Qwen's cold start. It does not install, enable, stop, start or restart
  GLM-Q2 and does not change any `ops/glm/*` files.
- The room-only Qwen unit binds NUMA0 CPUs `0-13,28-41`, `NUMAMask=0`,
  14 decode threads / 28 batch threads, and :8191. It requires 150 GiB
  reclaimable node-local RAM after Q1 has been stopped.
- Deployment waits up to 10,800 seconds for :8191 health; the deploy watchdog
  allows four hours for the first 98 GB cold load. A failed cutover rolls back
  the Qwen/Q1 state without touching Q2.

### Room generation policy

The Qwen room bot has no tools or tool schemas, uses one model response per
message, and caps output at 256 tokens. Thinking is always disabled with
`chat_template_kwargs.enable_thinking=false` and llama.cpp `reasoning_effort=none`. Live deltas are withheld until
the final text passes the hidden-token filter; any `<think>`, `</think>`
or `<|` delimiter drops the whole candidate answer.

The deployed IQ4_XS file is the 98.4 GB quant listed on the [uncensored GGUF card](https://huggingface.co/mradermacher/Qwen3.8-Flash-Next-Uncensored-GGUF), fine-tuned from Qwen's base model. Sampling follows the official [Qwen3.8-Flash-Next model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next)
for non-thinking instruct mode: `temperature=0.7`, `top_p=0.80`,
`top_k=20`, `min_p=0`, `presence_penalty=1.5`,
`repetition_penalty=1.0`. MAG maps the last field to llama.cpp's
`repeat_penalty` request parameter. The GGUF's embedded Jinja template is
used with `--jinja`; no custom template override is installed. See the
[llama.cpp server API and chat-template docs](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

## Legacy manual staging (not used by the GLM-room deploy)

The GLM-room deploy does not download weights or run this manual staging flow;
it verifies the already-present GGUF and its pinned upstream SHA. The commands
below are only for rebuilding the separate standalone operator fallback from
scratch. If the model is already present, do not rerun `hf download`.

```bash
cd ~/mag-stack
bash ops/qwen/build-runtime.sh
python3 ops/qwen/pin-model.py
sudo bash ops/qwen/install.sh stage
```

If `python3` lacks `huggingface_hub`, install it in a venv (do not run
system Python pip as root); the pin script only uses the library if Hugging
Face file listing has no LFS digest. The independent upstream SHA-256 is
stored as one line in the model directory and checked against *all* model
bytes before the service unit is staged. Keep the pinned file out of Git;
never store HF credentials in the repository. Build uses official
`ggml-org/llama.cpp` commit `4a45de4f2c68f3d144f6c2431ad7c1da7e53c99e`, not a mutable branch at runtime.

Staging installs `mag-llm-qwen.service` and the room-only `mag-llm-qwen-room.service` (both disabled/stopped), its
verified runtime launcher, and local upstream SHA. Q4/IQ3 PIDs and backend
routing are unchanged. Check `systemctl is-enabled mag-llm-qwen.service`
before enabling; this is **not** an automatic deploy.

## Historical operator rollback flow (Ornith NUMA1; not used by GLM room)

MAG already owns the coding agent, browser/VM, MCP tools, audit log and
model switch UI. There is **no separate Qwen agent stack** and no hard-coded
DeepSeek Jinja template for Qwen. The upstream llama.cpp qwen4exp runtime
uses the GGUF Jinja template, structured OpenAI tool_calls, streaming and
model-specific thinking control. The MAG room sends enable_thinking to that
template and does not forward DeepSeek's optional reasoning_effort prefix.

After the download, runtime build, upstream SHA pin and stage steps above,
run one operator-only cutover with no active chats, jobs or backup:

```bash
sudo bash ops/qwen/cutover.sh check
sudo bash ops/qwen/cutover.sh apply
```

The script holds the MAG deployment lock and keeps Q4 on NUMA0 untouched.
Never start this transaction while the Hugging Face download is still running.
It stops only IQ3 (which shares NUMA1 and :8091), starts Qwen, waits for
a healthy model and **tests real chat, thinking on/off, SSE and structured
tool calling** directly. Only after success does it update the audit
gateway, model-selection environment and idle MAG room. A second chat/tool
test verifies routing through the audit gateway. Failed cutovers restore
the previous IQ3/backend configuration; the snapshot remains for rollback.
There is unavoidable IQ3-to-Qwen load downtime on NUMA1, not for Q4.

```bash
sudo bash ops/qwen/cutover.sh status
sudo bash ops/qwen/cutover.sh rollback  # while room/jobs idle
```

GitHub green is not proof the model is serving. Host-side functional
checks and CPU benchmark are mandatory before claiming the rollout live.
MCP dispatch and access policy remain MAG-owned; only model tool-call
compatibility is checked by the Qwen direct/gateway smoke test.

## Deliberate cutover reference — operator only, when chat/jobs/backup are idle

1. Confirm `git`/MAG room + gateway code containing this change is deployed;
   `build/mag-audit-gateway` rebuilt. Snapshot the existing audit gateway
   binary and its systemd unit; check `curl -fsS 127.0.0.1:8090/health`.
2. Stop **only** `mag-llm-iq3.service` after idle check; ensure its PID is
   gone. Never touch `mag-llm.service`.
3. `sudo systemctl start mag-llm-qwen.service`; wait for
   `curl --noproxy '*' -fsS http://127.0.0.1:8091/health`.
   Verify `numastat -p $(systemctl show -p MainPID --value mag-llm-qwen)`
   plus `/proc/<pid>/numa_maps`: weights stay node-local. Test actual
   `/v1/chat/completions` streaming, `--jinja` tool calls, JSON/tool parsing,
   reasoning and cancellation at 8K/16K contexts, with timeouts and speed
   benchmarks. A healthy /health **alone is not enough**.
4. When passed, install the reviewed, freshly built
   `build/mag-audit-gateway` to `/opt/mag-audit/bin/mag-audit-gateway`
   atomically, and the reviewed `ops/audit/systemd/mag-audit-gateway.service`
   to `/etc/systemd/system/`. Write exactly
   `MAG_SECONDARY_MODEL=qwen` to
   `/etc/mag-stack/secondary-model.env` (0644, root-owned).
   `sudo systemctl daemon-reload`; restart gateway then
   `mag-120.service` **only when room is idle**. Verify `/v1/models`
   returns Q4+Qwen, `/api/state` returns Q4+Qwen and a real MAG chat tool
   request lands on :8091.
5. Only after all checks: `sudo systemctl disable mag-llm-iq3.service`
   and `sudo systemctl enable mag-llm-qwen.service`. Boot guard in
   `ops/boot/ensure-boot.sh` respects the operator's Qwen choice and
   never silently re-enables IQ3.

### Rollback if any step fails

Before routing cutover: stop only `mag-llm-qwen`, then start IQ3. After
routing cutover: write `MAG_SECONDARY_MODEL=iq3` (or remove the file),
restore saved gateway binary/unit, restart gateway and room only once
idle, stop Qwen, restart and enable IQ3. Do not run IQ3 and Qwen together;
both use :8091 and the same NUMA1 physical cores.
Q4 :8090 is never stopped/restarted by this process.

Guardrails: startup rejects unknown NUMA CPU layout, RAM below 150 GiB
reclaimable, bad/partial model, missing upstream digest, active backup,
active IQ3, or failed Q4 health. Qwen service is localhost-only; cgroups
enforce CPU+memory node1, no swap. NUMA1 also hosts restic backups.
Staging/hash/build can produce significant IO; run outside backup hours.

Sources:
- https://github.com/ggml-org/llama.cpp/pull/27742
- https://github.com/QwenLM/Qwen3.8-Flash-Next
- https://huggingface.co/mradermacher/Qwen3.8-Flash-Next-Uncensored-GGUF
