# MAG stack

One home server, one user (Chris, phone and desktop), one room, two bots.
This repository is the only source of the room, the model layer, the VM
helpers and the deployment. Architecture: `docs/ARCHITECTURE.md`.

The user explicitly chose C++ and removed Docker and the old
Python/ComfyUI/Open-WebUI/Ollama application stack. Do not reintroduce them.
Python is used for tests, one-off scripts and the Idol worker (ROCm/PyTorch).

Build with CMake/Ninja. Run `python3 mag-room/tests/test_native.py` after
changes to HTTP, scheduling, persistence or room routing, and
`python3 ops/models/tests/test_modeld.py` after changes to the model
supervisor. Keep frontend JavaScript plain (no build step) and serve every
dependency locally. Builds and jobs run on CPUs 28-55 only
(`taskset -c 28-55`); the physical cores 0-27 belong to the models.

Production is `mag-120.service` -> `/opt/mag-stack/current/bin/magroom`.
Deploy versioned releases through `ops/deploy.sh`; `ops/rollback.sh` reverts
executables. A deploy never drains, never waits for a model and never fails
because a bot is downloading, loading, paused or stopped. Runtime state, VM
overlays, models and credentials live outside Git. Preserve existing room
data and immutable backing disks through deployments. nginx stays the ingress.

S23, SSH, Tailscale, Gitea, Codex and Cursor are independent working services.
Use the actual current inventory, not old session statements, when changing
operations. Never put secrets, tokens or raw transcripts into a commit or tool
output; read only the configuration needed for a specific operation.

## Models

Two CPU-only llama.cpp servers, declared in `ops/models/models.json`:
Lena = Gemma 4 26B-A4B on NUMA0 (physical cores 0-13, :8191), Noah = gpt-oss
120B on NUMA1 (cores 14-27, :8192). Lena also loads the Gemma vision projector
(`mmproj`, on the CPU) so she sees images. No GPU offload: the MI50 belongs to MAG
Idol alone and its power cap stays at 72 W (`ops/idol/power-cap.sh` only ever
lowers it; never add anything that raises it). `mag-modeld` is the single
owner of the model lifecycle: it checks each GGUF against the Hugging Face
size, verifies its SHA256 once, starts `mag-model@<bot>.service` and publishes
`/run/mag-models/status.json`. Do not start, stop or enable model units by
hand or from a deploy; use `mag-modeld ctl <bot> start|stop|restart|freeze|thaw`.
A model file has exactly one writer: never start a second download on a file
another process has open. Never delete a model file, including the retired
Qwen, DeepSeek, Ornith and Flint weights.

Each server has one slot, no host prompt cache and no context checkpoints.
The room keeps an append-only prompt log per bot so the server only evaluates
new tokens; never rewrite what was already sent. The trash button erases the
slot of every server (`/slots/0?action=erase`) and restores only the saved
system-prefix state; no conversation is ever saved to or restored from disk.

## Room model

Everything in the timeline is one message type (text, bot work with its
steps, files, 3D results), stored one JSON file per message. A 3D result is a
normal message; deleting it also deletes its files and its Idol job directory.
The bots build scenes from the 3D models in the chat (`models_3d`,
`scene_3d`; every bot, with or without a VM): `mag-scene` runs the pinned
Blender on the CPU (cores 28-55, no device access, never the MI50), reads
what each figure is, gives animals and two-legged figures a skeleton and
moves their limbs; only a thing moves as a whole. A scene is described, never
scripted: no code of a bot or a request runs in Blender. The scene is a normal
message that plays in the chat at once; its film is rendered as a stage of
its own, survives restarts like a 3D job and joins the message.
The film is lit to look like the scene in the chat: key, fill and rim, and
figures that show their own colours; never ship a preset that renders dark.
A 3D job survives everything short of being deleted: its state and the result
of every finished stage are on disk, a stage runs on its own
(`KillMode=process`) and is followed again after a restart of the worker, a
lost stage starts again after a reboot, and the room picks a job up again
after its own restart. Nothing writes a job off as "interrupted"; never make
a deploy or a restart cost a running 3D job.
A finished HTML page of a bot (an `html` code block of its answer, or a file
it writes below `/home/bot/www`) is published on the bot's own tailnet site
and runs in the chat as a live preview framed from that site, with Code/
Vorschau, Kopieren and Vollbild (the page in a new tab). Bots post no
screenshots.
A bot's name is the one source of truth for how it is called and where its
site lives: `<name>.bot.stardenburg.de` (tailnet only; nginx hands these
hosts to the room, which serves the folder the bot's VM published). Bots are
renamed in the settings; earlier names, the bot id and the VM name
(`agentN`) redirect to the current address. The ids `lena`/`noah` and the
VM binding never change with a name.
Each bot has its own closed VM (browser, shell, files), bound to the bot's
id. In the bot's panel the owner sees that browser live and works in it, has
a real terminal in the VM (a PTY as user `bot`, xterm.js) and browses, opens,
downloads and deletes the bot's files; nothing there reaches outside the VM.
Browser and terminal run over the VM's realtime link: `mag-browser` in the
VM, authenticated UDP to `mag-browser-webrtc` on the host, WebRTC data
channels to the page. Pictures over HTTPS and single commands are only the
fallback where UDP cannot get through; never let them replace the link.
The bots read the whole chat: text files inline, 3D orders and their
results, images and documents. A document is read for them once: the text of
a PDF (OCR on the first pages of a scan), a Word, OpenDocument or PowerPoint
file is part of the message, and a long one continues as `<name>.txt` next to
the file in the bot's VM. A bot whose model has a vision projector gets the image
itself; it also writes down once what the image shows (a short branch off
its prompt log, never appended to it), and a bot without eyes reads that.
An image stands in the prompt right in front of its own `[Bild …]` line,
never at the head of a bundle of messages, and is asked about with the image
itself in front of the question, never by its name: otherwise a model
answers about another picture it remembers. The same picture posted again is
the same picture (one description); the owner can have a wrong one made anew.
A picture whose file ends in its middle is not taken as it is: the page reads
the file again first, and photo to 3D refuses it in words.
The bots share a notebook of skills (`skill`: list, read, save; `skills.json`
in the room state): how they do recurring work, with the steps, how to check
the result and what needs Chris's approval. A skill describes work and grants
nothing; credentials are refused. Chris reads and removes them in the settings.
The UI is mobile first: streamed answers appear below the user message in the
order they are produced, the view follows only while the reader is at the
end, and a running step is a live card with the real command, output,
runtime and a cancel button. All settings live in one sheet.
Reading aloud is local as well, and happens on the device: Piper voices run
in the browser (a Web Worker with onnxruntime-web and espeak-ng phonemes;
pinned in `ops/tts`, handed out by the room under `/tts/`), one voice and
tempo per speaker. Nothing plays by itself and no speech is made on the
server; where the voices are not installed the device's own voice reads.
Dictation is local and live: the microphone button sends 16 kHz PCM to the
room while Chris speaks (`/api/stt/live`), `mag-stt` recognizes it and the
text grows in the message field; a sentence followed by a pause is final.
`mag-stt` is its own service (transcribe.cpp with primeLine's German Parakeet
TDT as GGUF, pinned in `ops/stt`), CPU only on cores 28-55, with the model
always loaded. No cloud recognizer, no stored recording, and the journal gets
numbers, never what was said. The microphone is asked for when the button
goes down and stays open for a minute after a dictation, so nothing waits.
The retired whisper model stays on disk like every other model.

Access: only the owner logins in `access.json`, attested per request by the
Tailscale whois subrequest of nginx. There are no guests.

## Default bot execution contract

Enforce autonomy centrally in the C++ engine and the shared system prompt.
Chris revoked the ban on bot-triggered room turns on 2026-11-04.
In the MAG group room, bot messages may trigger another bot's response, including
explicit @mentions, follow-up questions, corrections and disagreement. Bots see
other bots' room messages as context and talk to each other naturally like
humans, server-side and without any client connected. A human message without
an addressee is answered by ALL active bots; then the bots keep talking on their
own (MAG_ROOM_BOT_ROUNDS and the setting "Bots untereinander", default 0 =
unlimited; as a safety net the room rests after MAG_ROOM_BOT_FUSE = 24 bot
turns in a row without a word from Chris). PASS is each bot's own choice and
invisible; a repeated answer or a bare acknowledgement counts as PASS; when
every bot PASSes in a row the chat rests until the next message. New human input and Stop
immediately preempt these bot rounds.
An explicit @mention decides who is asked: after `@Lena …` only Lena answers
and takes part until Chris writes again. Noah gets no turn, not even one to
PASS; he reads the message as context (`Chris an Lena: …`) and never treats
it as his task. Several mentions or `@alle` mean all of them; a name without
@ addresses as before. A mention is stored with the message as the bot's id,
so the page's chip and the meaning follow a rename; earlier names keep
working. The message field suggests the bots after `@`.
Chris can take a bot out in two ways, and both hold across reloads, deploys
and reboots. Paused (the dot in front of its name in the header, or its
sheet): the bot gets no turn and makes no model request, its model stays
loaded, and when it is back it reads what it missed. Switched off (its sheet,
`mag-modeld ctl <bot> stop`): the model server stops and frees its memory;
the weights stay on disk. A paused bot that is asked by name does not answer,
and the room says so. A pause changes no prompt. Per-backend model turns remain serial
(one request per -np 1 slot).
This permission is for room conversation, not unrestricted autonomous tool work.
Outside room dialogue, continue tools only on a successful tool result, distinct
progress and [[MAG:WEITER]]; otherwise remain idle.
Never fake tool results or treat a site access restriction as a cookie issue
without supporting evidence. After identical errors, diagnose rather than
retry blindly. Keep the existing per-agent KVM and browser ownership; do not
weaken guest sandboxing or introduce headless-stealth bypasses. Test VM
lifecycle against a disposable VM; never use a user's existing bot as a
destructive test target.

Keep regression coverage for idle autonomy, cancellation, bounded bot dialogue
and single-owner tool continuation. The CI fixture uses a fake model and a
fake guest runner; it does not prove the deployed models or the browser in
the existing VMs work.
