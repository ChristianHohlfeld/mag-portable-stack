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
                    mag-scene :8795 (scenes from 3D models, Blender on the CPU)
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
| `llm.hpp` | llama-server client through the audit gateway: chat stream, slot actions; what a model wrote made fit for the chat |
| `engine.hpp` | who answers when: turns, append-only prompt log, context budget, PASS |
| `tools.hpp` | tool schemas and dispatch |
| `vm.hpp` | guest exec with live output, file writes, browser controller, VM tokens |
| `site.hpp` | bot web pages: HTML blocks and `www` files published on the bot's site, shown as live previews |
| `idol.hpp` | photo-to-3D jobs as room messages |
| `scene.hpp` | scenes the bots build from 3D models, as room messages |
| `mcp.hpp` | plugins: remote MCP servers, their tools per bot, approvals |
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
deletes them, and for a 3D message also the worker's job directory. A scene
is such a message too (`model3d.kind = "scene"`, from `mag-scene`): its
animated GLB is there from the start; its film joins it when the owner asks
for one (`POST /api/3d/<id>/film`), its `.blend` likewise
(`POST /api/3d/<id>/blend`). The message keeps how the scene was described
(`model3d.spec`) and what was asked for (`model3d.prompt`). A user message
written in the line below a scene or a 3D model carries `about` (that
message's id): the page shows where it belongs, and the bot that reads it is
told how the scene was made (or which model is meant).

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

While a turn runs, the log carries `at_work` (why the bot was asked, and
whether a message of the turn exists yet). The mark goes when the turn ends,
unless the room itself is going down. A room that starts and finds it knows
that a restart cut the turn off: if Chris had asked and nothing of the
answer could be seen yet, the turn runs again once the model is ready (at
most three times for one turn), for the bots his message had named.

Each llama-server has one slot, no host prompt cache and no context
checkpoints. The only slot state ever written to disk is the evaluated system
prefix of the day (`prefix-<hash>-<date>.bin`).

### What a model wrote

The server parses a model's output into text, thoughts and tool calls. The
room does not rely on that going well (`llm.hpp`):

- **Markup.** The control tokens of both families are read for what they
  mean when they arrive raw in the content: Gemma's `<|channel>thought …
  <channel|>` and gpt-oss's `<|channel|>analysis<|message|> … <|end|>` are
  thoughts, a raw tool call and a turn made up for somebody else are dropped,
  every other tag disappears (`Clean`; code blocks and inline code are left
  alone). No tag reaches the chat, a thought, or the bot's own log.
- **Circles.** A text whose end is the same passage three times over (or the
  same long passage a fourth time) is a model that lost its way: the room
  closes the connection, which ends the generation, and keeps the text up to
  the end of the first round (`circles`). Inside an open code block and in
  tool-call arguments equal lines are normal, so the bar is far higher there.
  Thoughts in circles are asked once more from the start, then the answer
  ends with one line saying so.
- **Tool calls.** With Gemma the server's grammar reads a string argument up
  to the model's own quote token. A model that closes a string with a plain
  quote stays inside that string for the grammar: it cannot end its turn and
  writes markup and its answer into the arguments until its tokens run out.
  The room ends the generation at the first tag inside arguments; what the
  model meant stands in front of it and is called (`mend`). Arguments that are
  no JSON object after that are stored as `{}` and the tool is not run, the
  model is told why. Invalid arguments never enter a prompt log: the server
  refuses every later request whose history holds them. A log from before is
  repaired when it is read.
- **Errors.** What an answer ended with is one short line (`short_error`); a
  server error may quote a whole generation.

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
to that bot at its next step. It counts as a new request there (a bot builds
one scene per request, so it may build another one now), and the turn the
message queued does not answer it a second time.

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
gets the image itself (a 1280 px copy, each image right in front of its own
`[Bild …]` line); once per picture it writes down what it shows, asked on a
branch of its prompt log that is not appended, with the picture again in
front of the question. Pictures are told apart by their content (SHA256): a
picture posted twice has one description, and `POST /api/file/<f>/describe`
(owner) has it made anew for every copy. A bot without eyes waits briefly
for the description and gets a renewed one as an addendum.

`POST /api/upload` follows a JPEG, PNG or WebP to its end; one that ends in
its picture data is answered with 422 `truncated`. The page reads the file
again twice (a phone may still be writing a photo it has just taken) and
then sends it with `force=1`, which stores it marked `cut`. Before the GPU
stage the Idol runner measures how much of such a photo is missing and
refuses it in words (`MAG-ERROR`), which is what the 3D message then shows.

### Plugins (MCP)

A plugin is a remote MCP server (Streamable HTTP, protocol 2025-06-18; JSON
or event-stream answers). The owner connects it in the settings
(`POST /api/plugins`: the room initializes a session, reads `tools/list` and
keeps name, description, schema and the read-only hint of every tool in
`STATE/mcp/servers.json`) and gives it to a bot in the bot's sheet
(`PUT /api/bot/<id>` with `plugins: {<server>: {approval, off[], always[]}}`).
Only `https://` is accepted, or `http://` on this machine and the tailnet;
everything else leaves through the audit proxy, and "Internet für Bots" off
means no plugin outside is reached. A token lives in `STATE/mcp/<id>.secret`
(0600) and is sent as `Authorization: Bearer` or a header of the owner's
choice; it never reaches the page, a prompt or a log.

A bot's plugin tools are named `mcp__<server>__<tool>` and follow the room's
own in its tool list (at most 64); what the owner wrote about a plugin and
what the server says about itself stand in the system prompt. Both are part
of the prompt prefix, so they change only with a binding or a refresh. Every
bot has its own session per server. A call of a tool that is not read-only
holds the bot's turn: its step carries `approval` (`id`, tool, arguments),
the page shows "Erlauben / Immer erlauben / Ablehnen"
(`POST /api/approval/<id>`), a push goes out, and without an answer it runs
out (`MAG_MCP_APPROVAL_S`, 15 minutes). "Immer" is kept in the binding; a
binding set to `auto` asks nothing. A server that works with the owner's own
connector token (`auth: connector`) is sent `X-MCP-Readonly` and never
changes anything unasked.

### Resetting a bot

`POST /api/vm/<bot>/reset` (`Engine::reset_vm`) puts a bot back to its first
day. The steps, each of which can be done again: `stop` (the turn is
cancelled, nothing waits for it), `memory` (the bot's memories, its prompt
log, which starts again behind the last message, and its slot), `vm`
(`magbot-agent-ctl reset agentN`). The state is the `reset` object of the
bot's record (`state` running or failed, `step`, `vm`, `error`): a room that
starts with a running reset goes through it again from the top, and the VM
check at start-up leaves such a bot its VM name. While it runs the bot gets
no turn; what was written to it meanwhile is answered afterwards. A pause
and a stopped model are not touched.

`magbot reset` holds the lifecycle lock, asks the host's admission first
(so a refusal costs nothing), then tears down whatever is there (a domain in
any state, a directory with or without a disk, a damaged record, the rest of
a spawn that was cut off) without archiving, and spawns under the same name
with the same size. `site.sh reset` deletes what the bot published and gives
it its first page again. The Gitea account of the VM name stays (its
credential is issued anew), as do its repositories.

## Photo to 3D (`ops/idol`)

`worker.py` keeps one job at a time on the MI50: it is the one queue of
every job that needs the card (3D, and pictures from words), and the card
belongs wholly to the job whose turn it is (at its 72 W; "wholly" never means
more power). A stage that uses the card (`sample`, `shape`, `image`) also
holds `/var/lib/mag-idol/gpu.lock` (flock) for as long as its process runs,
across a restart of the worker; anything else that would use the card takes
the same lock. A stage that finds it taken waits, says "Wartet auf die
Grafikkarte" in the chat, and is not taken for standing still. A job is a folder below
`/var/lib/mag-idol/jobs`: `status.json` (state, phase, the running stage's
process) and the result of every finished stage (`splat.ply` after sampling,
`mesh.glb`/`mesh.stl` after meshing). A stage runs in its own session with its
output in a log and its exit code in a file; the unit has `KillMode=process`.
So a restart of the worker (a deploy) leaves the stage running and the new
worker follows it again; after a reboot the job starts again at the first
stage whose result is missing; queued and running jobs are taken up again in
order at every start. The room mirrors the job into its message (phase k of
n, the sampler's step and its own estimate) and picks jobs up again after its
own restart. A job that failed or was cancelled keeps its folder; "Noch
einmal" at its message (`POST /api/3d/<id>/retry`, the worker's
`/api/idol/jobs/<id>/retry`) queues it again, and it goes on at the first
stage whose result is missing, like after a reboot.

A photo with several things in it: before the order, the page asks which
object is meant ("Objekt wählen": `POST /api/3d/select`, the worker's
`/api/idol/select`). `objects.py` runs SAM 2 (`facebook/sam2.1-hiera-small`,
pinned in `install.sh`) on the CPU, cores 28-55, never the MI50: the object
at the place Chris tapped, or every object by itself (a grid of places, no
background, nothing twice). A thing Chris names instead is found by a bot
that sees (`Engine::locate`: the image in front of the question, on a branch
off its prompt log, answered with a place in per mille), then SAM 2 takes
that place. The page shows the mask, cuts the photo with it
(a PNG with alpha) and orders with that: Idol takes the alpha as the
foreground and does not guess one. "Alle einzeln" orders one normal job per
object Chris leaves marked. Nothing of a selection is kept on the worker.

A photo ordered for 3D is one job of the photo as it is, with Idol's own
foreground; nothing is split or looked at unasked (Chris found the automatic
splitting too much). Several things of one photo are what "Objekt wählen" is
for: one tapped or named, every one by itself, or the whole room. Both are
offered beside the photo before it is sent ("Objekt wählen", "Ganze
Szene"); "Ganze Szene" opens the same sheet and looks for the room's objects
at once, so Chris only takes off what does not belong and orders.

"Ganze Szene" is the same with places: `objects.py --scene` also estimates
how far away every pixel is (Depth Anything V2, the small model for rooms,
in metres; pinned in `install.sh`; the focal length from the photo's EXIF,
else a phone's main camera) and gives every object where it stands (x to
the right of the camera, z away from it, its foot's height above the floor),
how tall it is, and the room (left, right, back, height, the colours of
floor and wall). The page orders each object with that (`scene` of the
order, kept as numbers in `model3d.scene` with a group id). The mesh stage
notes from where the photo looked at each model (`mesh.json`, the direction
of the camera `photo.py` found). When no model of a group is under way any
more (`Api::assemble`, also after a restart, once per group), the models
that were made become one scene: `tall`, `lift`, `seen_from` and `at` of
each actor and `room` of the scene are what `scene.py` builds it from. The
photo is assumed to be taken about level; a camera that looks steeply down
puts the floor too low.

No stage has a time limit. The worker ends a stage only when it stands
still (for ten minutes it neither writes to its log nor uses the processor)
and starts it again; after three standstills the job fails in words and can
be taken up again like any failed job.

The shape stage comes first. Where it made a shape whose picture it covers
(`front_of`), the model is made from the shape and the photo alone
(`mesh.py --alone`, `reconstruct_shape`): no splat is sampled, which saves
the MI50 about a quarter of an hour per job; the model is scaled to about
one unit at its largest like Idol's, and seen from +z. Only where that does
not do (`alone.failed`), or for a job that was already sampling when the
worker started, Idol samples and the mesh is made from splat and shape as
described next. "Netz neu" makes a model anew from whichever it has.

The shape stage (`shape.py`, on the MI50, in
its own environment `~/idol-mi50/triposg/venv` that reuses Idol's ROCm torch)
asks TripoSG (VAST-AI, MIT, code and weights pinned) for the whole, closed
shape of the thing from the same cut-out picture: Idol's splat is a relief
seen from the side, TripoSG's shape is a head from every side. It writes
`shape.ply`; it never costs a job (not installed, failed: the mesh is made
from the splat as below, and the stage is not tried again for that job).
`mesh.py` fits the shape onto the splat (`fit_shape`: the 24 ways a box can
lie, each started with the splat's front on either end of the shape,
trimmed nearest-point fitting with scale, the splat asked from its side
only) and uses it when at least half of the splat's points lie on it (a splat has loose flaps at its sides); when it does not,
the shape stands where the photo's camera (found from the splat) saw it, and
only without that camera the splat's own shell is used. TripoSG sees its
picture straight from +z without perspective, a little smaller than its
-1..1 square (`front_of` finds how much smaller from the outlines; some
things TripoSG turns its own way, so `facing` tries the four turns about
the upright axis and keeps the one whose outline covers the picture), so the
shape lies exactly under the picture it was made from: the side the photo
shows takes the photo's own colours (`shape_photo.png`, the cut-out as fine
as the photo was taken, up to 4096 px), not the splat's blur with a little
detail on it; glasses, eyes and letters are as sharp as the photo. What the
photo did not see takes its colours carried over the surface (`carried`:
every corner the colour of the nearest seen one along the surface, a step
sideways in the photo counted three times, softened), so the back of a head
is the hair's colour, reached over the crown rather than around from the
cheeks, and the back of a shirt the shirt's. A shape TripoSG's grid left
open somewhere is closed (`mended`) before it is used. Without a shape the colours come from the splat and the
photo's fine detail as below. A person (or anything) the photo cuts off at its
lower edge stands on a clean, flat base there (`based`: `shape.py` notes in
`shape_cut.json` where the photo ends below the thing, the shape is cut
level a hair below that line and the cut closed with a flat face), not on
a ragged edge or a body TripoSG made up beneath it; a cut that would not
leave a solid is not made.

The mesh stage (`mesh.py`, CPU) makes the model from the splat, a cloud of
small flat Gaussians lying on what the photo showed:

1. `surface.opacity_field`: every Gaussian, none left out, is written into a
   grid with its own three widths and its own turn. A cell is as opaque as
   the Gaussians in it together (1 minus the product of what each lets
   through), so the shell is where that is one half, wherever many or few
   Gaussians lie.
2. `surface.body_of`: pores of a cell or two are closed, the grid is flooded
   from outside, and what the flood does not reach is the body: closed, with
   no shells or bubbles inside. That leaves the thing as it is: legs apart,
   arms off its sides, a bowl open. One case it gets wrong: where the
   Gaussians of a side the photo did not show leave a gap wider than a
   pore, the flood runs into the thing and leaves only its skin. That is
   known by what it leaves behind: large rooms walled in by the shell on
   all six sides (or on all but the underside). Only then are those rooms
   added, and the tear out to where the shell would run. A thing the flood
   did not get into is not touched at all. Loose bits fall away; a piece
   that is big enough is a figure of its own and stays (one photo may show
   a gnome and a fox).
3. The surface is taken anew from that body by marching cubes (uniform
   triangles, no holes), smoothed, and decimated twice: to the faces a
   printed model may have (the STL), and to the far fewer a shown model
   needs (the GLB). Both stay solids: closed, every edge between exactly
   two faces (what decimation breaks is mended, or fewer faces are taken
   out), faces outwards, no bubble inside, no corner where two sheets only
   touch. The log line of the stage says `solid`.
4. The small mesh is unwrapped (`unwrap.py`): xatlas in a process of its own
   for a limited time, and if it is not done by then the plain way (faces
   that look the same way and hang together are one chart, laid flat, the
   charts side by side; a second of work). The texture (2048² for "fast",
   4096² otherwise) is baked from the splat at every texel the way a splat
   is drawn: every Gaussian near the texel counts by how much of it lies
   there, with its own three widths and its own turn. So the colours are
   as sharp as the splat, however few faces the model has.
5. The side the photo shows takes its fine detail from the photo itself
   (`photo.py`). The photo's camera is found again from the splat alone:
   the view of the Gaussians that covers the photo's outline and has its
   colours, judged on the square around the thing (from everywhere around
   first, then closer from the best places; about a minute). The splat
   keeps its colours, which are of one piece around the whole thing; what
   the photo has more is added: the photo minus the photo blurred (within
   the thing's outline). Every texel that camera sees straight enough, with
   nothing in front of it, inside the outline but not at its rim, gets
   that, the more the straighter it is seen. So there is no seam of tone
   between the side the photo shows and the others, and a photo that does
   not sit exactly on the model misplaces only fine detail. The sampling
   stage notes which square of the photo the model was shown
   (`outputs/input_crop.json`, written by `runner.py` around the SDK's
   unchanged preparation), so the detail comes from the photo as fine as it
   was taken, not from the 1024² copy the model saw. If no view looks like
   the photo, the photo is not used, and the log line says so.

## Pictures from words (`ops/idol/imagegen.py`)

"Bild erzeugen" in the plus menu, or a bot's `image_generate` tool, orders a
picture: FLUX.2 klein 4B (Black Forest Labs, Apache-2.0, four steps, fp16 on
the MI50) in Idol's environment. It is a job of the Idol worker's one queue
(`POST /api/idol/images`, `kind: image`, result `image.png`), so the card
does one thing at a time and a picture waits behind a running 3D job. The
room's message (`model3d.kind = "image"`) shows the progress and becomes an
ordinary image of the chat, which the bots see and which can go on to
photo to 3D; cancel and "Noch einmal" work as for 3D jobs. The weights are
fetched once by `ops/imagegen/fetch.sh` (pinned revision, resumable).

## Videos from pictures (`ops/idol/videogen.py`)

Every picture of the chat (uploaded, painted, a bot's) has "3D" and "Video"
below it (`POST /api/image/act`, `{"file", "do": "3d"|"video", "quality",
"prompt"}`). "3D" orders photo to 3D for that picture as it is; the model
can then be animated at its message. "Video" asks what should happen and
orders a video: Wan 2.2 I2V-A14B (Wan-AI, Apache-2.0), its two experts
(high noise, then low noise) as GGUF Q6_K (QuantStack) with the Lightning
4-step LoRA (lightx2v), four Euler steps (shift 5, as the LoRA was
distilled; two for each expert) without guidance, 81 frames at 16 fps,
about 480p in the picture's shape, H.264 by ffmpeg. The card has no bf16
and no fast attention: the experts compute in fp16, attention is exact but
built a block of queries at a time, UMT5 reads the words in fp32 and leaves
the card before the experts come. It runs in `~/idol-mi50/video/venv`
(Idol's torch and diffusers through a .pth file, plus gguf, peft, ftfy);
the weights come from `ops/video/fetch.sh` (pinned, resumable, SHA256, one
writer) and until `.complete` is there the worker refuses a video in words
(`POST /api/idol/videos`, `kind: video`, result `video.mp4`). The room's
message (`model3d.kind = "video"`) shows the picture it comes from, the
stage and the steps, and becomes an ordinary video of the chat. A bot orders
one with `video_generate` (`image`: the picture's name as the chat shows it,
else the newest picture; `prompt`: the motion), and reads the video message
as "Video aus dem Bild …" with how it ended.

The MI50's one line: 3D, pictures and videos are jobs of the one worker,
which runs one at a time; a GPU stage also holds the lock file, and a lock
file that cannot be opened never fails a job. A queued job shows how many
jobs are before it (`ahead`, from the worker); up to 32 wait. Each job's
place is its `order` in its own status.json, so the line is the same after a
restart of the worker or a reboot: the job whose stage still runs comes
first, then the line as it was. Chris moves a waiting job one place forwards
or backwards at its message ("Nach vorn", "Nach hinten": `POST
/api/3d/<id>/move`, the worker's `/api/idol/jobs/<id>/move?to=up|down`) and
cancels it there. A stage survives a restart of the worker and is followed
again; after a reboot a 3D job goes on at its first missing stage (shape,
splat, mesh), a picture starts its one stage again, and a video goes on
after its last finished step (`state.pt` in its folder: the words as read,
the picture's latent, the latents and the scheduler with its history).

The bots take part in the same flow: `image_generate` paints, `photo_to_3d`
and `video_generate` take a picture of the chat by its name (or the newest),
and `scene_3d` animates the models; every result is an ordinary message of
the chat that goes on from there.

## Reading aloud (`ops/tts`)

Runs in the browser, not on the server. `web/app/speak.js` drives a Web Worker
(`ops/tts/tts-worker.js`): Supertonic 3 (Supertone; a duration predictor, a
text encoder, a flow-matching estimator and a vocoder as ONNX, 44.1 kHz) in
onnxruntime-web, on the device's GPU (WebGPU) where there is one, else in
WASM on several threads. The GPU gets 90 s to make the model ready and
speak its first word; a GPU that fails, is silent or takes longer is not
asked again on that device (`mag-tts-gpu` in its storage), and the worker
makes the model in WASM. For the threads the
page is cross-origin isolated
(`Cross-Origin-Opener-Policy: same-origin` on the page,
`Cross-Origin-Embedder-Policy: credentialless` on every response, so the
bots' pages still run in their frames). A provider is taken only after it
has spoken one word that is sound, and the networks are run a few times in
the background, because their first runs are the slowest. A voice is a
style file (five female, five male); each speaker has a voice and a tempo,
kept on the device. The text is cut into sentences, and they are made ahead
of what plays (up to three at a time), so the reading does not stop
between them. How long a part takes is learned on the device as a line, a fixed
time per part plus a time per step and second of speech (on Chris's
phone's GPU the fixed time is about a second, so a part of a few words
plays for less time than it takes to make); the first parts are as long
as that allows, so each is made while the ones before it play, and where
the fixed time is large the later parts are longer (200 letters instead
of 120). The first part of a reading is a few words only, and on a
device that is not much faster than real time the first parts are made in
three or four of the model's five steps: the sound starts within a second
or two instead of after the first whole sentence; a device that cannot
keep up even so (WASM on a phone) reads on in two rather than stop
between sentences; one that makes eight steps in a quarter of the time
they play (a GPU) gets eight. How fast the device is,
is measured on every part. The parts are laid end to end on the audio
clock (each starts on the sample where the one before ends). The newest
answer in the chat is made whole before anybody asks, already while it
comes in (while nothing is read and the page is in view, cut as the tap
will cut it; the first two parts in the device's best quality, the rest in
as many steps as the device makes in the time they play; about four
minutes of sound are kept; of the five answers before it the first
words), so the tap that most often comes speaks at
once and plays to the end without a pause even on a phone that makes
speech slower than it plays. An open page reloads only for a release
that changes its own files or the voice (their content is the cache key
of the assets, `mag-ui` and `ui` in `/api/state`; a release of a service
behind it does not cost the phone the minute its voice needs to be made
ready again), and only after a reading has ended; it takes the room's new
token from `/api/state`. And a tap never
waits for the model: while it is still loading, the device's own voice
reads that message. The page tells the room's journal how a reading
went, as numbers (`POST /api/tts/stat`: provider, threads, steps, time to
the first sound, work against length), never what was read. Before a sentence goes to the model it is written as it
is spoken (`german()` in the worker: numbers, dates, times, units,
abbreviations, and what is spelled out: "3D" is "drei De"), and it is given decomposed (the model's table knows "a" and
the two dots, not "ä"). German and English are read by the same voices.

Browsers get the model with its weights in float16, about 200 MB instead of
400: `pack.py` moves every large weight out of its network's `.onnx` into
`<network>.f16.data` (the graph refers to it as float32 external data), and
the worker widens it to float32 again before it hands it to onnxruntime, so
the model computes as before. `source.lock` pins the model as published,
`models.lock` what browsers fetch, `assets.lock` the runtime and our
worker, all by SHA256; `bootstrap.sh` fetches and packs them once,
`install.sh` (deploy) puts them into `/opt/mag-tts/m-<hash>/` (the model),
`/opt/mag-tts/r-<hash>/` (the runtime, about 27 MB) and
`/opt/mag-tts/v-<hash>/` (the worker) and writes `config.json`; the room
hands them out under `/tts/` (immutable, except the config). Model and
runtime have folders of their own, so a change of the worker costs no
browser a new download. A new release of the page never touches the
voice's Cache Storage (`sw.js` clears only its own caches). The worker
keeps everything in Cache Storage (asked to persist), the weights in
pieces of 8 MB, three coming in at once, that are kept as they come (a
download that breaks off goes on where it stopped) and never put together
into one file; it reads them piece by piece into the network it makes. A
piece that gets no byte for 25 s is asked for again by the worker itself;
the page never ends the worker while it downloads. After the download it
tells the page after every step that it is still at work, so a slow phone
is not taken for a stuck one and started anew (time out of view does not
count). Shortly after the page has loaded it
fetches and loads the model in the background (over a mobile connection
too; not with data saving on), so the first "Vorlesen" speaks at once.
While it loads, a thin line under every "Vorlesen" fills up (and breathes
while the model is made ready); the settings say it in words. No toast.
Without the model the device's own voice reads. The Piper voices of before stay on disk.

## Scenes (`ops/scene`)

The bots' tools `models_3d` and `scene_3d` turn 3D models from the chat into
animated scenes. A scene is a description (who plays, what each one does, in
order), never code. `mag-scene` (`worker.py`, user `magbot`, cores 28-55, no
device access) runs the pinned Blender (`blender.lock`, fetched once by
`bootstrap.sh`) headless with `scene.py`:

- `parts`: a generated model is one mesh that may show several figures; they
  are told apart, and each one is read (`rig.py`). The mesh is filled into a
  grid of cubes, so the second wall such models have inside does not matter.
  What rises from a patch of the ground until it meets another leg or the
  body is a leg; a tail or a head is followed from its tip until the slice
  through it widens into the body. Two legs make a figure that walks upright
  (with arms where they stand off the body), three or four an animal, anything
  else a thing.
- `build` (while the tool call waits, so a wrong description comes back in
  words): every figure gets its skeleton and weights (each vertex follows the
  bone of its cube, softened along the surface), the actions become
  keyframes. A figure on two legs moves as people do and an animal as
  animals do (see below); a thing moves as a whole. It writes `scene.glb`
  (skinned, animated), which the room posts at once and the page plays in
  its own viewer, and `scene.blend`.
- `render` (only when the film is asked for; a stage on its own, like the
  Idol stages): Cycles on the CPU, frame by frame, then ffmpeg. A restart of
  the worker follows the running render again; after a reboot it goes on with
  the first missing frame. The film joins the scene's message when it is
  done. A built scene without a film costs nothing but its folder.

Somebody on two legs moves with recorded motions (`motion.py`, numpy only,
so the tests run it without Blender). `motions.npz` holds them as a figure
of any build can take them over: for every frame, seen from the body itself,
how hips, chest and head are turned from standing straight, where thighs,
shins and arms point, how high the hips are in leg lengths, and what each
foot does (in the air, on its toes, standing). `mocap/build.py` makes the
file from the recordings `mocap/clips.json` names (`sources.lock` pins them;
`MOTIONS.md` has their terms and the credit they ask for); a gait is cut to a
round that ends as it begins. In a scene every such figure is a `Performer`
that does something in every frame, a motion that was asked for or standing
and breathing:

- its bones point where the recorded ones did; the way it covers follows the
  length of its own legs, short legs step quicker, and a jump is as high as
  the figure is tall;
- a foot that stands is held where it stands and the leg is solved for it,
  the hips come down when it would be out of reach; a foot that ends up far
  from where it belongs (after a turn on the spot, a stop) takes a step;
- one motion goes over into the next without the two being mixed: what is
  left of the old one fades out on top of the new one (inertialization);
- an arm that hangs does what the recorded arm does, one that is held up or
  bent keeps its hold and only moves along; a gesture is made with the hand
  that is free;
- on its way it is steered towards where it goes, in whole strides; before a
  leap the run-up is taken as far as the leap itself runs;
- who has nothing to do looks at who does something.

For an animal on four legs there are no recorded motions that may be passed
on, so `animal.py` works its motion out, as the body does it (numpy only as
well). An `Animal` is in a scene what a `Performer` is:

- the feet take turns in the order of the gait (walk and stalking one after
  the other, trot in pairs across the body, gallop and bounds with all four
  in the air); each stands where it was put down until its turn comes, and
  swings to where the body will be over it;
- the body rides on the legs that carry it: at a walk it vaults over them, in
  the springing gaits it gives; hips and shoulders turn and sink with the
  legs, the back flexes and stretches at a gallop, the head stays level, the
  tail follows late;
- a leg is carried at the length the model was made with, so standing it is
  itself; its joint to the body slides under the skin towards the foot (a
  shoulder blade does), which is what gives a short-legged model a stride;
  hips and shoulders come down to a foot that would be out of reach, and no
  leg whips round faster than a leg can;
- sitting, lying, sleeping, crouching, a bow and begging are poses the body
  eases into: an end that goes down has its feet put where they belong
  first, one that comes up after; paws whose end is lifted out of reach hang;
- a leap gathers, takes off, tucks, and lands with the legs giving; over
  somebody it is as long and as high as it takes to clear them, and out of a
  run it lands running. It also turns on the spot stepping round, lifts a
  paw, wags, shakes itself, sniffs, eats, howls, digs and pounces.

Limbs are turned to where they point from frame to frame (`motion.Swings`):
the least turn is anybody's guess when a bone points nearly opposite to
where it came from, and a limb must not spin about itself there.

The film is lit like the chat shows the scene (`LIGHTS` in `scene.py`): a key
light over the camera's left shoulder casts the one shadow, a fill and a rim
light are linked to the figures alone, and every figure shows a part of its
texture unlit (`glow`), because generated textures are painted light already.
Colours go out as they are (view transform Standard).

## Models (`ops/models`)

`models.json` declares both bots. `mag-modeld` owns download, verification
and start; the room only reads `/run/mag-models/status.json` and writes
control verbs. See `AGENTS.md`.

## Deploy (`ops/deploy.sh`)

Build, stage, switch, verify `/readyz` and nginx, roll back on failure. No
drain and no model gating.
