# MAG architecture

One home server, one user (Chris, phone and desktop), one room, two bots.

```
phone/desktop ── Tailscale :443 ── nginx (TLS, whois auth_request) ── magroom :8766
                                                                        │
                    bot VMs (KVM, user bot) ── bridge 192.168.122.1:8767┤
                    mag-audit-gateway :8080 ── llama-server lena :8191 (NUMA0)
                                            └─ llama-server noah :8192 (NUMA1)
                    mag-modeld (download, verify, start)   /run/mag-models/status.json
                    mag-idol :8793 (photo to 3D on the MI50)
                    mag-browser-webrtc :18784 (live view of the VM browsers)
                    mag-stt :8194 (speech to text, cores 28-55)
```

## Room (`mag-room/native`, C++17, one process)

| File | Responsibility |
| --- | --- |
| `util.hpp` | time, ids, atomic files, subprocess with streamed output and cancel |
| `http.hpp` | thread-per-connection HTTP/1.1 server, file ranges, libcurl client |
| `auth.hpp` | Tailscale LocalAPI whois for nginx, HMAC proof, owner check, CSRF |
| `room.hpp` | configuration, event bus, the message store, bots, settings |
| `llm.hpp` | llama-server client through the audit gateway: chat stream, slot actions |
| `engine.hpp` | who answers when: turns, append-only prompt log, context budget, PASS |
| `tools.hpp` | tool schemas and dispatch |
| `vm.hpp` | guest exec with live output, file writes, browser controller, VM tokens |
| `site.hpp` | bot web pages: HTML blocks and `www` files published on the bot's site, shown as live previews |
| `idol.hpp` | photo-to-3D jobs as room messages |
| `push.hpp` | Web Push (VAPID) |
| `api.hpp` | HTTP routes, event stream, VM bridge |
| `magroom.cpp` | wiring |

Helper binaries: `mag-modeld` (model supervisor), `mag-audit-gateway`,
`magbot` (`vm.cpp`: VM lifecycle and guest exec), `mag-browser` (controller
inside each VM), `mag-browser-webrtc` (Go, live view gateway).

### One message model

Everything in the timeline is a message, stored as
`STATE/messages/<id>.json`; ids sort by time. A message has `from`
(`chris`, `lena`, `noah`, `idol`), `text`, `state` (`streaming`, `done`,
`stopped`, `error`), `attachments[]`, and for a bot the ordered `parts[]`:
text and steps (tool calls with the real input, output, runtime and exit
state) in the order they happened. A 3D result carries `model3d` (job,
progress, quality) and its GLB/STL/PLY as attachments. Attachments live in
`STATE/files/` and belong to exactly one message: deleting the message
deletes them, and for a 3D message also the worker's job directory.

### Events

`GET /api/events` is one SSE stream. Every event carries a sequence number;
`GET /api/state` returns a snapshot with the sequence it reflects. A client
opens the stream first, fetches the snapshot and applies only newer events,
so a reconnect never loses or doubles anything. Streamed text arrives as
`delta` events in the order the tokens were produced; step output as
`step_output` events while the command runs.

### Prompt log and KV cache

Each bot owns an append-only prompt log (`STATE/wire/<bot>.json`): the system
prefix, then exactly the messages that were sent to its llama-server. A turn
appends what the bot has not read yet and calls the model with
`cache_prompt`, so the server only evaluates new tokens. Nothing that was
sent is rewritten (the clock is part of the message it arrived with). When
the log exceeds the bot's token budget the oldest turns are dropped in front
of a user message; `--cache-reuse` lets the server shift the kept tail
instead of evaluating it again.

Each llama-server has one slot, no host prompt cache and no context
checkpoints. The only slot state ever written to disk is the evaluated system
prefix of the day (`prefix-<hash>-<date>.bin`).

### Trash button

`POST /api/room/clear` stops all turns, deletes every message and attachment
(and all Idol job directories), resets both prompt logs to the system prefix,
erases the slot of every llama-server (`POST /slots/0?action=erase`) and
restores only the prefix state. No model is reloaded and nothing of the old
conversation can come back; the next answer starts from a warm prefix.

### Conversation rules

See the execution contract in `AGENTS.md`. In short: a human message without
an addressee is answered by all active bots (`@name` addresses one); a
visible bot message may be answered by the other bot; `PASS` is invisible and
when all bots pass the room rests; the setting "Bots untereinander" bounds
the bot rounds; new human input and Stop preempt bot rounds at once; one
request per llama-server at a time; tool work continues on its own only after
a successful tool result with distinct progress and `[[MAG:WEITER]]`.
A message Chris sends while a bot is working on his earlier request is handed
to that bot at its next step.

### Bot VMs

Each bot has its own KVM guest. The room reaches it only through two narrow
sudo wrappers (`magbot-guest-run`, `magbot-agent-ctl`). `magbot exec --live`
relays the output of a command while it runs. A VM reaches the room only on
the bridge listener, with a token derived per VM, so it cannot act as the
other bot. "Internet für Bots" off is a host firewall switch
(`magbot-netmode`), not a prompt rule.

In the bot's panel the owner has that browser live, a real terminal and a file
view of the bot's home (`mag-room/web/app/live.js`).

Browser and terminal use the VM's realtime link. `mag-browser` in the VM
captures its Chrome as JPEG frames (while someone is subscribed, about five a
second), takes pointer, wheel, key and text input, and holds one Bash PTY as
user `bot` that outlives every viewer. It talks to `mag-browser-webrtc` on the
host over UDP, authenticated with a key per VM. The gateway hands that to the
page as WebRTC data channels: `frames` (unreliable, a late frame is dropped),
`control` (input for the page) and `console` (the PTY in both directions, with
a heartbeat every 2 s so a dead session is noticed and reconnected). The room
only passes the SDP offer on (`POST /api/browser-live/offer`). The page shows
the frames on a canvas and runs xterm.js (vendored in `web/static`) on the PTY.
Where UDP cannot get through, the browser view falls back to pictures over
HTTPS (`/api/screen`) and the terminal offers single commands
(`/api/vm/<bot>/run`); the file view always goes through the wrappers.

### Documents

`Engine::document_text` turns an attached PDF, DOCX, PPTX, ODT or ODP into
text once and keeps it in `STATE/text/<file>.txt` (removed with the message):
`pdftotext -layout` for the text layer; when a PDF has none, `pdftoppm` and
`tesseract` (deu+eng) read its first six pages; office files are unzipped and
their XML reduced to paragraphs. The bots get up to 12,000 characters of each
document inline; the whole text is copied to the bot's VM as `<name>.txt`.

### Dictation

The page captures the microphone in an AudioWorklet (`web/app/capture.js`:
any device rate to 16 kHz mono PCM, 100 ms frames, the last 400 ms kept while
the microphone is open but idle) and `web/app/dictate.js` posts what has been
spoken since the last request to `POST /api/stt/live?sid=&seq=`, one request
after the other; `end=1` closes the dictation. The room only checks the
caller and passes the chunk on to `mag-stt` (`native/mag_stt.cpp`).

`mag-stt` keeps one recognizer loaded (transcribe.cpp, Parakeet TDT 0.6B in
primeLine's German fine-tune, Q8 GGUF, 16 threads on cores 28-55). The model
hears a recording as a whole in about 1/20 of its length, so live text comes
from decoding the open part of the recording again whenever at least 200 ms
of new audio are there. Every answer carries `committed` and `partial`:

- shown while speaking: words heard to their end (the newest one once two
  decodes agree on it), a full stop once silence follows it;
- committed, and the audio dropped: a sentence both of the last two decodes
  have and that is followed by 0.9 s of silence (2.5 s without a full stop),
  or that lies at least 1.5 s back once more than 12 s are open;
- chunks are numbered, so one that is sent again is not heard twice.

`POST /api/stt` takes a finished recording in any format ffmpeg reads and
answers with its text. `ops/stt/bootstrap.sh` builds the pinned recognizer
and fetches the pinned model once; `ops/stt/install.sh` is the deploy layer.
CI builds `mag-stt` without the recognizer and runs its protocol on a fake
engine whose "speech" spells its own transcript.

### Bot sites

A bot's site is `<name>.bot.stardenburg.de`, tailnet only. nginx hands every
host of that zone to the room; the room resolves the name (the current one
serves, an earlier name, the bot id or the VM name redirect) and sends the
files the bot's VM published (`/var/lib/mag-bots/www/<vm>`, uploaded by the VM
through `mag-bots-publish`). A finished HTML block of an answer, or a file
written below `/home/bot/www`, is published and framed from that site in the
chat. The room page's policy allows frames from that zone and nothing else.

### What the bots read

Every finished message of the others, with what belongs to it: small text
files inline, 3D orders and results, images. A bot with a vision projector
gets the image itself (a 1280 px copy, images in front of the text); once per
image it writes down what it shows, asked on a branch of its prompt log that
is not appended. A bot without eyes waits briefly for that description.

## Photo to 3D (`ops/idol`)

`worker.py` keeps one job at a time on the MI50. A job is a folder below
`/var/lib/mag-idol/jobs`: `status.json` (state, phase, the running stage's
process) and the result of every finished stage (`splat.ply` after sampling,
`mesh.glb`/`mesh.stl` after meshing). A stage runs in its own session with its
output in a log and its exit code in a file; the unit has `KillMode=process`.
So a restart of the worker (a deploy) leaves the stage running and the new
worker follows it again; after a reboot the job starts again at the first
stage whose result is missing; queued and running jobs are taken up again in
order at every start. The room mirrors the job into its message (phase k of
n, the sampler's step and its own estimate) and picks jobs up again after its
own restart.

## Models (`ops/models`)

`models.json` declares both bots. `mag-modeld` owns download, verification
and start; the room only reads `/run/mag-models/status.json` and writes
control verbs. See `AGENTS.md`.

## Deploy (`ops/deploy.sh`)

Build, stage, switch, verify `/readyz` and nginx, roll back on failure. No
drain and no model gating.
