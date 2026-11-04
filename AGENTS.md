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
The warm `mag-llm` inference instance is intentionally independent; do not stop
or reload it as part of ordinary application changes.

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
other bots' room messages as context and may speak repeatedly, up to
MAG_ROOM_BOT_ROUNDS (default 6) after each human message. PASS is invisible;
avoid empty politeness loops. New human input and Stop immediately preempt these
bot rounds. Per-backend model turns remain serial (one request per -np 1 slot).
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
