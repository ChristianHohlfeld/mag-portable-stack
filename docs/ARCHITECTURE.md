# MAG architecture

One home server, one user (Chris, phone and desktop), one room, two bots.
This document describes the processes, how a request travels, where state
lives and what happens on a restart. Installation is in
[INSTALL.md](INSTALL.md), day-to-day operation in
[OPERATIONS.md](OPERATIONS.md), the rules for changes in
[`AGENTS.md`](../AGENTS.md).

- [Processes and ports](#processes-and-ports)
- [Request flow](#request-flow)
- [CPU, memory and the MI50](#cpu-memory-and-the-mi50)
- [The room](#the-room-mag-roomnative-c17-one-process)
- [Models](#models-opsmodels)
- [Audit layer](#audit-layer-opsaudit)
- [Bot VMs](#bot-vms)
- [The MI50 queue: 3D, pictures, videos](#the-mi50-queue-opsidol)
- [Scenes](#scenes-opsscene)
- [Dictation](#dictation-opsstt)
- [Reading aloud](#reading-aloud-opstts)
- [Backups](#backups-opsbackup)
- [Gitea and CI](#gitea-and-ci)
- [Deploy](#deploy-opsdeploysh)
- [Reboot and restart behaviour](#reboot-and-restart-behaviour)

## Processes and ports

```
 phone / desktop on the tailnet
   https://mag.stardenburg.de, https://<name>.bot.stardenburg.de
        │
        ▼
 tailscaled  :443 on the Tailscale address (raw TCP forward, PROXY v2)
        │
        ▼
 nginx  127.0.0.1:4443  TLS, SNI: room, bot sites, Gitea
        │  auth_request /_mag_whois ──► magroom /_internal/tailscale/whois
        │                                  └─► tailscaled LocalAPI (whois)
        ▼
 magroom  127.0.0.1:8766 (page, API, event stream)        CPUs 28-55
   │       192.168.122.1:8767 (bridge for the bot VMs)
   │
   ├─► mag-audit-gateway 127.0.0.1:8080 ─┬─► llama-server lena :8191  NUMA0, cores 0-13
   │                                     └─► llama-server noah :8192  NUMA1, cores 14-27
   ├─► /run/mag-models/status.json, /var/lib/mag-models/control/ ◄── mag-modeld
   ├─► mag-idol    127.0.0.1:8793  one queue on the MI50 (3D, pictures, videos)
   ├─► mag-scene   127.0.0.1:8795  Blender on the CPU
   ├─► mag-stt     127.0.0.1:8194  speech to text
   ├─► mag-browser-webrtc 127.0.0.1:18784 (offers), UDP :18783 (VMs)
   ├─► sudo magbot-guest-run / magbot-agent-ctl / magbot-netmode ─► magbot ─► libvirt
   └─► outbound web (search, plugins) through mag-audit-webproxy :8888

 bot VMs magbot-agentN on virbr0 (192.168.122.0/24) reach only 192.168.122.1:
   8767 room bridge · 8091 site publisher · 3001 Git gateway ─► Gitea 127.0.0.1:3000
   8888 audit web proxy · 8889 browser CONNECT · 53 DNS · UDP 18783 live link
```

| Unit | Binary / script | Listens | Runs as | CPUs |
| --- | --- | --- | --- | --- |
| `mag-120.service` | `/opt/mag-stack/current/bin/magroom` | 127.0.0.1:8766, 192.168.122.1:8767 | `magbot` | 28-55 |
| `mag-audit-gateway.service` | `/opt/mag-audit/bin/mag-audit-gateway` | 127.0.0.1:8080 | `mag-audit` | any |
| `mag-audit-webproxy.service` | mitmproxy in `/opt/mag-audit/venv` | :8888 (audited), :8889 (CONNECT only) | `mag-audit` | any |
| `mag-audit-nft.service` | `nft -f /etc/nftables.d/mag-audit.nft` | | root | |
| `mag-modeld.service` | `/usr/local/lib/mag-models/mag-modeld` | | root | 28-55 |
| `mag-model@lena.service` | `/usr/local/lib/mag-models/run.sh lena` | 127.0.0.1:8191 | `chris` | 0-13 |
| `mag-model@noah.service` | `/usr/local/lib/mag-models/run.sh noah` | 127.0.0.1:8192 | `chris` | 14-27 |
| `mag-model-fetch@<bot>-<kind>.service` | `/usr/local/lib/mag-models/fetch.sh` | | `chris` | 28-55 |
| `mag-browser-webrtc.service` | `/opt/mag-stack/current/bin/mag-browser-webrtc` (Go) | 127.0.0.1:18784, UDP :18783 | `magbot` | any |
| `mag-idol.service` | `ops/idol/worker.py` | 127.0.0.1:8793 | `chris` | 28-55 |
| `mag-scene.service` | `ops/scene/worker.py` | 127.0.0.1:8795 | `magbot` | 28-55 |
| `mag-stt.service` | `/opt/mag-stt/bin/mag-stt` | 127.0.0.1:8194 | `magbot` | 28-55 |
| `mag-bots-publish.service` | `/usr/local/lib/mag-bots/mag-bots-publish` | 192.168.122.1:8091 | root | any |
| `mag-agents-network.service` | `ops/firewall.sh` (loads `ops/mag-agents.nft`) | | root | |
| `mag-git-gateway.socket` | `systemd-socket-proxyd` to 127.0.0.1:3000 | 192.168.122.1:3001 | dynamic | |
| `gitea.service` | `/usr/local/bin/gitea` | 127.0.0.1:3000 (HTTP), 127.0.0.1:2222 (SSH) | `git` | |
| `mag-gitea-runner.service` | `/usr/local/bin/gitea-runner` | | `mag-runner` | 42-55 |
| `nginx.service` | nginx | 127.0.0.1:4443 | `www-data` | |
| `tailscaled.service` | tailscaled | :443 on the Tailscale address | root | |
| `mag-cpu-tune.service` | cpupower and sysfs writes | | root | |

Timers: `mag-stack-check.timer` (health, every minute),
`mag-bot-saves.timer` (VM saves, 04:15), the backup timers (see
[Backups](#backups-opsbackup)), `mag-acme-renew.timer` (room certificate,
twice a day), `mag-tailscale-cert.timer` (the `ts.net` certificate),
`mag-gitea-funnel.timer` (re-asserts the Tailscale serve config),
`mag-tailscale-egress.timer` (the nft set for `api.tailscale.com`) and
`mag-agent-drift.timer` (removes half-created bot VMs every 15 minutes).

The phone control daemon `s23ctl` (`devices/s23`, 127.0.0.1:18721) is
Chris's own service. Nothing here installs or starts it; the room offers the
phone tools only while its token file exists.

## Request flow

1. A device on the tailnet opens `https://mag.stardenburg.de/`. DNS points
   at the host's Tailscale address. `tailscaled` owns port 443 there and
   forwards the raw TLS stream with a PROXY v2 header to nginx on
   127.0.0.1:4443. The same socket carries the bot sites and Gitea; nginx
   picks the vhost by SNI.
2. nginx (`ops/private-access/mag-room.nginx.conf`) admits only tailnet
   addresses (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`, loopback) and sends
   every request first to `auth_request /_mag_whois`. That internal
   location asks the room's `/_internal/tailscale/whois` with the client
   address from the PROXY header.
3. The room (`auth.hpp`) asks tailscaled's LocalAPI who owns that address,
   and answers with identity headers plus an HMAC proof. nginx copies them
   onto the proxied request; client-supplied `X-Tailscale-*` headers are
   overwritten. The room accepts an identity only with a valid proof, and
   only if the login is listed in `owners` of
   `/var/lib/magbot/room/access.json` (re-read when the file changes).
   There are no guests. A loopback request without identity is "local": it
   may read and chat, nothing destructive.
4. The room calls the models only through `mag-audit-gateway`
   (127.0.0.1:8080). The gateway picks the server by the bot's slot
   header and writes every request and answer to the audit log. The nft
   table `mag_audit` rejects any other local user that tries to reach
   ports 8191/8192 directly.
5. The room's own outbound web traffic (`web_search` through Brave, remote
   MCP plugins) goes through the audit web proxy on 127.0.0.1:8888, which
   logs it fully. The trust store for that is
   `/etc/mag-audit/ca-bundle.pem`.

`/readyz` is served without the whois step, so the deploy can probe it.
App shell, service worker, manifest and API responses are `no-store`; the
event stream is not buffered.

## CPU, memory and the MI50

The host has two NUMA nodes. Physical cores 0-27 belong to the models;
everything else runs on CPUs 28-55.

| CPUs | What |
| --- | --- |
| 0-13, memory of node 0 | Lena's llama-server (`numactl --physcpubind --membind` in `ops/models/run.sh`) |
| 14-27, memory of node 1 | Noah's llama-server |
| 28-55 | the room, mag-modeld, downloads, mag-idol, mag-scene (Blender), mag-stt, SAM 2 and depth estimation, every bot VM's vCPUs and its QEMU emulator thread (`vcpupin`/`emulatorpin` at spawn), VM saves, builds and deploys |
| 42-55, memory of node 1 | backups (`numactl --physcpubind=42-55 --membind=1`, 15 % CPU quota) and the Gitea Actions runner |

`mag-cpu-tune.service` runs before the models: performance governor,
uncore frequency fixed at its maximum, energy bias 0, C1E disabled.

The AMD MI50 belongs to `mag-idol` alone. No bot model offloads to it: the
llama.cpp runtime is built without a GPU backend
(`ops/models/build-runtime.sh`) and `ops/ci/check.sh` refuses GPU flags in
`models.json`. `mag-scene` has `PrivateDevices=true`. The card's power cap
is 78 W: `ops/idol/power-cap.sh` runs as `ExecStartPre` of `mag-idol` and
only ever lowers the cap to 78 W, never raises it. Every stage that uses
the card holds the flock `/var/lib/mag-idol/gpu.lock`.

## The room (`mag-room/native`, C++17, one process)

| File | Responsibility |
| --- | --- |
| `magroom.cpp` | wiring |
| `util.hpp`, `common.hpp`, `json.hpp` | time, ids, atomic files, subprocess with streamed output and cancel, JSON |
| `http.hpp` | thread-per-connection HTTP/1.1 server, file ranges, libcurl client |
| `auth.hpp` | Tailscale LocalAPI whois for nginx, HMAC proof, owner check, CSRF |
| `room.hpp`, `room_model.hpp` | configuration, event bus, the message store, bots, settings |
| `llm.hpp` | llama-server client through the audit gateway: chat stream, slot actions; what a model wrote made fit for the chat |
| `engine.hpp` | who answers when: turns, append-only prompt log, context budget, PASS, reset and restore of a bot |
| `group_dispatcher.hpp` | pure helpers of the group dialogue (tested by `test_group_dispatcher.cpp`) |
| `tools.hpp` | tool schemas and dispatch |
| `vm.hpp`, `guest_pty.hpp`, `browser_udp.hpp`, `browser_recovery.hpp` | guest exec with live output, file writes, browser controller, terminal, VM tokens |
| `site.hpp` | bot web pages: HTML blocks and `www` files published on the bot's site, shown as live previews |
| `idol.hpp` | MI50 jobs (3D, pictures, videos) as room messages |
| `scene.hpp` | scenes the bots build from 3D models, as room messages |
| `mcp.hpp` | plugins: remote MCP servers, their tools per bot, approvals |
| `backup.hpp` | backup on request: starts it and reports its state |
| `gitea_account.hpp` | the bots' Gitea accounts |
| `push.hpp`, `push_core.hpp` | Web Push (VAPID) |
| `api.hpp` | HTTP routes, event stream, VM bridge, file folder |

Other binaries built from this tree: `mag-modeld`, `mag-audit-gateway`,
`magbot-native` (installed as `/usr/local/bin/magbot`, from `vm.cpp`),
`mag-browser` (controller inside each VM), `mag-bots-publish`
(`ops/bots/publishd.cpp`), `mag-stt`, `mag-browser-webrtc` (Go,
`mag-room/webrtc`) and `s23ctl` (`devices/s23`).

The page is plain JavaScript without a build step (`mag-room/web`):
`core.js` (store and live connection), `messages.js` (timeline),
`composer.js`, `mention.js`, `dictate.js` and `capture.js` (dictation),
`speak.js` (reading aloud), `viewer3d.js` (WebGL2 viewer), `live.js` (VM
browser and terminal), `files.js` (file folder), `panels.js` (header and
the settings sheet). Its few libraries (marked, DOMPurify, highlight.js,
xterm.js, the Inter font) are vendored in `mag-room/web/static`; the page
loads nothing from outside the release.

### State

Everything the room keeps is below `/var/lib/magbot/room` (`STATE`):

| Path | Content |
| --- | --- |
| `messages/<id>.json` | one file per message |
| `files/` | attachments; each belongs to exactly one message |
| `text/<file>.txt` | text read from a document |
| `wire/<bot>.json` | the bot's append-only prompt log |
| `agents.json`, `bots.json` | the bots: name, VM, model, pause, plugins, reset or restore in progress |
| `settings.json` | room settings |
| `skills.json` | the bots' shared notebook of skills |
| `mcp/servers.json`, `mcp/<id>.secret` | plugins and their tokens (0600) |
| `push_subscriptions.json` | Web Push subscriptions |
| `access.json` | owner logins |

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
most three times for one turn), for the bots his message had named. An
answer that was cut off in view stays as it is ("Gestoppt").

Each llama-server has one slot (`--parallel 1`), no host prompt cache
(`--cache-ram 0`) and no context checkpoints. The only slot state ever
written to disk is the evaluated system prefix of the day
(`/var/lib/mag-models/slots/prefix-<hash>-<date>.bin`).

### What a model wrote

The server parses a model's output into text, thoughts and tool calls. The
room does not rely on that going well (`llm.hpp`):

- **Markup.** The control tokens of both families are read for what they
  mean when they arrive raw in the content: Gemma's `<|channel>thought …
  <channel|>` and gpt-oss's `<|channel|>analysis<|message|> … <|end|>` are
  thoughts, a raw tool call and a turn made up for somebody else are dropped,
  every other tag disappears (`Clean`; code blocks and inline code are left
  alone). No tag reaches the chat, a thought, or the bot's own log. The
  thoughts stay with the message (`reasoning`) but the page never shows
  them: they are raw and often English. What the page shows of a bot's text
  (also of older messages, and what it reads aloud) is tidied (`MAG.tidy`):
  an English line next to German ones, a tool call written out as JSON, a
  request for approval in words and a bare `<br>` are left out, and an
  answer of nothing else is not shown.
- **Circles.** A text whose end is the same passage three times over (or the
  same long passage a fourth time) is a model that lost its way: the room
  closes the connection, which ends the generation, and keeps the text up to
  the end of the first round. Inside an open code block and in tool-call
  arguments equal lines are normal, so the bar is far higher there.
  Thoughts in circles are asked once more from the start, then the answer
  ends with one line saying so.
- **Tool calls.** The room ends the generation at the first tag inside
  tool-call arguments; what the model meant stands in front of it and is
  called. Arguments that are no JSON object after that are stored as `{}`
  and the tool is not run; the model is told why. Invalid arguments never
  enter a prompt log: the server refuses every later request whose history
  holds them.
- **Errors.** What an answer ended with is one short line; a server error
  may quote a whole generation and is never shown.

### Conversation rules

The execution contract is in [`AGENTS.md`](../AGENTS.md). In short:

- A human message without an addressee is answered by all active bots.
  `@Lena …` asks Lena alone; the other bot gets no turn and reads the
  message as context. Several mentions or `@alle` mean all of them. A
  mention is stored as the bot's id, so it survives a rename.
- A visible bot message may be answered by the other bot. Bots keep talking
  among themselves; the setting "Bots untereinander"
  (`MAG_ROOM_BOT_ROUNDS`, 0 = unlimited) bounds the rounds, and the room
  rests after `MAG_ROOM_BOT_FUSE` (24) bot turns in a row without a word
  from Chris.
- `PASS` is invisible; a repeated answer or a bare acknowledgement counts as
  PASS. When all bots pass in a row the room rests.
- New human input and Stop preempt bot rounds at once.
- One request per llama-server at a time.
- Tool work outside dialogue continues on its own only after a successful
  tool result with distinct progress and `[[MAG:WEITER]]`.
- A message Chris sends while a bot is working on his earlier request is
  handed to that bot at its next step.
- A paused bot gets no turn and makes no model request; it reads what it
  missed when it is back. A stopped bot's model server is off.

### Tools

The list is fixed and in a stable order, so the prompt prefix stays
cacheable (`tools.hpp`). With a VM: `shell`, `write_file`, `browser_open`,
`browser_look`, `browser_read`, `browser_click`, `browser_type`,
`browser_key`, `browser_login`, `browser_pay`, `browser_wait`,
`browser_scroll`, `browser_back`, `share_file`. For every bot: `web_search`,
`read_image`, `remember`, `consent`, `routine`, `skill`, `backup`,
`models_3d`, `image_generate`, `photo_to_3d`, `video_generate`, `scene_3d`. The phone tools `s23_status`,
`s23_screenshot`, `s23_tap`, `s23_swipe`, `s23_text`, `s23_key` are offered
only while Chris runs the phone's control daemon. Plugin tools follow as
`mcp__<server>__<tool>`.

### Trash button

`POST /api/room/clear` stops all turns, deletes every message and attachment
(and all Idol job directories), resets both prompt logs to the system prefix,
erases the slot of every llama-server (`POST /slots/0?action=erase`) and
restores only the prefix state. No model is reloaded and nothing of the old
conversation can come back; the next answer starts from a warm prefix.

### The chat's files

The folder button in the header opens every file of the chat in one sheet
(`mag-room/web/app/files.js`): uploads, pictures, models, scenes, films,
videos, audio and documents, newest first, grouped by kind, with a preview,
a download and a link back to the message. `GET /api/files` lists them;
`GET /api/files.zip?files=…` streams several as one uncompressed ZIP whose
length is known in advance. The files stay where they are; the folder is
only a view.

Nothing Chris sends is dropped without a word (`composer.js`): a file that
did not arrive (too large, empty, refused by the room) stays above the
message field with the reason and "Erneut senden". A phone's camera app
takes the page out of view and may have it reloaded, which loses the
photo: the page notes that the camera is open (`sessionStorage`), and when
it comes back as a new page, or the camera returns nothing, it says so and
offers the camera again or the camera inside the browser (`getUserMedia`),
which no reload can take away. A HEIC photo is sent as JPEG where the
browser can read it.

### Documents

`Engine::document_text` turns an attached PDF, DOCX, PPTX, ODT or ODP into
text once and keeps it in `STATE/text/<file>.txt` (removed with the message):
`pdftotext -layout` for the text layer; when a PDF has none, `pdftoppm` and
`tesseract` (deu+eng) read its first six pages; office files are unzipped and
their XML reduced to paragraphs. The bots get up to 12,000 characters of each
document inline; the whole text is copied to the bot's VM as `<name>.txt`.

### What the bots read

Every finished message of the others, with what belongs to it: small text
files inline, 3D orders and results, images. A bot with a vision projector
gets the image itself (a 1280 px copy, each image right in front of its own
`[Bild …]` line); once per picture it writes down what it shows, asked on a
branch of its prompt log that is not appended, with the picture again in
front of the question; the description begins with what the picture shows in
one to three words and a colon ("Roter Stuhl: …"). Pictures are told apart
by their content (SHA256): a picture posted twice has one description, and
`POST /api/file/<f>/describe` (owner) has it made anew for every copy. A bot
without eyes waits briefly for the description and gets a renewed one as an
addendum.

`POST /api/upload` follows a JPEG, PNG or WebP to its end; one that ends in
its picture data is answered with 422 `truncated`. The page reads the file
again twice (a phone may still be writing a photo it has just taken) and
then sends it with `force=1`, which stores it marked `cut`. Before the GPU
stage the Idol runner measures how much of such a photo is missing and
refuses it in words, which is what the 3D message then shows.

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

A bot's plugin tools follow the room's own in its tool list (at most 64);
what the owner wrote about a plugin and what the server says about itself
stand in the system prompt. Both are part of the prompt prefix, so they
change only with a binding or a refresh. A call of a tool that is not
read-only holds the bot's turn: its step carries `approval`, the page shows
"Erlauben / Immer erlauben / Ablehnen" (`POST /api/approval/<id>`), a push
goes out, and without an answer it runs out after 15 minutes
(`MAG_MCP_APPROVAL_S`). A binding set to `auto` asks nothing. A server that
works with the owner's own connector token (`auth: connector`) is sent
`X-MCP-Readonly` and never changes anything unasked.

### Skills

`skill` (list, read, save) is the bots' shared notebook in
`STATE/skills.json`: how they do recurring work, the steps, how to check the
result and what needs Chris's approval. A skill grants nothing; credentials
in a skill are refused. Chris reads and removes skills in the settings
(`/api/skills`).

### Approvals and routines (`assist.hpp`)

`consent` (action send, buy, delete, publish or routine; what, to,
amount_eur) is called by a bot right before a consequential action. The
rules are `assist` in the settings (`auto`, `rules` per kind ask/auto/never,
`once_eur`, `day_eur`): never refuses, auto (or the auto mode for ask)
allows at once, a purchase only within both limits (the day's sum comes
from the journal), anything else puts a card on the bot's step
(`approval` with `action`, the same `/api/approval/<id>` as plugin cards,
allow or deny, 15 minutes, a push notification). Every decision is appended
to `STATE/assist/ledger.enc` (who, what, amount, to, when, the decision and
why, whether by the auto mode), one AES-256-GCM sealed line each (random
nonce, tag); the key is `STATE/assist/ledger.key` (0600, made on first use,
in the encrypted backup with the state), read only by the room and never
part of a prompt, a tool result or the page. The settings show the last
twenty (owner only). An answer without tool calls that asks for approval in
words (`Engine::asks_in_words`) is taken back once per turn and the bot is
told by a room note to call `consent`; decisions only come from
`/api/approval` (owner) or the rules. The room cannot intercept a click in a VM's
browser, so for actions there the rule lives in the tool and the prompt;
plugin tools keep their enforced card. Paying goes through `browser_pay`:
`consent` first, then a pay card where Chris picks a saved card and gives its
security code; cards are sealed in `STATE/pay/cards.enc` (`vault.hpp`,
AES-256-GCM, key `STATE/pay/vault.key`, 0600), the security code is never
stored, the page and the bots see brand and last four digits only.

`routine` (add, list, remove, pause, resume, run) keeps routines in
`STATE/routines.json`: a name, a bot, an instruction and when (`time` with
`days` 1-7, `every_minutes` at least 15, `once` as a time, or `event`
picture, job_done or file), with `next` and `last`. Setting one up goes through
`consent` (kind routine). A tick every 30 s fires what is due as a chat
message from `routine` (role user, mentions its bot, `routine` id) and
`Engine::on_human` for that bot only. A run missed while the room was down
runs once if it is at most 12 hours late; an event routine runs at most
every 15 minutes, so one that makes pictures cannot set itself off.
`event: file` (optional `path`, a folder below `/home/bot`, and `pattern`, a
name glob such as `*.pdf`) fires on a new file in the bot's VM: once a
minute, only for a bot with such a routine not paused and only while its VM
answers (`Guests::state` ready; the guest helper never starts a VM), a
read-only Python walk lists the regular files below `/home/bot` (no dot files
or folders, no `node_modules`, no unfinished downloads, at most 15000; it
removes its own previous output log in `~/.mag-shell`). A file is new when it
was not in the last listing, kept in `STATE/assist/vm-files.json` with the
files that wait, so a restart neither fires for old files nor misses new
ones; the first listing of a bot only notes what is there, and a file changed
in the last 20 s waits for the next look. The message names the files
(`/home/bot/…`, ten at most). What appears while the routine's own run goes
on (from its firing until the bot is idle again) is its own and is dropped;
what appears in the 15 minutes after a run waits and fires when they are
over.
`GET /api/routines` lists them with the rules; the owner pauses, resumes,
runs (`POST /api/routines/<id>/<what>`) and removes (`DELETE`) them.

## Models (`ops/models`)

`models.json` declares both bots: the GGUF (path, Hugging Face URL, size,
SHA256), Lena's MTP draft model and vision projector, NUMA node, CPUs,
threads, port, context and sampling arguments, and the pinned llama.cpp
revision (`runtime.rev`), which `build-runtime.sh` builds CPU-only.

`mag-modeld` is the single owner of the model lifecycle:

- a GGUF that is missing or partial is "downloading": the supervisor starts
  `mag-model-fetch@<bot>-<kind>.service` (`wget -c`) only when no other
  process has the file open, and `fetch.sh` takes a lock of its own, so a
  file has exactly one writer;
- a file whose size equals the Hugging Face size gets one SHA256 pass and a
  `<bot>.<kind>.ok` stamp in `/var/lib/mag-models`;
- a verified model is started as `mag-model@<bot>.service` and watched on
  `/health`;
- the state is published in `/run/mag-models/status.json` ("lädt herunter",
  "wird geprüft", "lädt", "bereit"); control verbs (start, stop, restart,
  freeze, thaw) arrive as files in `/var/lib/mag-models/control/`, written
  by the room or by `mag-modeld ctl`.

A model file is never deleted, truncated or rewritten. Lena runs her vision
projector on the CPU (`--no-mmproj-offload`).

## Audit layer (`ops/audit`)

- `mag-audit-gateway` (C++, `mag_audit_gateway.cpp`) is the only path from
  the room to the model servers. It logs every request and answer below
  `/var/log/mag-audit` with hash chains per day; `mag-audit-tail` shows and
  verifies them.
- `mag-audit-webproxy` (mitmproxy with `mitm_audit_addon.py`) logs every
  web access of the room and of the VMs' shells, tools, `apt`, `pip` and
  `git` on port 8888, bodies included (large ones content-addressed under
  `/var/log/mag-audit/blobs`). Port 8889 lets the VM browsers through as
  CONNECT without TLS interception; only host and SNI are logged.
- `mag-audit.nft` (`mag-audit-nft.service`) enforces it: only root and
  `mag-audit` reach the model ports; the room user `magbot` and the VM
  bridge reach the web only through the proxy. `mag-tailscale-egress`
  keeps a narrow set for `api.tailscale.com`.
- `vm-proxy-setup.sh` runs inside each VM (through the guest agent) and
  points its shell tools and Chrome at the proxy with its CA.

## Bot VMs

Each bot has its own KVM guest, bound to the bot's id. The VM name is
`agentN`, the libvirt domain `magbot-agentN`; the binding never changes with
a rename.

### Image and disks

`ops/vm/build-golden.sh` builds a reproducible base image from a pinned
Ubuntu 24.04 cloud image (serial and SHA256 in the script), customised once
in a throwaway QEMU boot: guest agent, cloud-init, Xvfb, Google Chrome
stable, the German locale, the audit CA tooling. It is written atomically
to `/var/lib/libvirt/images/magbot/base/golden.qcow2` (read-only,
`root:kvm 0440`) with a manifest of what it contains. Chrome and Ubuntu
updates are not pinnable; their versions are in the manifest.

`magbot spawn` (`vm.cpp`) checks the host's admission limits
(`/etc/magbot/config.json`, defaults in `vm.cpp`: 2 vCPUs, 16 GiB RAM,
20 GiB disk per bot), creates a qcow2 overlay
`bots/<id>/root.qcow2` on the golden image and a cloud-init seed, defines
the domain with `virt-install`, pins its vCPUs and emulator thread to
CPUs 28-55, throttles its disk I/O, sets it to autostart, gives it a Gitea
account, prepares its browser sandbox and points it at the audit proxy.
`mag-agent-drift.timer` removes half-created VMs that no bot refers to.

### Isolation

- The room reaches a VM only through three narrow sudo wrappers
  (`/usr/local/sbin/magbot-guest-run`, `magbot-agent-ctl`, `magbot-netmode`;
  `ops/magbot-guest.sudoers`). Commands run in the guest through the QEMU
  guest agent as user `bot`. `magbot exec --live` relays output while a
  command runs.
- A VM reaches the host only on the bridge address 192.168.122.1, and there
  only DHCP, DNS, 8767 (room bridge, with a token derived per VM, so it
  cannot act as the other bot), 8091 (site publisher), 3001 (Git), 8888 and
  8889 (audit proxies) and UDP 18783 (live link). Forwarding into private,
  tailnet and link-local ranges is rejected. These rules are the table
  `inet mag_agents` in `ops/mag-agents.nft`, loaded by `ops/firewall.sh`
  from `mag-agents-network.service` on every deploy and boot.
- "Internet für Bots" off is `magbot-netmode offline`: a host nft table that
  leaves a VM only DHCP, the room bridge, the publisher and the live link.

### Browser and terminal

`mag-browser` in the VM captures its Chrome as JPEG frames (while someone
is subscribed, about five a second), takes pointer, wheel, key and text
input, and holds one Bash PTY as user `bot` that outlives every viewer. It
talks to `mag-browser-webrtc` on the host over UDP, authenticated with a key
per VM. The gateway hands that to the page as WebRTC data channels:
`frames` (unreliable, a late frame is dropped), `control` (input) and
`console` (the PTY in both directions, with a heartbeat every 2 s). The room
only passes the SDP offer on (`POST /api/browser-live/offer`). The page shows
the frames on a canvas and runs xterm.js on the PTY (`mag-room/web/app/live.js`).
Where UDP cannot get through, the browser view falls back to pictures over
HTTPS (`/api/screen`) and the terminal to single commands
(`/api/vm/<bot>/run`); the file view (`/api/vm/<bot>/files`) always goes
through the wrappers.

The bot's own browser tools go to the same controller over HTTP (port 9225,
a bearer token per VM; protocol `MGB6`, a controller of an older protocol is
replaced at the next call). `/elements` numbers every visible element one
can act on (links, buttons, fields, menus; covered ones left out) with a
label and the point at its middle and keeps them in the isolated world;
`/click` takes such a number, a point of the 1440x900 window, a selector or
a visible text (exact, then contained); `/type` clicks a numbered field and
replaces its text; `/key` sends real key codes (Enter submits); `/wait`
waits for the load and a text. After each action the tool returns the page
as `browser_look` sees it, and a half-size JPEG of the window goes to
`STATE/shots/<message>-<step>.jpg` (`GET /api/shot/…`; the step has
`shot`), deleted with its message. A password field's value is never read.

`browser_login` first goes to the login form itself (`tools::login_form`:
a page without a user or password field gets its sign-in link or button
clicked, at most three times; a two-step page that first wants the way
the code comes gets its first such button) and then waits like a consent card
(`action: login`, the site from the page's address, reason, `fields` the
form asks for now, `saved` logins of the site). Every login typed into the
card is kept at once (whether the page then takes it or not; a code of a
two-step page is typed into its field with Enter and never kept) in
`STATE/pay/logins.enc`, sealed like the cards, per site and account; the
next card of that site offers it ("Gespeicherten Login verwenden",
`{"saved": id}`), and the settings ("Logins", `/api/vault/logins`) list,
change, remove and, on a tap, show one. The card's step has no input or
output in the chat, only the card; the activity row says what it waits
for (login, payment, approval).
Chris either posts his login to `POST /api/login/<card>` (owner only): the
room finds the user and password fields by `/elements`, types them with
`/type` (a site that asks for the user on a page of its own gets Enter and
is looked at again), presses Enter and checks that no password field is
left; nothing of it is logged, and the strings are wiped after.
Or he opens the bot's live browser from the card, logs in himself (passkey,
SMS code, captcha) and taps "Fertig, weiter" (`decision: done`). Then the
tool returns the page to the bot. Cookies stay in the VM's Chrome profile.

`browser_pay` (shop, amount_eur, what, the card number field as n or x/y,
the order of the further fields) first asks `consent` with kind buy; only
when that allows, a pay card waits (`action: pay`). Chris picks a saved card
and gives its security code: `POST /api/pay/fill/<card>` (owner only) opens
the card from the vault (`vault.hpp`: `STATE/pay/cards.enc`, one sealed
JSON, key `STATE/pay/vault.key`; Luhn-checked number, MM/YY, name; never
the code) and types it into the page: fields of the page by their names
(`/elements`), else the field the bot named is clicked, the number typed,
then Tab and each further field. Or he pays himself in the bot's browser
from the card. Cards are managed in the settings ("Zahlung",
`/api/pay/cards`); the page gets brand, last four digits, expiry and name.
`/elements` lists frames too (`kind: frame`), so a provider's card frame can
be named.

### Goal driver

`Engine::lead_` is the bot that carries out Chris's current request: the
first to call an acting tool (shell, files, browser, media, phone, plugins)
after his message. Another bot's acting tool gets "<lead> erledigt
diese Aufgabe schon", nothing runs, its turn ends there and its message is
marked `yielded` and not shown. A turn of Chris's (or a routine's)
that used tools and ends after a failed step, without text or with an
announcement of what the bot will do next (`announces`) gets a room note
with the goal (Chris's last request) and is continued, three times a turn
at most. A turn that ends while its browser shows a login, password or
code page (the last browser result has a password field or such a title)
is told to call `browser_login` instead of asking in words, and so is an
answer without tools that asks Chris for a login, password or code.
`consent` and `routine` do not make a bot the lead. A bot whose
running turn for Chris already acted stays the lead when Chris writes
again. A consent, login or pay card that ended without a yes, and a
refused acting tool, are blockers and end the turn as it is. After a
failed browser action the tool result already holds the page as it is now.

### Bot sites

A bot's site is `<name>.bot.stardenburg.de`, tailnet only. nginx
(`ops/bots/mag-bots.nginx.in`) hands every host of that zone to the room;
the room resolves the name (the current one serves, an earlier name, the bot
id or the VM name redirect) and sends the files the bot's VM published to
`/var/lib/mag-bots/www/<vm>` through `mag-bots-publish`. A finished HTML
block of an answer, or a file written below `/home/bot/www`, is published
and framed from that site in the chat. The room page's policy allows frames
from that zone and nothing else. The zone has its own wildcard certificate
(`ops/bots/issue-wildcard.sh`).

### Reset

`POST /api/vm/<bot>/reset` (`Engine::reset_vm`, the sheet's "Zurücksetzen")
puts a bot back to its first day. The steps, each of which can be done
again: `stop` (the turn is cancelled), `memory` (memories, prompt log, slot),
`vm` (`magbot-agent-ctl reset agentN`). The state is the `reset` object of
the bot's record; a room that starts with a running reset goes through it
again from the top. While it runs the bot gets no turn. Name, address,
model and a pause stay as they were.

`magbot reset` holds the lifecycle lock, asks the host's admission first,
tears down whatever is there without archiving, and spawns under the same
name with the same size. `site.sh reset` deletes what the bot published and
gives it its first page again. The VM's Gitea account and repositories
stay; its credential is issued anew. A reset leaves the VM's saves alone.

### Saves and restore

A save is the bot's overlay (everything it changed against the golden
image: files, programs, browser profile, its site) at one moment, written
compressed with `qemu-img convert -c` to
`/var/lib/libvirt/images/magbot/saves/<vm>/<stamp>-<kind>/root.qcow2` with
a `META.json`. A running VM keeps running: an external disk-only snapshot
(file systems frozen through the guest agent where it answers) makes the
overlay read-only for the copy, and a shallow block commit takes what was
written meanwhile back into it. A save that was cut off is settled at the
next save or restore. The newest four saves per VM are kept
(`keep_saves`); there must be at least the overlay's size plus 30 GiB free.

- `mag-bot-saves.timer` runs `mag-bot-saves@daily.service`
  (`magbot save-all daily`) every night at 04:15 Europe/Berlin.
- The bot's sheet ("Sicherungen der VM") lists the saves
  (`GET /api/vm/<bot>/saves`), makes one now (`POST /api/vm/<bot>/save`,
  kind `manual`) and restores one (`POST /api/vm/<bot>/restore`).
- A restore shuts the VM down, keeps its current disk as a save of kind
  `vor-wiederherstellung` (a rename, so a restore can be undone), puts the
  chosen save in place, starts the VM and renews its Git credential,
  browser sandbox and proxy setup. The bot's turn ends and it gets no turn
  until the VM is back; what it remembers of the chat stays. The restore is
  in the bot's record with a token; after a restart of the room it goes on,
  and `magbot` knows by the token whether it already went through.
- Saves and restores run as transient units (`magbot-save-agentN`,
  `magbot-restore-agentN`) on CPUs 28-55, so a restart of the room does not
  cut them off. One save or restore per bot at a time
  (`/run/lock/magbot-<vm>.lock`).

## The MI50 queue (`ops/idol`)

`worker.py` keeps one job at a time on the MI50: it is the one queue of
every job that needs the card (3D, pictures from words, videos). A stage
that uses the card also holds `/var/lib/mag-idol/gpu.lock` for as long as
its process runs; a stage that finds it taken waits and says "Wartet auf die
Grafikkarte". A job is a folder below `/var/lib/mag-idol/jobs`:
`status.json` (state, phase, the running stage's process, its place
`order`) and the result of every finished stage. A stage runs in its own
session with its output in a log and its exit code in a file; the unit has
`KillMode=process`.

Resumability:

- a restart of the worker (a deploy) leaves the running stage alone and the
  new worker follows it again;
- after a reboot a 3D job goes on at its first missing stage (shape, splat,
  mesh), a picture starts its one stage again, and a video goes on after its
  last finished step (`state.pt`);
- queued jobs keep their place: the job whose stage still runs comes first,
  then the line as it was; up to 32 wait;
- no stage has a time limit; a stage that stands still for ten minutes (no
  log output, no CPU) is started again, and after three standstills the job
  fails in words;
- a job that failed or was cancelled keeps its folder; "Noch einmal"
  (`POST /api/3d/<id>/retry`) queues it again at its first missing stage.
  Chris moves a waiting job with "Nach vorn"/"Nach hinten"
  (`POST /api/3d/<id>/move`) and cancels it at its message.

The room mirrors each job into its message (phase k of n, the sampler's
step) and picks jobs up again after its own restart.

### Photo to 3D

A photo ordered for 3D is one job of the photo as it is; nothing is split
or looked at unasked. The shape stage comes first: TripoSG (`shape.py`, on
the MI50, in its own environment `~/idol-mi50/triposg/venv` that reuses
Idol's ROCm torch) makes the whole, closed shape from the cut-out picture.
Where that shape covers its picture, the model is made from the shape and
the photo alone (`mesh.py --alone`); no splat is sampled. Only where that
does not do, Idol samples a splat and the mesh is made from splat and shape.

The mesh stage (`mesh.py`, CPU) produces closed solids: marching cubes over
an opacity field, smoothed, decimated to a printable STL and a lighter GLB,
unwrapped (`unwrap.py`: xatlas with a time limit, else a simple chart
layout), textured. Before it, the paint stage (`paint.py`, MV-Adapter's
image-and-geometry pipeline on SDXL, in TripoSG's environment, on the MI50)
draws the shape's position and normal maps from MV-Adapter's six orthographic
views on the CPU (nvdiffrast is CUDA's alone) and paints the six views from
the photo with MV-Adapter's own settings (50 steps, guidance 3, ShiftSNR 8,
768 px, seed 42), each view then twice as large by Real-ESRGAN x2plus as its
texture script does; `mesh.py` lays them onto the model, each texel taking the
views that see it straight. Code at a fixed commit (`~/idol-mi50/mvadapter/src`),
weights by `ops/idol/paint/fetch.sh` (SDXL base 1.0, its fp16 VAE, the ig2mv
adapter; checked against their SHA256). Without them the side the photo
shows wears the photo itself (up to 4096 px) and what the photo did not see
takes its colours carried over the surface from the nearest seen part. The shape is TripoSG's as its own
scripts make it (the picture prepared as `scripts/image_process.py` does: a
hard mask on white, 10 % margin, at most 2000 px; 50 steps, guidance 7,
seed 42); nothing is cut off or added to it. "Netz neu" makes a model anew
from what the job has.

"Objekt wählen": SAM 2 (`objects.py`, `facebook/sam2.1-hiera-small`) on the
CPU finds the object Chris taps, every object by itself, or a named one
(found by a bot that sees, on a branch off its prompt log). The page shows
the mask and orders the cut-out like any photo.

"Ganze Szene": `objects.py --scene` also estimates depth (Depth Anything V2,
small indoor metric model) and gives every object where it stood, how tall
it was and the room around it. Each object is ordered as its own job; when
the last one is through, the room assembles them into one scene, each actor
named after the description a seeing bot gave its cut-out ("Roter Stuhl: …"
begins every description). A cut-out nobody has described yet is looked at
first; after eight minutes the room stands anyway, with "Objekt N" for what
is still unnamed.

### Pictures from words (`ops/idol/imagegen.py`)

"Bild erzeugen" in the plus menu, or a bot's `image_generate`, orders a
picture: FLUX.2 klein 4B (four steps, fp16 on the MI50). It is a job of the
same queue (`kind: image`, result `image.png`); the room's message becomes
an ordinary image of the chat. The weights come from `ops/imagegen/fetch.sh`.

### Videos with their sound (`ops/idol/h3gen.py`, HyperFlow H3 Flash)

Every picture of the chat has "3D" and "Video" below it
(`POST /api/image/act`), and "Video erzeugen" in the plus menu
(`/api/message` with `mode: video`) takes a picture from the gallery or the
camera, or words alone (`seconds` 5, the default, or 10, `use` first or ref; a bot's
`video_generate`). Every video is HyperFlow H3 Flash by Video Rebirth, a 14B few-step
MiniMax H3 that makes the video and its stereo sound together, from words
alone, from a picture as its first frame (fl2va) or with the picture as who
or what is in it (ref2va, "<Picture 1>"). Its setup is Video Rebirth's own
workflow (its HyperFlow sigmas, Euler, CFG 1, 24 fps, 124 or 243 frames),
made for minutes on the MI50 instead of about an hour: Video Rebirth's
4-step schedule instead of six steps, and 480p (864x480 without a picture,
else that area in the picture's shape) instead of one megapixel, a third of
the tokens and a ninth of the attention. Its NVIDIA
parts (INT8 convrot, NVFP4, SageAttention) do not exist on gfx906, so the
same model runs as GGUF Q8_0 with the MiniMax H3 Qwen3-VL-32B encoder as
GGUF Q4_K_M and H3's video (fp16) and audio (fp32) VAEs, in
stable-diffusion.cpp with Video Rebirth's patch on one native path, ggml's
HIP backend for gfx906 (ROCm 6.3.4, unpacked by `ops/video/h3/rocm.sh` into
the owner's directory and never installed into the system; `build.sh` builds
`sd-cli-hip` against it): int8 dp4a matrix kernels (MMQ) for the Q8_0
weights, stable-diffusion.cpp's fused RMSNorm(q/k)+RoPE, and our own kernels
(`ops/video/h3/gfx906-kernels.patch` and `gfx906-ggml.patch`, each checked against exact math on the
CPU by `ops/video/h3/check.sh`): the attention for gfx906 (`--sage-attn`: K
less its token mean, Q in int8 per row and K per block of 64, Q*K^T by
v_dot4, online softmax in fp32, P*V by v_dot2_f32_f16), RMSNorm, its
weight and the AdaLN modulation in one pass, and the VAE's Conv3d as gfx906's
own implicit fp16 GEMM in ggml (`gfx906-ggml.patch`: the patches as pairs
along k, v_dot2_f32_f16 instead of conversions and scalar FMAs); parts that wait stay in memory
(`--offload-to-cpu`), the VAE decodes in tiles; ffmpeg writes H.264 with AAC.
A clip is made by `mag-h3d` (stable-diffusion.cpp's server from the same
build, `127.0.0.1:8796`, cores 28-55, its own 64 GB limit): it keeps H3 loaded
between clips (`models/load` with the recipe `minimax-h3-fl2va` or
`-ref2va`; the weights in RAM, on the card only while a clip is made inside
Idol's video stage, which holds the MI50's lock), and every other GPU stage,
once the card is its own, has it unload the model, so 3D and pictures have
the whole card. Where h3d does not answer, sd-cli makes the clip. The h3d job
id is kept in the job folder, so a restarted stage follows the same clip.
The model's result (`h3.webm`) is kept, so a lost stage only writes the
file again. Its licence (MiniMax H3 Community License) leaves out the EU;
MiniMax authorised Chris personally on 2026-11-13 (private, non-commercial,
nothing passed on, output marked as made by AI, no training of other
models, only him). So an installer never fetches it: the owner runs
`ops/video/h3/fetch.sh` (about 54 GB, pinned, checked) and
`ops/video/h3/build.sh` on his host; the worker runs it only when both are
there (`video` in its health) and refuses a video in words, and every video says in the chat and in the
file's metadata that it is made by AI with MiniMax H3. Wan 2.2 made the
videos before (its messages still say so); its weights and environment stay
on disk, unused.

## Scenes (`ops/scene`)

The bots' tools `models_3d` and `scene_3d` turn 3D models from the chat into
animated scenes. A scene is a description (who plays, what each one does,
in order), never code; no code of a bot or a request runs in Blender.
`mag-scene` (`worker.py`, user `magbot`, CPUs 28-55, no device access) runs
the pinned Blender (`blender.lock`) headless with `scene.py`:

- `parts` / `rig.py`: a generated model may show several figures; each is
  read. Two legs make a figure that walks upright, three or four an animal;
  a long body without legs, a bird, an insect or spider and a vehicle (by
  its wheels) are recognised too; anything else is a thing that moves as a
  whole.
- `build` (while the tool call waits, so a wrong description comes back in
  words): skeletons, weights and keyframes; writes `scene.glb` (posted at
  once and played in the chat) and `scene.blend`.
- `render` (only when the owner asks for the film): Cycles on the CPU frame
  by frame, then ffmpeg. A restart of the worker follows the running render
  again; after a reboot it goes on with the first missing frame. The film
  joins the scene's message.

Figures on two legs move with recorded motions (`motion.py`,
`motions.npz`, built by `mocap/build.py` from CMU motion capture, see
`MOTIONS.md`): about 150 motions found by description, joined at the
nearest pose, feet that stand stay where they stand, a free `pose` uses IK
on the figure's own arm. A fight (kick, punch, attack) knocks the target
away in an arc. Animals move by a worked-out gait (`animal.py`). Parts of a
thing (a lid, a door, a turret, a drawer) are found on the mesh alone: a
seeded watershed over the faces, from the middle of the side against the
thing's inner half, whose border lies on the folds of the surface (grooves,
steps, inner creases); without such a border the side is cut by a plane at
the seam of its cross-section. A part turns about a hinge on its edge,
slides out (a drawer) or spins about the middle of its edge (a turret). A figure that speaks or shows a feeling gets a face (`face.py`:
MediaPipe's face landmarker, pinned in `ops/scene/face`, else the head's
proportions). Its lips follow the sounds of what is said, not the letters:
`phonemes.py` (pure Python, no packages, in Blender's Python) turns German
text into phonemes by rules (long and short vowels, silent h, sch, ich/ach,
ng/nk, pf, qu, z, st/sp at the start, the weak e and -er, the diphthongs),
says numbers as words like the voice does, and takes clearly English text
(the page's own test) through a lighter set of English rules; each phoneme
is a mouth shape (open, mid, spread, rounded, lips shut, lip on teeth,
sibilant, tongue, velar, r, schwa, pause) that lasts as long as the sound,
a stressed long vowel longer, and the whole is stretched to the length the
scene gives. The film's voices are made on the device and sent with the
film request. Each voice's tempo is fitted to how long its mouth moves
(0.8x-1.3x), so lips and voice start and end together; the scene's speaker
button plays the same voices in the chat, following the viewer's clock. The film is lit with key, fill and rim light like the scene in
the chat.

## Dictation (`ops/stt`)

The page captures the microphone in an AudioWorklet (`mag-room/web/app/capture.js`:
16 kHz mono PCM, 100 ms frames) and `mag-room/web/app/dictate.js` posts what has been
spoken since the last request to `POST /api/stt/live?sid=&seq=`; `end=1`
closes the dictation. The room only checks the caller and passes the chunk
on to `mag-stt` (`mag-room/native/mag_stt.cpp`).

`mag-stt` keeps one recognizer loaded (transcribe.cpp, primeLine's German
Parakeet TDT 0.6B as Q8 GGUF, 16 threads on CPUs 28-55). Live text comes
from decoding the open part of the recording again whenever at least 200 ms
of new audio are there; a sentence followed by a pause is committed. Nothing
is stored and the journal gets numbers only. `POST /api/stt` takes a
finished recording. `ops/stt/bootstrap.sh` builds the pinned recognizer and
fetches the model once; `ops/stt/install.sh` is the deploy layer. CI builds
`mag-stt` without the recognizer and tests its protocol on a fake engine.

## Reading aloud (`ops/tts`)

Speech is made in the browser, never on the server. `mag-room/web/app/speak.js`
drives a Web Worker (`ops/tts/tts-worker.js`) that runs Supertonic 3 in
onnxruntime-web, on WebGPU where the device has one, else in WASM threads
(the page is cross-origin isolated for that). On the GPU what one network
hands to the next stays there (the text for the steps, the sound from step
to step to the vocoder): only the length of the speech and the finished
sound are read back, two waits for the GPU per part instead of one per
step. One voice and tempo per
speaker, kept on the device. The page fetches and prepares the model in the
background after load, so the first "Vorlesen" speaks at once; until then
the device's own voice reads. The newest answer is synthesised ahead while
it arrives. The last four answers made whole are kept in Cache Storage as
16-bit sound (`mag-tts-said`): after a reload a tap on one of them plays
the model's voice at once, while the model is still being made ready. Before a sentence goes to the model it is written as it is
spoken (numbers, dates, units, abbreviations).

`ops/tts/bootstrap.sh` fetches and packs the model once (weights moved to
float16 by `pack.py`, about 200 MB); `source.lock`, `models.lock` and
`assets.lock` pin every file by SHA256. `ops/tts/install.sh` puts model,
runtime and worker under `/opt/mag-tts/` in folders named by their content
and writes `config.json`; the room serves them under `/tts/` as immutable
files. The worker keeps everything in Cache Storage, so a browser downloads
the model once.

## Backups (`ops/backup`)

`backup.sh <kind>` (kinds `daily`, `deploy`, `manual`, `clear`) copies:

- the room state `/var/lib/magbot/room` (copied until it is consistent);
- Gitea: an online SQLite backup of `gitea.db`, the repositories and
  `/etc/gitea`;
- configuration: `/etc/nginx`, `/etc/mag-nginx`, `/etc/mag-acme`,
  `/etc/magbot`, `/etc/default/mag-room`, `/etc/mag-stack`, `/etc/mag-audit`,
  `/var/lib/mag-audit`, the MAG and Gitea units, cron, and
  `/var/lib/tailscale`;

and writes each run as an independent restic repository
`/var/backups/mag-full/<stamp>-<kind>/repo`, then checks it, restores it to
a scratch folder and compares a SHA256 manifest. Only then is it marked
`VERIFIED` and older archives rotated: the last three verified archives are
kept, plus the pre-deploy archive of the current release. Model files,
GGUFs and VM disks are never in a backup (`*.gguf`, `ggml-*.bin` and
`*.qcow2` are excluded and their presence fails the run). Bot VMs are
covered by their own saves (see above). The archives are local and
unencrypted by password (`--insecure-no-password`); there is no offsite
copy.

| Unit | When | What |
| --- | --- | --- |
| `mag-backup-daily.timer` | 03:30 daily | `backup.sh daily` |
| `mag-backup-deploy.service` | before every deploy | `backup.sh deploy` |
| `mag-backup-chat.service` | when Chris asks | `backup.sh manual` |
| `mag-backup-weekly.timer` | Monday 04:30 | `restic check` of every archive |
| `mag-backup-monthly.timer` | 1st, 05:15 | `restic check --read-data-subset=10%` |
| `mag-backup-restore-test.timer` | 1st, 06:30 | full restore to scratch, manifest check |
| `mag-backup-monitor.timer` | hourly | unhealthy when the last success is older than 26 h |
| `mag-backup-clear.timer` | 30 s after it is (re)started | `backup.sh clear`; nothing in the room starts it today |

The state is `/var/lib/mag-backup/status.json`. Backup jobs run on CPUs
42-55 with NUMA node 1 memory, nice 19, idle I/O and a 15 % CPU quota.

**Backup on request.** "Jetzt alles sichern" in the settings, or a bot's
`backup` tool (`start`, `status`), calls `POST /api/backup`. The room runs
`magbot-agent-ctl backup`, which starts `mag-backup-chat.service` and
`mag-bot-saves@chat.service` (a save of every bot VM, kind `chat`) in the
background. `GET /api/backup` reports whether one is running, the last
success, health, the last error and every bot's saves.

## Gitea and CI

- Gitea 28.1.0 (`ops/gitea/install.sh`, pinned binary) listens on
  127.0.0.1:3000 with SQLite in `/var/lib/gitea`. It is reachable on the
  tailnet and publicly through Tailscale Funnel on the host's `ts.net`
  name; both go through the shared nginx socket
  (`ops/gitea-ci/public.nginx.in`, rate limits on login and API).
  `ops/tailscale/apply-gitea-funnel.sh` sets the serve config (port 443 as
  raw TCP with PROXY v2 to nginx) and `mag-gitea-funnel.timer` re-asserts it.
- `ops/gitea/mirror-github.sh` keeps a pull mirror of the GitHub repository.
- Every bot VM has a Gitea account `magbot-agentN`, restricted, never admin,
  with a private org `bots-<user>` (`ops/gitea/bot-rights.sh`,
  `ops/gitea-ci/bot-auth.sh`). VMs reach Gitea at 192.168.122.1:3001
  (`mag-git-gateway.socket`).
- CI: `.github/workflows/ci.yml` runs on GitHub for every pull request and
  push to `main`; `.gitea/workflows/ci.yml` runs the same steps on the host
  runner `mag-gitea-runner.service` (user `mag-runner`, CPUs 42-55, 6 GB,
  no access to room state, credentials, models or backups). The steps are
  listed in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Deploy (`ops/deploy.sh`)

Build, snapshot, stage, switch, verify, roll back on failure. No drain and
no model gating:

1. Build the release targets with Ninja on CPUs 28-55
   (`ops/build-targets.sh`), and the Go gateway when its sources changed.
2. nginx preflight; the pre-deploy backup (`mag-backup-deploy.service`);
   a rollback copy of units, binaries and the room state in
   `/var/backups/mag-stack/<release>`.
3. Stage `/opt/mag-stack/releases/<release>` (binaries, `web/`, `ops/`,
   `SHA256SUMS`, `SOURCE_COMMIT`).
4. Stage nginx, switch `/opt/mag-stack/current`, install `magbot`, the sudo
   wrappers and units, reload the VM firewall, restart the audit gateway
   (only when it changed), the room and the WebRTC gateway, wait for
   `/readyz`, activate and smoke-test nginx. Any failure up to here restores
   the previous release.
5. Independent layers, each allowed to fail with a warning: the model layer
   (`ops/models/install.sh`, never restarts a running model), `ops/stt`,
   `ops/tts`, `ops/scene`, `ops/idol` (when its environment exists),
   `ops/bots`, the health and VM-save timers, `ops/boot/ensure-boot.sh` and
   release pruning (the new release and one fallback are kept).

A deploy never waits for a model or a running answer and never fails
because a bot is downloading, loading, paused or stopped. A turn it cuts off
is handled as described under [Prompt log](#prompt-log-and-kv-cache).

## Reboot and restart behaviour

`ops/boot/ensure-boot.sh` runs after every deploy and enables for boot
every unit that is running: the room and its gateways, the audit layer,
mag-modeld, mag-idol, mag-scene, mag-stt, mag-bots-publish,
mag-cpu-tune, Gitea and its runner and funnel, nginx, tailscaled, libvirtd,
ssh and all timers. Model units are not enabled: mag-modeld starts each
verified model itself after boot. `needrestart` is told to leave the MAG
services alone, so package updates never restart them.

Bot VMs are set to autostart at spawn; `ensure-boot.sh` keeps autostart on
for the VMs of the room's bots and for the libvirt networks.

What resumes:

| After | What happens |
| --- | --- |
| room restart (deploy) | turns cut off before anything was visible run again; resets and VM restores in progress go on; MI50 jobs and scene renders are picked up again and mirrored into their messages; a whole-room scene whose objects are done is assembled |
| Idol worker restart | running stages keep running and are followed again; the queue keeps its order |
| scene worker restart | a running film render keeps running and is followed again |
| reboot | mag-modeld verifies stamps and starts the models; 3D jobs continue at their first missing stage, videos after their last finished step, pictures start again; films go on at the first missing frame; the queue order is restored from each job's `order`; the MI50 cap is clamped to 78 W before the worker starts; VMs boot; missed timers (`Persistent=true`) run |
| a VM save cut off | the next save or restore of that VM commits the side overlay back first |

Nothing writes a 3D job off as "interrupted".
