# Changelog

Releases are tagged `mag-stack-<version>` and published on GitHub with
detailed notes (German, for the owner). Versions follow SemVer: a major
version for a new architecture or anything an existing host has to do by
hand, a minor version for new features, a patch for fixes.

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
