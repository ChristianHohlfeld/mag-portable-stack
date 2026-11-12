# Contributing

MAG lives in this repository. Read [`AGENTS.md`](AGENTS.md) first: it holds
the rules for models, deploys, the room and the bots' execution contract.
How the parts fit together: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Build

C++17 with CMake and Ninja. On the production host every build and job runs
on CPUs 28-55; the physical cores 0-27 belong to the models.

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
taskset -c 28-55 ninja -C build
```

Build dependencies (Ubuntu 24.04): `ninja-build`, `cmake`, a C++17
compiler, `libssl-dev`, `libcurl4-openssl-dev`, `libicu-dev`, `jq`; Go for
`mag-room/webrtc` (built by `ops/deploy.sh`). The test steps also use
`python3-numpy`, `python3-scipy`, `python3-pil` and `node`.

The release targets are listed in `ops/build-targets.sh`. `mag-stt` builds
without its recognizer unless `MAG_TRANSCRIBE_SRC` and
`MAG_TRANSCRIBE_BUILD` point at the build of `ops/stt/bootstrap.sh`.

The page is plain JavaScript without a build step. Every dependency is
vendored under `mag-room/web/static`; the page loads nothing from outside
the release.

## Checks

CI (`.github/workflows/ci.yml` on GitHub, `.gitea/workflows/ci.yml` on the
host runner) runs, in this order:

1. Build the release targets and `mag-stt`, `test-group-dispatcher`,
   `test-model-text`, `s23ctl`.
2. `bash ops/ci/check.sh`: shell syntax of everything a deploy runs, valid
   `models.json` without GPU flags, the vision projector on the CPU, a
   deploy that never waits for a model or drains, Idol and scene workers
   with `KillMode=process`, no device access for scenes, a power-cap script
   that only lowers, the needrestart rule, bot sites tailnet-only, no
   external URL in the page, the VM panel on its realtime link with the
   pinned xterm.js, and the pinned reading-aloud worker.
3. `python3 ops/models/tests/test_modeld.py`
4. `python3 ops/idol/tests/test_worker.py`, `test_unwrap.py`,
   `test_surface.py`, `test_photo.py`
5. `node ops/tts/tests/test_text.js`
6. `python3 ops/scene/tests/test_rig.py`, `test_motion.py`,
   `test_animal.py`, `test_worker.py`
7. `python3 devices/s23/tests/test_s23ctl.py`
8. `./build/test-group-dispatcher`, `./build/test-model-text`,
   `python3 mag-room/tests/test_native.py`

A second job runs gitleaks over the whole history.

Run `python3 mag-room/tests/test_native.py` after changes to HTTP,
scheduling, persistence or room routing, and
`python3 ops/models/tests/test_modeld.py` after changes to the model
supervisor. The room tests use a fake model, a fake guest runner and fake
workers; they do not prove that the deployed models or the browser in the
real VMs work.

## Pull requests

- One topic per pull request. Each one is deployed and checked live on its
  own.
- Branch from `main`; `main` is what gets deployed. Branches are short-lived.
- Merges are squash merges: one commit on `main` per pull request, with the
  pull request number in the subject.
- Subjects follow `type(scope): what changes, in plain words`, for example
  `feat(vm): every bot VM is saved nightly and on request, and restored from a save`
  or `fix(idol): a stage that runs is always found and followed after a restart`.
  Types in use: `feat`, `fix`, `test`, `docs`, `ci`, `perf`, `chore`. Scopes
  name the part: `room`, `web`, `vm`, `idol`, `scene`, `tts`, `stt`,
  `backup`, `bots`, `mi50`, `video`, `imagegen`, `deploy`, `ci`.
- Merge when CI is green.
- Describe what is there now; leave no dead code, retired units or old
  documentation behind.

## Never in a pull request

- secrets, tokens, credentials, raw transcripts or chat content;
- deleting a model file, or starting or stopping a model from a pull
  request, a deploy hook or a cleanup script (`mag-modeld` owns the models);
- anything that raises the MI50 power cap or gives the card to a bot model
  or a scene;
- Docker, or the old Python/ComfyUI/Open-WebUI/Ollama stack;
- motion data or models whose terms forbid commercial use or passing them
  on: this repository is public.
