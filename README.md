# MAG stack

MAG is a private chat room on one home server. One owner (Chris, on phone
and desktop) talks with two local language-model bots, Lena and Noah. Both
run on the CPU with llama.cpp. Each bot has its own KVM virtual machine with
a shell, a real Chrome and a small web site. An AMD MI50 turns photos into
3D models, words into pictures and pictures into videos. The bots build
animated Blender scenes from those models. Dictation runs on the server,
reading aloud runs on the device. Models, speech and media generation run
on this host; only web search and plugins Chris connects reach outside.
Every part is in this repository.

Read next:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): processes, ports, request
  flow, state, restart behaviour.
- [docs/INSTALL.md](docs/INSTALL.md): from a blank Ubuntu 24.04 host to a
  running system.
- [docs/OPERATIONS.md](docs/OPERATIONS.md): deploy, rollback, models, VMs,
  backups, logs, TLS, access.
- [CONTRIBUTING.md](CONTRIBUTING.md): build, checks, pull requests.
- [AGENTS.md](AGENTS.md): the rules every change must keep.

## Components

| Component | What it is | Where |
| --- | --- | --- |
| Room | `magroom` (C++17): page, API, event stream, conversation engine, tools; unit `mag-120.service` | `mag-room/native`, `mag-room/web` |
| Lena | Gemma 4 26B-A4B (Q4_K_M) with vision projector, llama.cpp, CPU only, NUMA0, cores 0-13, port 8191 | `ops/models` |
| Noah | gpt-oss 120B (MXFP4), llama.cpp, CPU only, NUMA1, cores 14-27, port 8192 | `ops/models` |
| Model supervisor | `mag-modeld`: download, size and SHA256 check, start of `mag-model@<bot>.service`, `/run/mag-models/status.json` | `mag-room/native/mag_modeld.cpp`, `ops/models` |
| Audit layer | `mag-audit-gateway` (only path to the models, full log), `mag-audit-webproxy` (mitmproxy, every web access), nft rules | `mag-room/native/mag_audit_gateway.cpp`, `ops/audit` |
| Bot VMs | `magbot`: spawn, reset, save, restore of one KVM guest per bot from a golden image | `mag-room/native/vm.cpp`, `ops/vm`, `ops/mag-agents.nft` |
| VM live link | `mag-browser` in each VM, `mag-browser-webrtc` on the host: browser picture and terminal over UDP/WebRTC | `mag-room/native/browser.cpp`, `mag-room/webrtc` |
| Bot sites | `<name>.bot.stardenburg.de` (tailnet only), uploaded through `mag-bots-publish` | `ops/bots` |
| MI50 queue | `mag-idol`: photo to 3D (TripoSG, Idol), pictures (FLUX.2 klein 4B), videos (Wan 2.2 I2V-A14B); one job at a time, 72 W | `ops/idol`, `ops/imagegen`, `ops/video` |
| Scenes | `mag-scene`: pinned Blender on the CPU, rigging, recorded and worked-out motion, films | `ops/scene` |
| Dictation | `mag-stt`: transcribe.cpp with German Parakeet TDT, CPU only | `mag-room/native/mag_stt.cpp`, `ops/stt` |
| Reading aloud | Supertonic 3 in the browser (onnxruntime-web, WebGPU or WASM), served under `/tts/` | `ops/tts`, `mag-room/web/app/speak.js` |
| Ingress | Tailscale, nginx with whois `auth_request`, Let's Encrypt via lego DNS-01 | `ops/private-access`, `ops/nginx`, `ops/tailscale` |
| Gitea and CI | Gitea 28.1.0, Actions runner on the host, pull mirror of GitHub, Git access for the VMs | `ops/gitea`, `ops/gitea-ci`, `ops/runners` |
| Backups | restic archives (daily, before each deploy, on request), nightly VM saves | `ops/backup`, `ops/vm` |
| Health and boot | `mag-stack-check.timer`, CPU tuning, boot enablement | `ops/health.sh`, `ops/boot` |
| Phone tools | `s23ctl`, Chris's own control daemon for his phone; the room only talks to it while it runs | `devices/s23` |

## Repository layout

| Path | Purpose |
| --- | --- |
| `CMakeLists.txt` | all native targets |
| `mag-room/native` | the room and the native helpers (`magroom`, `mag-modeld`, `mag-audit-gateway`, `magbot`, `mag-browser`, `mag-stt`) |
| `mag-room/web` | the page: plain JavaScript, vendored libraries, no build step |
| `mag-room/webrtc` | `mag-browser-webrtc` (Go) |
| `mag-room/tests` | room tests with a fake model, fake guest, fake Idol, fake scene worker and fake MCP server |
| `ops/deploy.sh`, `ops/rollback.sh`, `ops/build-targets.sh`, `ops/prune-releases.sh` | releases |
| `ops/mag-120.service`, `ops/mag-browser-webrtc.service`, `ops/mag-stack-check.*`, `ops/mag-agents-network.service`, `ops/mag-git-gateway.*`, `ops/mag-agent-drift.*` | units of the release |
| `ops/models` | `models.json`, model units, launcher, downloader, runtime build |
| `ops/audit` | audit gateway unit, web proxy, nft rules, VM proxy setup |
| `ops/vm` | golden image build, VM save units |
| `ops/spawnbot`, `ops/despawnbot`, `ops/magbot-*`, `ops/firewall.sh`, `ops/mag-agents.nft`, `ops/agent-drift.sh` | VM wrappers and isolation |
| `ops/bots` | bot sites: publisher, nginx vhost, wildcard certificate |
| `ops/idol`, `ops/imagegen`, `ops/video` | MI50 worker and weight fetchers |
| `ops/scene` | scene worker, Blender scripts, motions, face landmarker |
| `ops/stt`, `ops/tts` | speech to text, reading aloud |
| `ops/private-access`, `ops/nginx`, `ops/tailscale`, `ops/trust` | ingress, TLS, Tailscale, trust roots |
| `ops/gitea`, `ops/gitea-ci`, `ops/runners` | Gitea, its public vhost, the Actions runner |
| `ops/backup` | backup, restore, maintenance, units |
| `ops/boot`, `ops/health.sh` | boot enablement, CPU tuning, health check |
| `ops/security` | journal secret scan and purge |
| `ops/ci/check.sh` | static checks |
| `devices/s23` | phone control daemon (not part of a release) |
| `docs` | this documentation |

## Quick start

A new server, from nothing to a running MAG (secrets only where
[docs/INSTALL.md](docs/INSTALL.md) says):

```bash
git clone https://github.com/ChristianHohlfeld/mag-portable-stack.git ~/mag-stack
cd ~/mag-stack && sudo ./install.sh
```

Build and test on a development machine (CPUs 28-55 on the production host):

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
taskset -c 28-55 ninja -C build
```

## Deploy and rollback

```bash
cd ~/mag-stack && git checkout -q main && git pull -q --ff-only origin main \
  && taskset -c 28-55 sudo -n bash ops/deploy.sh
sudo bash ops/rollback.sh <RELEASE_ID>
```

A deploy builds, takes a backup, stages `/opt/mag-stack/releases/<id>`,
switches, checks `/readyz` and nginx, and rolls back on failure. It never
drains and never waits for a model. Details in
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Tests

These are the steps CI runs (`.github/workflows/ci.yml`):

```bash
bash ops/ci/check.sh                          # static checks
python3 ops/models/tests/test_modeld.py       # model supervisor
python3 ops/idol/tests/test_worker.py         # MI50 jobs survive restarts
python3 ops/idol/tests/test_unwrap.py
python3 ops/idol/tests/test_surface.py
python3 ops/idol/tests/test_photo.py
node ops/tts/tests/test_text.js               # text written as it is spoken
python3 ops/scene/tests/test_rig.py           # scenes
python3 ops/scene/tests/test_motion.py
python3 ops/scene/tests/test_animal.py
python3 ops/scene/tests/test_worker.py
python3 devices/s23/tests/test_s23ctl.py      # phone control daemon
./build/test-group-dispatcher                 # room
./build/test-model-text
python3 mag-room/tests/test_native.py
```

A separate CI job scans the history for secrets with gitleaks.

## Third-party models, data and libraries

Only what the repository itself records is listed. Where no licence is
recorded here, read the upstream model card before relying on it.

| Item | Used for | Licence as recorded in this repository |
| --- | --- | --- |
| TripoSG (VAST-AI) | whole shape of a photo's object | MIT (`ops/idol/shape.py`) |
| FLUX.2 klein 4B (Black Forest Labs) | pictures from words | Apache-2.0 (`ops/imagegen/fetch.sh`) |
| Wan 2.2 I2V-A14B (Wan-AI), GGUF by QuantStack, Lightning LoRA by lightx2v | videos from pictures | Apache-2.0 (`ops/video/fetch.sh`) |
| Parakeet TDT 0.6B v3 (NVIDIA), German fine-tune by primeLine, GGUF | dictation | CC-BY-4.0 (`ops/stt/bootstrap.sh`) |
| transcribe.cpp | dictation runtime | pinned in `ops/stt/transcribe.lock`; licence not recorded here |
| Supertonic 3 (Supertone) | reading aloud | weights OpenRAIL-M, see its `LICENSE` (`ops/tts/bootstrap.sh`, `ops/tts/source.lock`) |
| onnxruntime-web 1.30.0 | reading aloud runtime | MIT (`ops/tts/bootstrap.sh`) |
| MediaPipe face landmarker | faces in scenes | Apache-2.0 (`ops/scene/face/bootstrap.sh`) |
| CMU Graphics Lab Motion Capture Database (BVH by Bruce Hahne) | `ops/scene/motions.npz` | free for research and commercial use, credit required (`ops/scene/MOTIONS.md`) |
| Blender 4.5.16 | scenes | pinned in `ops/scene/blender.lock`; licence not recorded here |
| Gemma 4 26B-A4B (HauhauCS build), gpt-oss 120B (heretic v2 build) | Lena, Noah | not recorded here; URLs in `ops/models/models.json` |
| SAM 2.1 hiera small, Depth Anything V2 metric indoor small | object selection, room depth | not recorded here; revisions pinned in `ops/idol/install.sh` |
| Idol | splat sampling on the MI50 | not recorded here; installed outside this repository |
| xterm.js 5.5.0, fit addon 0.10.0 | VM terminal | MIT (`mag-room/web/static/xterm-LICENSE.txt`) |
| marked 12.0.2 | Markdown | MIT (file header) |
| DOMPurify 3.1.6 | HTML sanitising | Apache-2.0 or MPL-2.0 (file header) |
| highlight.js 11.10.0 | code highlighting | BSD-3-Clause (file header) |
| Inter | font | SIL OFL 1.1 (`mag-room/web/static/Inter-OFL-LICENSE.txt`) |

PartField is not used: its licence forbids commercial use. Motion data may
only be added if its terms allow commercial use and passing it on.
