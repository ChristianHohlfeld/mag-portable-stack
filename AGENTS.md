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
