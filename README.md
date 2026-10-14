# MAG — native agent server

One repository, one deployed application stack. No Docker, Ollama, Open WebUI,
ComfyUI, Python room server or PM2 orchestration.

MAG keeps one existing **GPT-OSS-120B / llama.cpp** instance warm on the host.
Every room bot owns a **KVM virtual machine**, a persistent **Chromium browser**,
a dedicated **Gitea administrator account**, files and durable memories.
The room, scheduler, browser controller and VM lifecycle are **C++17**.
The frontend is plain HTML/CSS/JavaScript with local Markdown rendering.

Canonical source on mag: `/home/chris/mag-stack`.

Production room: **https://mag.tail3e02ae.ts.net:8446/** (Tailscale).
Gitea and the S23 have their own independent endpoints and services.

## Source and runtime

| Source | Runtime responsibility |
| --- | --- |
| `mag-room/native/magroom.cpp`, `routes.hpp` | HTTP/SSE, shared files, tools, model streaming, server-owned scheduler |
| `mag-room/native/browser.cpp` | Persistent Chromium/CDP in each guest; browser API requires authentication |
| `mag-room/native/vm.cpp` | Guarded spawn/despawn, guest execution, quotas, dedicated Git accounts |
| `mag-room/native/common.hpp`, `json.hpp` | Process, HTTP and JSON primitives |
| `mag-room/web/index.html` | Chat, code rendering, files/canvas, browser controls, microphone, local preferences |
| `ops/` | Versioned deployment, systemd units, health and rollback |
| `mag-room/tests/` | Isolated regression tests; Python is a test driver, not a production server |

Application releases live at `/opt/mag-stack/releases/<release>`; `current`
selects the active release. Room data lives at `/var/lib/magbot/room`.
VM overlays/registry live at `/var/lib/libvirt/images/magbot/bots`, and the
read-only shared backing disk stays at `base/golden.qcow2`.
Model weights and credentials are runtime data and never belong in Git.

## Build and verify

Ubuntu 24.04 x86_64: C++ compiler, CMake, Ninja, OpenSSL development headers.

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
python3 mag-room/tests/test_native.py
```

Local voice: pinned whisper.cpp (see `ops/whisper.lock`), FFmpeg and a multilingual
Whisper model. TTS uses local espeak-ng; OCR uses local Tesseract (`deu+eng`).
`ops/bootstrap-voice.sh` builds the pinned transcriber without containers.

## Deploy

The host must already provide libvirt/KVM, an immutable prepared golden image,
Gitea, the warm llama.cpp endpoint and Tailscale. The deployment preserves their
existing data and does not reload the inference model.

```bash
sudo bash ops/deploy.sh
sudo bash ops/health.sh
```

The installer snapshots the previous unit, native lifecycle and room state under
`/var/backups/mag-stack/<release>`, installs a versioned release and checks
readiness before retiring the old service entries. Keep `/etc/default/mag-room`
(root-only) for the existing Brave key. The primary UI stays loopback-only; the
VM bridge binds only the VM gateway. Gitea VM access is a socket proxy bound to
`192.168.122.1:3001`, with each bot's own account credentials.

Rollback selects the previous executable/unit, preserving subsequent room data:

```bash
sudo bash ops/rollback.sh RELEASE_ID
```

A room-state restore is deliberately separate: stop the room, restore the saved
state directory from that release's backup, then start it. Do not overwrite
current data casually during an executable rollback.

## Operating behavior

- Autonomy runs on the server without browser viewers, and its enabled/paused
  state survives a service restart. **Autonomy ON does not mean continuous chat.**
  A new user turn arms one accountable task owner (the first selected bot).
  Other selected bots may answer the explicit human request but do not create
  autonomous tasks by talking to each other.
- Continuations require a successful tool action, a distinct tool-result
  fingerprint and the model's `[[MAG:WEITER]]` marker. `[[MAG:ENDE]]` ends
  the task. Markers are stripped from persisted responses. A failed/no-tool
  step, repeated result, repeated reply, cancelled task or eight automatic
  continuations ends execution, while the Autonomy toggle can remain ON.
- Human turns preempt active work; the single inference slot is never duplicated.
  Restarts preserve the ON switch, but do not silently resurrect stale tasks.
  A new user instruction is needed to resume autonomous work.
- Stop disables autonomy and cancels the active model stream/queued turns.
- Creating a bot uses an existing warm golden disk with a private qcow2 overlay,
  cloud-init identity and DHCP identification by MAC. Admission limits are checked
  before provisioning. No browser installation occurs per spawn.
- Each guest runs commands as `bot`. Its own Git administrator credentials are
  provisioned only inside that guest; deletion revokes that dedicated account.
- Agent deletion checks its registry ownership, destroys only its own VM and
  private files, and never removes the shared backing disk or another VM.
- Room reset purges shared files/transcript/published UI while preserving bot
  memories. It does not destroy bots.
- Shared HTML previews run in sandboxed iframes. Bots can download/publish the
  room frontend and verify it in their own browser.
- Microphone transcription is local; the existing model may correct context when
  its inference slot is free. The result is editable and never sent automatically.

## Practical limits

GPT-OSS here is a text model. Image text can be read with OCR and browser content
with CDP; this does not provide a general multimodal vision model. Local espeak-ng
is functional speech, not a natural neural voice. Browser display uses bounded
snapshot polling; it is not a video stream. Real speech quality requires personal
DE/EN samples. The model's CPU inference remains the dominant response latency.

See `docs/CONSOLIDATION_PLAN.md` and `docs/VALIDATION.md` for the migration and
measured results. Historical stacks are removed from the active tree and remain
only in pre-migration backups and Git history.

## Task-oriented autonomy

The server in `mag-room/native/magroom.cpp` owns the workflow; no per-VM
prompt copies, second scheduler, Python application server or browser-side
agent orchestration. Bots continue work only for a real user request, not for
another bot's chatter. Every turn records tool results, and identical failing
commands are cut off within a turn. A browser boot failure now reports guest
filesystem diagnostics and keeps the previous browser binary untouched if
download fails. A successful browser installation or site access must be
verified on the actual MAG host; CI cannot reach user VMs.

Use `POST /api/autotalk {"on":true}` (or the UI toggle) to enable autonomous
continuations for **future user turns**. When idle it consumes no inference
cycles. `POST /api/stop` cancels the active stream, clears the task and
disables autonomy. Explicit `POST /api/message` always works whether or not
autonomy is enabled. The eight-step ceiling is a deliberate safety limit;
follow-up tasks are started with another explicit user instruction.
