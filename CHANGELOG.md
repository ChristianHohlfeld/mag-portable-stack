# Changelog

Releases are tagged `mag-stack-<version>` and published on GitHub with
detailed notes (German, for the owner). Versions follow SemVer: a major
version for a new architecture or anything an existing host has to do by
hand, a minor version for new features, a patch for fixes.


## Unreleased

- Jobs again, per bot: each bot's sheet lists its recurring jobs (and the
  VM they run in), the settings all of them; set up, change (name, what,
  when, bot), pause, run now and delete right there. The bots use the same
  jobs through their `routine` tool, now also with `update`.
- Reading aloud: never fewer than five steps (#562), parts by sentences and
  a start with enough made ahead (#563).
- A step card says in words what the bot asked for and what happened, not
  the hint meant for the bot (#564).

## 5.4.0 (2026-11-13)

- Reading aloud is Supertonic 3 again, now in the client alone: the browser
  fetches the model from Hugging Face and the runtime from jsDelivr (pinned,
  SHA256), once into Cache Storage; the room serves nothing for it. Piper
  (5.3.0) sounded robotic and is gone (#560).
- A login card is the bot's browser itself, live in the chat over its UDP
  link (WebRTC) and usable right there (tap, type, keyboard, also on the
  phone): Chris logs in himself and taps "Angemeldet, weiter". The login and
  code forms, the room filling them in and the static picture are gone
  (`/api/login`, `tools::login_form`); a pay card shows the live browser too
  (#559).
- H3 does what Chris asked: the rewriter formats and translates, invents
  nothing and drops no line (#558).

## 5.3.0 (2026-11-13)

- Reading aloud in the client alone, on the phone and the desktop: Piper
  voices (onnxruntime-web in WASM, espeak-ng) instead of Supertonic 3, each
  voice German and English (Kerstin, Ramona, Thorsten, Karlsson; Alba and Joe
  in English), picked by the language of the text. The browser fetches about
  63 MB per voice and 33 MB of runtime once from jsDelivr and Hugging Face,
  pinned by SHA256, and keeps them; the room serves, installs and records
  nothing for speech any more (`/tts/`, `/api/tts/stat`, `ops/tts` removed;
  `/opt/mag-tts` stays on disk).
- A new message does not cancel the system prefix being evaluated, so it is
  saved and the next trash restores it (#555).

## 5.2.1 (2026-11-13)

- The MI50's power cap is 78 W (Chris, 2026-11-13, 17:52; 82 W in 5.2.0);
  `ops/idol/power-cap.sh` only lowers it and never writes more than 78 W.
- H3 prompts in MiniMax's own form (the model card's "Context IR"), speech as
  its guide writes it (#552).

## 5.2.0 (2026-11-13)

- Teilen in every message's ⋯ menu and in the file preview: the files through
  the phone's own sharing (Web Share), else a download; words are shared or
  copied (#545).
- H3 videos are decoded by madebyollin's tiny autoencoder `taeh3` (MIT): seconds
  instead of about two minutes per 5 s clip (#548).
- H3's linear layers are ggml's MMQ again: the own W8A8 GEMM of #544 brought no
  speed (#550).
- The MI50's power cap is 82 W (Chris, 2026-11-13; 73, 85 and 100 W for a while
  that day, #546, #547, #549); `ops/idol/power-cap.sh` only lowers it and never
  writes more than 82 W.

## 5.1.0 (2026-11-13)

Videos with sound by HyperFlow H3, painted 3D models, and a chat whose every
message can be deleted; 41 pull requests since 5.0.0 (#502–#542). A 5.0.0 host
is updated with `ops/deploy.sh`; for videos the owner runs
`ops/video/h3/fetch.sh` and `ops/video/h3/build.sh` once (licence).

- The MI50's power cap is 78 W instead of 72 W (Chris, 2026-11-13);
  `ops/idol/power-cap.sh` still only lowers it and never writes more than 78 W
  (#532).
- Videos with their sound: HyperFlow H3 Flash (MiniMax H3, licensed to the
  owner personally; never fetched by the installer) replaces Wan 2.2, natively
  on ROCm/HIP on the MI50, 4 steps at 480p, 5 s by default (#518, #521, #522).
  gfx906 kernels of its own: attention with int8 Q·Kᵀ (v_dot4) and one Q scale
  per row, RMSNorm with the AdaLN modulation in one pass, the VAE's Conv3d as
  an implicit fp16 GEMM (#524, #526, #528, #529); `h3d` keeps H3 loaded between
  clips and lets go of it for 3D and pictures, finds its weights through hard
  links and is asked to load only another model (#530, #531, #536); an H3 clip
  is resumable like 3D and pictures: a restart (deploy, h3d, reboot) goes on
  after its last finished step, or only decodes (#535); „Fortsetzen“ at an H3 video (and `video_generate`
  with `continue`) makes a new part from its last frame and joins the whole
  video without a seam in one new message (#538), and never meets a worker
  that takes the video for a picture (#539); H3's own special tokens and
  speech in its form (#541).
- Photo to 3D: TripoSG's shape as its own scripts make it (no bust cut, hard
  mask, full picture), painted from six sides by MV-Adapter on SDXL, the views
  made twice as large by Real-ESRGAN as MV-Adapter's texture script does; a
  fresh host fetches the paint weights in its installer (#519, #520, #523,
  #525).
- Browser: the room finds the login form itself, logins are kept sealed and
  offered again, a two-step login gets a code card, and the header says when
  a bot waits at a login or a checkout (#514, #515, #516).
- Bots: the bot at work keeps its task when Chris writes; a bot at a login
  page is sent to `browser_login`; consent is not for opening, reading or
  logging in and does not make a bot the lead; the bot that stands back ends
  at once; the goal driver stops at a no from Chris and at a refusal on
  purpose (#508–#512, #517).
- Routines: a new file in a bot's VM sets an event routine off (#507).
- Scenes: lips follow the sounds of speech (`phonemes.py`), moving parts are
  found along the folds of the surface, the room of a photo stands at once
  when no bot can name its objects (#504, #506, #517).
- Reading aloud: after a reload the newest answers speak at once (#503, #505).
- Chat: English twin lines, written-out tool calls and text approvals are not
  shown, also in old messages; a camera photo that does not arrive is said in
  words (#502, #513).
- Chat: every message (Chris's, a bot's, picture, video, 3D, file) has the
  same row with ⋯ and „Nachricht löschen“, always in sight; a running job can
  be deleted too (#527). Code longer than 12 lines folds like a step, and a
  tool call or a scene a bot writes into its text as JSON is taken out and the
  bot is sent to make the call for real (#533, #537). The room's markers
  (`[[MAG:WEITER]]`) and a lone PASS never reach the chat, also after a step
  or a restart (#540); the label of „Fortsetzen“ sits in the middle of its
  button (#542).
- CI on GitHub green again (#517).

## 5.0.0 (2026-11-12)

The stack as it runs on mag now; 575 commits since 4.0.4.

- New room core: mobile-first UI, one message model, real trash, @mentions,
  pause and switch-off per bot, reset to the first day, bots talking to each
  other, a model's thoughts never shown.
- Two CPU-only bots: Lena (Gemma 4 26B-A4B with vision) and Noah (gpt-oss
  120B), supervised by mag-modeld; the MI50 belongs to Idol alone (72 W).
- Bots work: their own VM with a browser they use like a person
  (`browser_look`, numbered elements, keys, waits), logins and payments as
  Chris's cards, a sealed card vault, approvals per action with an auto mode
  and a sealed journal, routines, a goal driver, one bot per task, skills,
  plugins (remote MCP), phone tools, live HTML pages and bot sites.
- MI50 queue (Idol), reboot-proof and reorderable: photo to 3D with
  TripoSG's whole shape and the photo as texture, object selection (SAM 2),
  whole rooms from a photo (depth), pictures from words (FLUX.2 klein),
  videos from pictures (Wan 2.2).
- Scenes (Blender on the CPU): every body type found by itself (two legs,
  four legs, birds, fish and snakes, insects, vehicles, things), about 150
  recorded motions, IK poses, hands, faces with MediaPipe, lips in step with
  voices made on the device, climbing and parkour, fights, parts that open,
  camera moves, films on request.
- Local dictation (Parakeet, mag-stt) and reading aloud in the browser
  (Supertonic 3).
- Files: one folder for every file of the chat, real previews, ZIP.
- Backups: room/Gitea nightly, every bot VM nightly with restore, backup on
  request in the chat, the whole system for new hardware with its restore.
- Self-contained: `sudo ./install.sh` from this repository, host identity in
  `/etc/mag-stack/host.env`, secrets only from templates, third-party parts
  pinned with checksums (Chrome, mitmproxy, ROCm torch, the venvs, Go),
  `--probe` for a trial install; a fresh droplet installed in one run
  (PROBE OK).
- Removed: the coding room, DeepSeek/Qwen/Ornith/GLM/Flint, Piper, Caddy,
  the Python gateway, the self-updater, the GitHub runner, Pages and other
  leftovers.

## 4.0.4 and earlier

See the GitHub releases `mag-stack-4.0.0` to `mag-stack-4.0.4`.
