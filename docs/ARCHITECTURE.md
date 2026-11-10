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

## Models (`ops/models`)

`models.json` declares both bots. `mag-modeld` owns download, verification
and start; the room only reads `/run/mag-models/status.json` and writes
control verbs. See `AGENTS.md`.

## Deploy (`ops/deploy.sh`)

Build, stage, switch, verify `/readyz` and nginx, roll back on failure. No
drain and no model gating.
