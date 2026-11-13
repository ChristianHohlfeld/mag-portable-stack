# MAG stack

One home server, one user (Chris, phone and desktop), one room, two bots.
This repository is the only source of the room, the model layer, the VM
helpers and the deployment. Architecture: `docs/ARCHITECTURE.md`.

The user explicitly chose C++ and removed Docker and the old
Python/ComfyUI/Open-WebUI/Ollama application stack. Do not reintroduce them.
Python is used for tests, one-off scripts and the Idol worker (ROCm/PyTorch).

A fresh server is installed with `sudo ./install.sh` (`ops/install/run.sh`,
phases that go on where they stopped, docs/INSTALL.md): everything the host
runs comes from this repository; secrets only from the documented templates
(`ops/install/templates`). Whatever a host needs must be installed by it: a
change made by hand on mag belongs in the installer or a deploy. Who and
where the host is (owner, domain, bot zone, Tailnet address and name) is one
file, `/etc/mag-stack/host.env` (defaults `ops/host.env`); scripts source
`ops/host.sh`, and units, nginx sites, `models.json` and `app.ini` keep the
reference host's values in the repository and are rendered with `mag_render`
when they are installed. Never write a host's name, address or owner into a
script.

Build with CMake/Ninja. Run `python3 mag-room/tests/test_native.py` after
changes to HTTP, scheduling, persistence or room routing, and
`python3 ops/models/tests/test_modeld.py` after changes to the model
supervisor. Keep frontend JavaScript plain (no build step) and serve every
dependency locally. Builds and jobs run on CPUs 28-55 only
(`taskset -c 28-55`); the physical cores 0-27 belong to the models.

Production is `mag-120.service` -> `/opt/mag-stack/current/bin/magroom`.
Deploy versioned releases through `ops/deploy.sh`; `ops/rollback.sh` reverts
executables. A deploy never drains, never waits for a model and never fails
because a bot is downloading, loading, paused or stopped. It may fall into a
bot's turn: an answer Chris asked for and of which nothing could be seen yet
(the bot was reading or thinking) is given after the restart; one that was
cut off in view stays as it is ("Gestoppt"). Runtime state, VM
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
Idol alone and its power cap stays at 78 W (Chris, 2026-11-13; before: 72 W;
`ops/idol/power-cap.sh` only ever lowers it; never add anything that raises
it, and nothing above 78 W). `mag-modeld` is the single
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
moves their limbs; a long body without legs (a snake, a fish) gets a chain
of bones and slithers in a wave; a bird (two legs under a long body) walks,
pecks and flies with beating wings; an insect or spider walks on all its legs
in alternating sets; a vehicle (found by its wheels, or `kind: vehicle`) drives
along a curve and its wheels turn as it rolls; only a
thing moves as a whole. A part of a thing or vehicle (a lid, a door, a turret,
a bonnet, a boot, a drawer; `part` names its side or the part with its side,
"Tür links") is found on the surface itself (`region()` in `scene.py`, no
model): from the middle of its side and from the inner half of the thing two
floods grow over the faces and meet where the surface folds inwards (a groove,
a step, an inner crease), and that edge is the part's, if it runs along folds
for the most part; else the side is cut by a plane at the seam of its
cross-section as before. Both cuts are closed with an inside of their own (the
seam's colours, darker, as a small texture); the part turns about a hinge on
its edge (`open`, `close`), a drawer slides out with walls of its own and
leaves a pocket, and `turn_part` spins it about the middle of its edge (a
turret). PartField is not used (its licence forbids commercial use). A figure on two legs that is to speak or
show a feeling (`say`, `face`) gets a face: the front of its head is rendered
on the CPU and MediaPipe's face landmarker (Apache-2.0, pinned in
`ops/scene/face`, installed for Blender's Python) finds mouth, eyes and brows,
else the head's proportions place them; small bones (jaw, mouth corners, lids,
brows) carry the skin, speech goes from the words to their sounds (German by
rules, clearly English text by lighter ones, numbers as words; pure Python in
`ops/scene/phonemes.py`) and from the sounds to mouth shapes that last as long
as each sound, and it blinks now and then. Its film speaks what was said: when Chris asks for the
film, the page makes the voices on the device (Supertonic, one voice per
figure) and sends them as the film's sound; nothing is spoken on the server.
Each voice's tempo is fitted to how long that mouth moves (0.8x to 1.3x), so
lips and voice start and end together; the same voices play in the chat with
the scene (its speaker button), following the viewer's clock. A figure on two legs moves
with recorded motions (`ops/scene/motions.npz`, made by
`ops/scene/mocap/build.py` from motion capture that is free to use and to
pass on, credited in `ops/scene/MOTIONS.md`): its bones take the recorded
directions, its way follows its own legs, a foot that stands stays where it
stands, and nobody in a scene stands like a statue. An animal's motion is
worked out (`ops/scene/animal.py`): its feet take turns in the order of its
gait and stand where they were put down, the body rides on the legs that
carry it, and it sits, lies, leaps and lands on the same ground. About 150 recorded motions are
found by their description (`do: motion`, `query`) and joined at the pose
nearest the one before; a free `pose` puts a hand somewhere (up, to the
head, pointing at or touching another) with IK on the figure's own arm. A
fight is told with kick, punch or
attack and a target: the one who is hit flies away in an arc, lands, lies
and gets up again (`knock` in `scene.py`). Somebody on two legs climbs onto a thing
(`climb`; up a high one hand over hand), vaults over it (`vault`) and jumps
down (`jump_down`): its feet stand on what it stands on. This repository is
public: never add motion data whose terms forbid commercial use or passing
it on. A scene is described, never
scripted: no code of a bot or a request runs in Blender. The scene is a normal
message that plays in the chat at once. Its film is rendered only when the
owner asks for it at the scene ("Film rendern"), as a stage of its own that
survives restarts like a 3D job and joins the message; the `.blend` is
fetched on request too. What a model or a scene should do next is written in
the line right below its view: that message is about it (`about`), and the
bot reads how the scene was made, so a new version has the same figures.
The film is lit to look like the scene in the chat: key, fill and rim, and
figures that show their own colours; never ship a preset that renders dark.
A 3D job survives everything short of being deleted: its state and the result
of every finished stage are on disk, a stage runs on its own
(`KillMode=process`) and is followed again after a restart of the worker, a
lost stage starts again after a reboot, and the room picks a job up again
after its own restart. Nothing writes a job off as "interrupted"; never make
a deploy or a restart cost a running 3D job. A job that failed or was
cancelled is taken up again when the owner asks for it at its message ("Noch
einmal"), at the first stage whose result is missing: sampling that is done
is never done twice.
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
Chris can reset a bot to its first day (its sheet, "Zurücksetzen", one
question): its turn ends at once, its memories, prompt log and slot are
emptied, and its VM is deleted and made anew from the golden image under the
same name, with its files and its published site (`magbot reset`, nothing is
archived). Name, address, model and a pause stay as they were. The reset is
in the bot's record and goes on after a restart of the room; it works from
every state of the VM, and asking twice is asking once.
Each bot VM is saved every night and whenever Chris asks (its sheet):
`magbot save` copies the VM's overlay on the golden image,
compressed, to `saves/<vm>/` while the VM runs on (an external snapshot,
frozen through the guest agent, and a shallow block commit back; never into
the golden image). The last four stay. A restore stops the VM and puts a save
in its place; the disk it replaces is kept as a save first, so a restore is
undone by restoring that. It is in the bot's record like a reset and goes on
after a restart; the conversation and memories stay. A reset leaves the saves.
A backup of everything is one request away: "Jetzt alles sichern" in the
settings, or a bot's `backup` tool when Chris asks in the chat. It starts
`mag-backup-chat.service` (restic: room, Gitea, configuration; never models
or VM images) and `mag-bot-saves@chat.service` (a save of every bot VM) in
the background; restoring stays Chris's (a bot's sheet, `ops/backup/restore.sh`).
The whole system has a backup of its own for new hardware (`ops/backup/system.sh`,
weekly Sunday 05:00, "Gesamtsystem sichern" in the settings, a bot's `backup`
with scope system): restic of the root file system with a description of the
host (packages, disks, libvirt definitions, a consistent Gitea database, the
model manifest) and a fresh save of every VM; models only with `--with-models`
and never onto mag's own disk. It goes to `MAG_SYSTEM_REPO`
(/etc/mag-backup/system.env), else a disk at /mnt/mag-backup, else
/var/backups/mag-system (two states, only with room to spare: no protection
against a broken disk). `ops/backup/system-restore.sh` brings it back on a
fresh Ubuntu 24.04.
Browser and terminal run over the VM's realtime link: `mag-browser` in the
VM, authenticated UDP to `mag-browser-webrtc` on the host, WebRTC data
channels to the page. Pictures over HTTPS and single commands are only the
fallback where UDP cannot get through; never let them replace the link.
A bot works its browser like a person at the screen: `browser_look` gives
every element it can act on, numbered with label and point (and a picture
for the step in the chat), and it clicks or types by number, by point or by
visible text, presses keys and waits. A login, captcha or code is never
typed by a model: `browser_login` puts a card at its step, where Chris types
his login into a form that the room fills into the VM's page (kept sealed
in the vault per site and account and offered again on that site's next
card, never logged or shown to a model) or takes the bot's browser over himself; then
the bot sees the page and goes on. Sessions and cookies stay in the VM's
Chrome profile. A model's thoughts are never shown in the chat.
At a checkout a bot calls `browser_pay`: the purchase goes through
`consent` (kind buy, Chris's rules and limits), then a card where Chris
picks a saved card and gives its security code; the room types it into the
page (fields of the page by their names, a provider's frame by the field
the bot named and Tab). Cards are sealed in `STATE/pay/cards.enc`
(AES-256-GCM, key `STATE/pay/vault.key`, 0600); the security code is never
stored; the page and the bots see brand and last four digits only.
The room drives a bot to its goal: one bot carries out a request (the first
to act on it; the other's acting tools are refused with who does it), and a
bot that worked with tools and stops after a failed step, without a word or
announcing what it will do is sent on by a room note, three times a turn at
most; a card that ended without a yes is a real blocker and ends the work.
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
Photo to 3D makes one model of one object, its whole shape from TripoSG
(MIT, on the MI50 in Idol's queue), made from the shape and the photo alone
(Idol's splat is sampled only where that does not do), coloured the way
TripoSG's authors do it: MV-Adapter (Apache-2.0, on SDXL, on the MI50 in the
same queue) paints the shape from six sides at once from the photo
(`paint.py`, its own defaults), and the model wears those views; only where
its weights are missing or it fails, the side the photo shows wears the
photo and the rest its colours carried around. A photo that is ordered is ordered as it is, one job, nothing
split or looked at unasked (Chris, 2026-11-11). In a photo with several things
Chris can say which is meant ("Objekt wählen": he taps it or names it and sees
the mask, or has all of them found and orders each by itself; a named thing
is found by a bot that sees, on a branch off its prompt log); SAM 2 looks on the CPU
(cores 28-55, never the MI50), and the cut photo is an ordinary order.
"Ganze Szene" makes the photo's whole room: every object is ordered as a
model of its own with where it stood (a depth estimate of the photo, on the
CPU), and when the last of them is through the room puts them together as
one scene, each as tall as it was, where it stood, turned as the photo saw
it, with the floor and the walls around them. Each stays a figure of its
own there, called what a seeing bot said it is, so the bots can move it like
any figure of a scene.
Pictures are painted from words on the MI50 (FLUX.2 klein 4B, Apache-2.0):
"Bild erzeugen" in the plus menu or a bot's `image_generate`; a job of Idol's
one queue, never beside a 3D job, and the result is an ordinary image.
Every picture of the chat (uploaded, painted, a bot's) goes on from its own
place: "3D" (a model, which can then be animated at its message) or "Video".
A video is made WITH its sound by HyperFlow H3 Flash (Video Rebirth's
few-step MiniMax H3, the setup of its own workflow: its sigmas, Euler, CFG 1,
24 fps; for minutes on the MI50 rather than an hour, four steps at 480p, 5 s
or 10 s), from words alone, from a picture as
its first frame or with the picture as who is in it ("Figur", ref2va). On
gfx906 it is the same model as GGUF in stable-diffusion.cpp, one native
path: HIP on ROCm 6.3.4 (unpacked for the owner, never installed into the
system; `ops/idol/h3gen.py`, `ops/video/h3`); no Vulkan, no ComfyUI. "Video erzeugen" in the
plus menu takes a picture from the gallery or the camera, or words alone; a
bot's `video_generate` names the picture as the chat shows it or none. An H3
video goes on ("Fortsetzen", `video_generate` with `continue`): a new part
from its last frame, and one new message with the whole video, joined
without a seam.
MiniMax licensed H3 to Chris personally (2026-11-13: private,
non-commercial, nothing passed on, marked as made by AI, no training of
other models, only him): its weights stay on his host, never in this
repository, a backup, a shared place or an installer that runs by itself,
and every H3 video is marked "KI-generiert · MiniMax H3" in the chat and in
its file. Wan 2.2 made the videos before; its weights stay on disk, unused.
3D, pictures and videos are jobs of one line
on the MI50: whoever has the card has it alone (at 78 W), the others wait
with their place shown (Chris moves them forwards or backwards, or cancels
them, at their message; the line survives restarts and reboots) and run
after it by themselves; the bots order 3D and videos too
(`photo_to_3d`, `video_generate`); a lock file that
cannot be taken never fails a job. Hunyuan models are not to be fetched
(licence).
The bots share a notebook of skills (`skill`: list, read, save; `skills.json`
in the room state): how they do recurring work, with the steps, how to check
the result and what needs Chris's approval. A skill describes work and grants
nothing; credentials are refused. Chris reads and removes them in the settings.
Before a consequential action (send, buy, delete, publish, set up a
routine) a bot calls `consent`; Chris's rules decide (settings "Freigaben":
per kind ask, without asking or never; the auto mode turns every "ask" into
"without asking"; money only within a limit per purchase and per day, above
it always a card). A card at the bot's step waits for his yes (15 minutes);
every decision (who, what, amount, to, when, allowed or not, by the auto
mode or not) is a line of `STATE/assist/ledger.enc`, sealed with AES-256-GCM;
its key (`STATE/assist/ledger.key`, 0600) is the room's alone and never
reaches a model, a tool or the page. A bot that asks for approval in words
is taken back and has to call `consent`; only Chris's tap on the card (an
owner request) allows, never a model and never a yes in the chat. Card numbers and
passwords never reach a model, a tool, a log or the chat; a card is typed
into a page by the room from the sealed vault (`browser_pay`), or Chris pays
himself in the bot's live browser. Routines (`routine`; `STATE/routines.json`) are
instructions for a bot at a time or on an event (a picture Chris posts, a
job he ordered being done, a new file in the bot's VM, optionally in one
folder or by a name pattern); one that is due is a chat message to its bot
(`from: routine`, naming the new files) answered in a normal turn. A run
missed while the room was down runs once when it is late by 12 hours at
most; an event routine runs at most every 15 minutes, and the files a file
routine's own run writes never set it off again. A VM is only read for
this, never started. Chris pauses, runs and removes them in the settings.
Plugins are remote MCP servers Chris connects in the settings and gives to a
bot in its sheet (`plugins` of the bot; `STATE/mcp/servers.json`, a token in
`STATE/mcp/<id>.secret`, never in a prompt, a log or the page). The room
speaks Streamable HTTP from the host: nothing is installed, no server process
is started, and nothing of it runs in a VM. A plugin's tools belong to the
bot's prompt prefix and change only when Chris connects, refreshes or binds.
A tool that is not marked read-only waits in the chat for his yes (allow,
always, deny; it runs out after 15 minutes) unless he set that binding to
"ohne Nachfrage"; with his own account behind a plugin (`connector`) nothing
is ever changed unasked. The scene tools are the room's own, not a plugin.
The phone tools (`s23_*`) are offered to the bots only while Chris runs the
phone's control daemon (`devices/s23`, `mag-s23ctl`); nothing here installs or
starts it, and his other S23 services are never spoken to.
Every file of the chat (uploads, pictures, models, scenes, films, videos,
documents) is also in one folder, the folder button in the header
(`files.js`, `/api/files`): by kind, searchable, each tile showing what the
file really holds (`/api/file/<f>/thumb`, made once in STATE/thumbs: a 3D
file rendered by Blender in the scene worker, never the photo it came from;
a picture scaled, a video's frame, a sound's waveform, a PDF's first page by
ffmpeg/pdftoppm; a text or document its first lines, `/excerpt`), a larger
preview (picture, video, sound, a model that turns, text), a download, and
the way back to its message; what is shown comes as one ZIP (`/api/files.zip`, streamed, nothing
packed on disk). The files stay where they are.
The UI is mobile first: streamed answers appear below the user message in the
order they are produced, the view follows only while the reader is at the
end, and a running step is a live card with the real command, output,
runtime and a cancel button. All settings live in one sheet.
Reading aloud is local as well, and happens on the device: Supertonic 3
runs in the browser (a Web Worker with onnxruntime-web, on WebGPU or in
WASM; pinned in `ops/tts`, handed out by the room under `/tts/`), one voice
and tempo per speaker. The page fetches and loads the model in the
background once, so the first tap speaks at once. Nothing plays by itself
and no speech is made on the server; where the model is not installed the
device's own voice reads. The retired Piper voices stay on disk.
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
What a model writes is never shown raw, and never trusted to be well formed:
the room reads channel markup itself (thoughts are thoughts, kept with the
message but never shown in the chat; no tag reaches the chat), ends a generation that runs in circles or whose tool call runs
away into markup and keeps such an answer once, and gives a tool only
arguments that are a JSON object. Arguments that are not are stored as `{}`:
they must never enter a prompt log, the server refuses every later request
that holds them. An error in the chat is one short line, never a server's
quote of what a model wrote. A tool call a model writes into its text as JSON (a
scene for `scene_3d`, `{"name": …, "arguments": …}`) is no call: the room
takes it out of the text and tells the bot once to make the call for real.
Code in an answer longer than 12 lines is folded like a step (its first lines
as a preview, Kopieren in its head).
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
