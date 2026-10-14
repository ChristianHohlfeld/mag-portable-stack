# MAG consolidation — 2026-10-14

## Source of requirements

All four OpenCode sessions in the live SQLite database were inventoried and
backed up using SQLite's online backup API. Private transcripts and the database
stay outside Git in `/home/chris/server-audit-20261014` (0700).

The applicable product sessions are "Systemcheck: installierte Software" and
"Fixing the stack". The later two sessions concern installing/repairing Codex.
The latest explicit stack decision is C++ for the application, not the old
Python room or the historical Docker/ComfyUI bundle.

Required behavior: one warm GPT-OSS-120B inference backend; isolated KVM bot VMs
from the existing golden image; independent persistent browsers and web search;
autonomous server-side turns even with no viewers; Stop cancels work; human input
remains available during autonomy; shared room files/canvas, persistent bot memory;
agent create/delete, room cleanup preserving bot memory; local speech/OCR;
Tailscale access, readable code/chat rendering, stored user preferences.

## Observed baseline

- Canonical Git origin: ChristianHohlfeld/mag-portable-stack.
- Live room: `/opt/magbot/app.py`, systemd mag-120, loopback 8766.
- Inference: mag-llm, llama.cpp on loopback 8080, one slot. Keep warm and unchanged.
- Existing room data: `/var/lib/magbot/room`, currently one user-created bot Ab/agent1.
- Native code: two divergent, undeployed copies; repository's untracked native
  tree has additional scheduler/cancellation/bridge fixes. Preserve both first.
- Existing VM lifecycle and browser helpers use Python; migrate application
  control paths to native binaries while preserving VM disks and identities.
- S23, SSH, Tailscale, Gitea, Codex, Cursor are working independent services.

## Implementation sequence

1. Snapshot source, room state, root configuration and VM registry; inventory
   running units and repository history. Never copy auth credentials into Git.
2. Use the existing repository as the single source, with native C++ room,
   browser/VM control, native local speech tools, explicit frontend and one build.
   Historical generation stack is removed from the active repo (user explicitly
   requested "Docker weg, altes weg"); it survives only in private backups/Git history.
3. Repair incomplete endpoint/frontend wiring, persistence, cancellation and
   lifecycle behavior. Test isolated temporary state/ports before production.
4. Deploy versioned native release under `/opt/mag-stack/releases`, atomically
   select it with `current`, retain rollback release and room backup. Keep the
   existing mag-120 unit/8766/Tailscale URL; replace only its executable.
5. Verify production HTTP/SSE, existing guest commands/browser, native lifecycle
   roundtrip using a disposable VM, local speech/OCR and real model inference.
6. Archive redundant source copies and obsolete installers/backups after their
   replacements pass. Preserve models, golden disk, user VMs, S23 and host access.
7. Commit and push the consolidated repo, document measured checks, limitations,
   release/rollback commands and actual active services.

## Validation and rollback

Native tests cover HTTP framing, chunked model streaming, event delivery, queued
human input, Stop, room reset/file purge with memories retained, traversal and
bridge authorization, persisted state and lifecycle ownership/admission checks.
Browser tests use actual Chromium in agent1; speech uses a locally generated
fixture. One real model turn verifies the preserved warm backend. No reboot or
model unload is needed. Deployment rolls back to the saved unit on failure.
