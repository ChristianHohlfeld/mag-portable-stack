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

## Per-bot live browser (WebRTC/UDP)

MAG reuses the transport pattern from the S23 native UDP stack, while
keeping **one persistent Chromium + profile inside each bot's own KVM**.

- In the VM, \`mag-browser\` owns the one CDP connection to the hot Chromium
  tab. It reattaches on controller upgrades without resetting tabs or cookies.
- Each browser sends per-VM-key HMAC-SHA256-authenticated, bounded JPEG packets over
  **VM's UDP :9227 → MAG UDP :18783**; no second browser or video transcoder.
- The dedicated Go/Pion \`mag-browser-webrtc\` gateway forwards assembled JPEG
  frames on an unordered WebRTC SCTP DataChannel over DTLS/ICE UDP
  (ICE UDP 18840–18890). Pointer/touch/keyboard/wheel commands travel back
  on a reliable WebRTC DataChannel and signed UDP into that same VM browser.
- Normal agent tools use the same existing CDP controller, not a separate
  browser. The MAG frontend is signaling-only over HTTPS; live video and
  interactive input do **not** use the old 600ms HTTP screenshot polling.
- A paused viewer stops subscribing and the guest stops captures after eight
  seconds; Chromium stays open. Each VM emits only a one-second UDP heartbeat.
- The S23's Android hardware provides native H.264; this Chromium route uses
  JPEG screenshots over WebRTC data, so S23's 60fps figures **do not apply**.
  Actual FPS and latency require measurement on MAG.

Port :18783 is permitted only for guest-to-host UDP on virbr0. The
WebRTC gateway's HTTP offer API binds **127.0.0.1:18784**, and MAG validates
the selected bot ID/VM name before relaying signaling. Browser UDP packets use separate persistent, random keys per VM under
`/var/lib/magbot/room/browser-udp-keys/`, never the shared HTTP bridge token.
They are validated per VM and limited to the VM subnet.
Do not expose the gateway's HTTP endpoint outside loopback.

Release prerequisites: Go 1.23 or newer available on MAG for the relay build,
KVM guest address 192.168.122.1, and allowed Tailscale/WebRTC UDP. The existing
\`ops/deploy.sh\` builds, installs and starts the gateway; it backs up the prior
room service and supports rollback. The Chrome profiles and overlays do not
move or get recreated.

Check deployment using:

\`\`\`bash
sudo bash ops/health.sh
curl -fsS http://127.0.0.1:8766/api/browser-live/status
curl -fsS http://127.0.0.1:8766/api/state
\`\`\`

Select a bot in the MAG room, enable the live-browser checkbox, then open
\`https://example.com\`. The URL, browser image and mouse/keyboard interaction
must all refer to that bot's same Chrome tab; switching to the other bot must
not change the first bot's tab. The status endpoint reports each VM and viewer
count; a VM's age must remain below a few seconds when its controller is up.
A CI-green build proves protocol compilation, not actual Tailscale ICE
connectivity or runtime VM screenshot FPS.

## MAG operator UX and isolated live consoles (14 October 2026)

The room is a three-column, dark workbench. The left (bots) and right
(browser/terminal) rails resize independently by dragging their inner
edge; either can be collapsed from the header. Widths and desktop collapse
state persist in browser local storage. On narrow screens, one rail can be
opened at a time without obscuring the main chat controls.

Each bot still owns its **own guest VM, Chromium profile, and terminal**.
Selecting a bot chooses both its hot browser and its VM terminal. The
console is not a host shell. Its dedicated WebRTC DataChannel
(`console`) relays signed per-VM UDP to a real persistent `forkpty` Bash
session, running as the guest's unprivileged `bot` account. This replaces
the former one-shot HTML command input entirely: xterm.js now renders the
native ANSI terminal with keyboard shortcuts, Ctrl+C, colors, history and
dynamic rows/columns. The terminal lives where the Activity panel used to
be. It stays open inside the VM when the viewer closes or changes bots,
but will restart if the guest's controller process is upgraded or restarted.
No HTTP host-shell endpoint exists.

WebRTC teardown now closes abandoned offers and disconnected peers, sends
a signed unsubscribe as soon as the last viewer disconnects, and stops
screenshot capture while preserving Chromium and all VM disks. Browser
controller restarts carry an authenticated instance nonce to reset frame
sequence tracking without discarding the open tab. The Go relay holds at
most one incomplete JPEG per VM and rejects old or expired fragments;
the frontend decodes onto a canvas, closes each ImageBitmap, and discards
incomplete frames and console fragment buffers on disconnect. Browser
navigation automatically expands plain hostnames such as `example.com`
to `https://example.com`; explicit HTTP(S) URLs remain intact.

For a new user request the selected bot immediately shows a pulsing,
visible status, then `Denkt nach`, tool activity and progressively
formatted/sanitized Markdown as the model streams. A backend failure or
empty generation is reported in chat rather than leaving a name-only
message.

Runtime smoke-check on MAG after installing the release:

1. Select bot A, open `example.com`, click the page, type into a field,
   press Enter and Backspace. Verify the same browser remains open during
   panel collapse/expand and that `/api/browser-live/status` reports viewers.
2. Run `pwd`, `whoami` in bot A's terminal, then switch to bot B and run
   them again. Both must execute as `bot` in *different* guest VMs.
3. Start a long bot task: pulsing progress must appear immediately, tool
   activity must update, Markdown formatting must appear during the stream,
   and the placeholder must disappear on completion or error.
4. Close the right sidebar and verify the viewer count falls to zero and
   UDP guest screenshot sending stops promptly. Switch back: the browser
   profile, previous tab, and VM filesystem must be unchanged.
5. `sudo bash ops/health.sh` and
   `curl -fsS http://127.0.0.1:8766/api/browser-live/status`
   check services and VM heartbeats. GitHub CI cannot prove ICE/network
   connectivity or native guest keyboards on the actual MAG host.


### Native terminal release contract

MAG vendors pinned xterm.js and FitAddon assets during deployment into
`/static/` for first-party serving. Browser clients never depend on the
CDN, and missing assets block installation rather than leaving a fake input
fallback. The local GitHub Actions build also downloads and validates the
same assets. Guest PTY behavior is independently tested for interactive
input, output and resize in `build/test-guest-pty`.

Use the xterm terminal to type `whoami`, `pwd`, `ls --color=auto`, and
`sleep 30` followed by Ctrl+C. The shell should return a prompt after Ctrl+C,
resize to the sidebar width and preserve its scrollback when switching bots.
The Browser live toggle only controls picture frames, **not** the terminal
channel. This is not an SSH emulator or a command input field: Bash reads
real PTY keystrokes in the corresponding isolated VM.

### Console viewport/scrolling (14 October 2026)

The selected bot's xterm is a fixed-height panel with its own bounded
scrollback. Its terminal host and screen are constrained to that panel,
rather than expanding the right sidebar as new shell output arrives.
Mouse-wheel/touchpad events over the terminal scroll **xterm history**
only; outside the terminal, the right sidebar scrolls normally.
Typing after reviewing history returns to the live shell cursor.
Resizing the right sidebar or the terminal panel fits the PTY once per
animation frame and reports the rows/columns to the selected VM.
Changing the selected bot keeps per-bot scrollback and does not recreate
the remote shell. This is only a UI scroll fix; no guest images/disks,
model settings or browser profiles are touched.

To check it on the MAG host: in Ab's terminal execute
`for i in $(seq 1 150); do echo "line $i"; done`, scroll over the
terminal with the mouse to read older lines, scroll down again, type
`pwd` and press Enter. Both the command prompt and new output must stay
visible within the terminal, with no outer sidebar jump. Repeat after
resizing the right panel and in Joe's terminal **if Joe's VM exists**.

## Tailscale HTTPS: one remote desktop URL per bot

MAG reuses its existing Tailnet HTTPS origin, not one Tailscale proxy or
certificate per VM. Every bot's **persistent name slug / agent ID** is also
its dedicated URL. Example:

- `https://mag.tail3e02ae.ts.net:8446/bot/ab/`
- `https://mag.tail3e02ae.ts.net:8446/bot/joe/`

The examples resolve only if bots with IDs `ab` and `joe` currently
exist. The room lists an **Fernzugriff** link next to each registered bot,
derived from its actual persisted ID. Bot creation immediately enables the
URL; deletion immediately returns HTTP 404, without rewriting proxy config,
restarting TailScale or leaving an orphan web route. VM registry and room
files are the source of truth; no parallel URL database or bot router.

The remote desktop is a focused version of the **same MAG web client**:
live Chromium screenshots/keyboard/mouse and a real xterm.js PTY on
separate per-bot authenticated WebRTC/UDP channels. The URL locks the
browser/console selection to that bot and omits chat/other bots' controls.
Opening multiple remote viewers never creates a new Chromium browser
instance or changes the persistent guest profile. Normal browser and
terminal fullscreen controls remain available. Remote sessions tear down
WebRTC peers when the page is closed; Chromium and the guest VM remain up.

**Access scope:** The dedicated URL is a navigation link, not an
independent security credential. MAG remains accessible only to devices
allowed by your existing Tailscale ACL and HTTPS Serve configuration.
Do not enable public Funnel or expose localhost ports to the public
Internet to make these links work. Anyone authorized to use the MAG room
within the Tailnet already has its normal operator permissions.

**Verify after deployment:** open the actual room over Tailscale,
click the listed Fernzugriff links and verify Browser+PTY control of Ab
and Joe independently. Closing one remote page must not close the
corresponding Chromium window or change the other bot's screen. Test
`curl -I` or GET on a nonexistent `/bot/unknown/` path to confirm 404,
then create/delete a temporary test bot only if resource capacity permits.
GitHub CI checks routes/UI/streaming compilation but not your Tailnet
ACL, HTTPS certificate or live UDP ICE traversal on MAG.

## Resilient per-VM browser-controller recovery

When MAG restarts or deploys a new browser controller, each existing bot
**keeps the same Chromium process, CDP page, cookies and profile**. MAG first
tries the authenticated controller state endpoint up to three times to
avoid interrupting a temporarily busy but healthy controller.

If an upgrade is genuinely necessary, only the selected VM's unprivileged
`mag-browser` process is stopped. The guest script ignores zombie/defunct
processes (which cannot own a listening socket), waits up to five seconds
for SIGTERM, then gives an unresponsive controller a narrowly scoped
SIGKILL and waits another three seconds. It does **not** signal `chrome`,
`chromium`, the guest itself or other bots. The replacement binary is
downloaded into a temporary file, atomically installed, and accepted only
after its signed `/state` endpoint reports the current `MGB4` protocol.
Failures include short guest-local process/log diagnostics rather than
unconditionally retrying or attempting to recreate the VM.

A Linux process regression in `test-browser-recovery` verifies ordinary
termination, stubborn processes that ignore SIGTERM, preserved unrelated
processes and ignored zombies. It runs in GitHub Actions before deployment.
Actual guest kernel/process state still needs confirmation on MAG.

If a VM temporarily reports `Existing browser controller could not be
stopped`, first deploy this recovery fix; do not delete/recreate its disk.
Server-side diagnostics (without resetting the bot):

```bash
sudo magbot status
sudo magbot exec agent2 -- "ps -u bot -o pid,ppid,stat,comm | grep -E 'mag-browser|chrom' || true"
sudo magbot exec agent2 -- "tail -n 20 /home/bot/browserd/browser.log 2>/dev/null || true"
curl -fsS http://127.0.0.1:8766/api/browser-live/status
```

Check MAG's browser in the Tailnet UI after deploying. A successful CI run
does not establish live browser/CDP connectivity on the private KVM host.
