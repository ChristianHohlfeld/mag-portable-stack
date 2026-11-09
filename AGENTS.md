# MAG native stack

This is the canonical source for the MAG room, native browser controller and
native VM lifecycle. The user explicitly chose C++ and removed Docker and the old
Python/ComfyUI/Open-WebUI/Ollama application stack. Do not reintroduce them.
Python may be used for tests and one-off development/audit scripts.

Build with CMake/Ninja. Run `python3 mag-room/tests/test_native.py` after changes
to HTTP, scheduling, persistence or room routing. Keep frontend JavaScript plain
and serve dependencies locally. Test VM lifecycle against a disposable VM;
never use a user's existing bot as a destructive test target.

Production is `mag-120.service` -> `/opt/mag-stack/current/bin/magroom`.
Deploy versioned releases through `ops/deploy.sh`; use `ops/rollback.sh` to revert
executables. Runtime state, VM overlays, models and credentials live outside Git.
Preserve existing room data and immutable backing disks through deployments.
A deploy never drains, never waits for a model and never fails because a bot
is downloading, loading, paused or stopped.

## Models

Two CPU-only llama.cpp servers, declared in `ops/models/models.json`:
Lena = Gemma 4 26B-A4B on NUMA0 (physical cores 0-13, :8191), Noah = gpt-oss
120B on NUMA1 (cores 14-27, :8192). No GPU offload: the MI50 belongs to MAG
Idol alone and its power cap stays at 72 W (`ops/idol/power-cap.sh` only ever
lowers it). `mag-modeld` is the single owner of the model lifecycle: it checks
each GGUF against the Hugging Face size, verifies its SHA256 once, starts
`mag-model@<bot>.service` and publishes `/run/mag-models/status.json`.
Do not start, stop or enable model units by hand or from a deploy; use
`mag-modeld ctl <bot> start|stop|restart|freeze|thaw`. Never delete a model
file, including the retired Qwen, DeepSeek, Ornith and Flint weights.
Builds and jobs run on CPUs 28-55 only (`taskset -c 28-55`).

S23, SSH, Tailscale, Gitea, Codex and Cursor are independent working services.
Use the actual current inventory, not old session statements, when changing
operations. The audit snapshots in server-audit-20261014 contain historical
credentials; never include them or their raw transcripts in a commit or tool
output. Read only the configuration needed for a specific operation.

## Default bot execution contract

Enforce autonomy centrally in the C++ scheduler and shared system prompt.
Chris revoked the ban on bot-triggered room turns on 2026-11-04.
In the MAG group room, bot messages may trigger another bot's response, including
explicit @mentions, follow-up questions, corrections and disagreement. Bots see
other bots' room messages as context and talk to each other naturally like
humans, server-side and without any client connected. A human message without
an addressee is answered by ALL active bots; then the bots keep talking on their
own (MAG_ROOM_BOT_ROUNDS, default 0 = unlimited). PASS is each bot's own choice
and invisible; when every bot PASSes in a row the chat rests until the next
message. New human input and Stop immediately preempt these bot rounds. Per-backend model turns remain serial (one request per -np 1 slot).
This permission is for room conversation, not unrestricted autonomous tool work.
Outside room dialogue, continue tools only on a successful tool result, distinct
progress and [[MAG:WEITER]]; otherwise remain idle.
Never fake tool results or treat a site access restriction as a cookie issue
without supporting evidence. After identical errors, diagnose rather than
retry blindly. Keep the existing per-agent KVM and browser ownership; do not
weaken guest sandboxing or introduce headless-stealth bypasses.

Keep regression coverage for idle autonomy, cancellation, bounded bot dialogue
and single-owner tool continuation. The CI fixture uses
a fake model and does not prove the deployed browser works in existing VMs.
