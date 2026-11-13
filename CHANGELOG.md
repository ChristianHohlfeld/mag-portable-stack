# Changelog

Releases are tagged `mag-stack-<version>` and published on GitHub with
detailed notes (German, for the owner). Versions follow SemVer: a major
version for a new architecture or anything an existing host has to do by
hand, a minor version for new features, a patch for fixes.

## Unreleased

- A video is resumable like 3D and pictures: the sampler's state after every step
  is on disk, and a restart (deploy, h3d, reboot) goes on after the last finished
  step, or only decodes.
- The MI50's power cap is 78 W instead of 72 W (Chris, 2026-11-13);
  `ops/idol/power-cap.sh` still only lowers it and never writes more than 78 W.
- Videos with their sound: HyperFlow H3 Flash (MiniMax H3, licensed to the
  owner personally; never fetched by the installer) replaces Wan 2.2, natively
  on ROCm/HIP on the MI50, 4 steps at 480p, 5 s by default (#518, #521, #522).
- Photo to 3D: TripoSG's shape as its own scripts make it (no bust cut, hard
  mask, full picture), painted from six sides by MV-Adapter on SDXL; the
  installer fetches the paint weights (#519, #520).
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
