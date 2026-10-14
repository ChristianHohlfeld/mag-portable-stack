# Validation — MAG consolidation, 2026-10-14

## Requirements and preservation

Four OpenCode sessions (890 messages, 2,991 stored parts) were inventoried via
read-only SQLite queries, backed up with the online backup API and exported
privately for audit. Product requirements were taken from the two stack sessions;
the latest explicit implementation direction is C++.

Existing room bot Ab/agent1, its VM disk, room transcript and user uploads were
preserved. GPT-OSS-120B stayed in its original running llama.cpp process; no model
unload, host reboot or duplicate inference instance was needed. S23, Gitea,
SSH, Tailscale, Codex and Cursor remain separate working services.

## Verified

- Brave web search was called through the actual native implementation and
  returned five grounded sources with the existing configured key.
- CMake/Ninja builds native `magroom`, `mag-browser` and `magbot-native`.
- Eight isolated integration checks pass: actual chunked HTTP model streaming,
  selected bot turns, file roundtrip, reset/persistent memory, bridge authorization,
  origin/traversal validation, immediate stream cancellation and human input while
  autonomy is active, restart persistence and invalid HTTP framing.
- Existing guest execution is native and runs as user `bot`, not root.
- A disposable VM was created from the existing golden disk, used, and removed.
  Its own Gitea administrator account was provisioned and authenticated via API,
  then removed with that VM. Original account chris and VM agent1 remained.
- An initial test exposed duplicated DHCP identity in golden overlays. Explicit
  cloud-init `dhcp-identifier: mac` was added; agent1 used 192.168.122.63 while the
  disposable guest used 192.168.122.113. They were reachable simultaneously.
- Native browser navigation and DOM reading returned "Example Domain" and a real
  PNG. The default room UI loaded in the actual guest Chromium, displayed Ab and
  established its SSE connection. Sandbox works through a narrowly scoped guest
  AppArmor profile; no host namespace restriction was disabled.
- A real GPT-OSS turn returned "Die native Konsolidierung ist bereit." Another
  browser-tool task completed with "Example Domain".
- Local espeak-ng produces WAV audio. Local whisper.cpp produces editable German
  transcription; multilingual large-v3-turbo is installed. A synthetic espeak
  fixture is not a voice-quality benchmark; proper DE/EN user samples remain needed.
- Room API create/delete was also tested, including a second independent native
  browser and per-bot memory. An overly narrow DHCP firewall rule was found and
  repaired to permit the initial DHCP broadcast; admission failures roll back the
  guest and its Git account instead of leaving orphans.
- VM public HTTPS access works. Requests from a guest to host SSH and the tailnet
  address were denied by the dedicated VM nftables policy. Gitea access is limited
  to its VM gateway port and still requires the bot's own credentials.
- Production deploy has a previous-unit/binary/room snapshot and readiness check.
  A redeploy encountered an already-removed legacy unit; automatic rollback kept
  the live room working. The installer was repaired to tolerate missing old units.

## Removed from the active stack

1,216 old tracked files: Docker compose/images, ComfyUI, Open WebUI/Ollama capture,
old config snapshots/installers and the Python application/lifecycle code.
Old `mag-chat`, PM2 and finalizer units were disabled and removed. `/opt/magbot`
and duplicate home source trees were archived, then removed from active paths.
Docker/containerd packages, executables and data directories were already absent
on this host; their repo infrastructure has now also been removed.

Private recovery artifacts: `/home/chris/server-audit-20261014` and
`/var/backups/mag-stack`. They contain sensitive historical configuration and stay
outside Git. Current production source is `/home/chris/mag-stack`. The existing private Git
origin `ChristianHohlfeld/mag-portable-stack` is retained for history/continuity.

## Limits

No host reboot was performed. Continuous browser viewing is screenshot polling,
not video streaming. GPT-OSS remains text-only; images use OCR/browser text.
Single-slot CPU inference is still the dominant response latency. Existing user
projects and the model collection were not treated as disposable old software.
