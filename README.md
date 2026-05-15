# MAG Portable Stack

Portable, self-contained MAG stack snapshot for transfer to a new machine.

## GitHub Export Note

This GitHub copy is infra-first and excludes large runtime payloads due Git LFS quota limits:

- `models/`
- `open-webui-data/.open-webui`
- `config/comfyui-gpu1.db`, `config/comfyui-gpu2.db`, `config/comfyui.db`

Use `scripts/sync-from-mag.sh` (and optional `scripts/sync-protected-from-mag.sh`) to reconstruct full runtime data.

Included runtime stack:

- `nginx` reverse proxy with `/comfyui` regression-safe routing
- `open-webui`
- `ollama`
- `comfyui-flux` (port 8187)
- `comfyui-wan` (port 8188)

Snapshot metadata:

- Captured: `2026-05-15`
- Manifest timestamp: `2026-05-15T23:15:17+02:00`
- ComfyUI commit: `c9589f29`
- Original local full bundle size: ~`62G`
- Original local models payload: ~`60G` (ComfyUI models + Ollama model store)

## 1) What Is Included (GitHub Infra Bundle)

- Infra code: `docker-compose.yml`, `Dockerfile`, `comfyui/Dockerfile`, Nginx config, scripts, tests
- Full ComfyUI runtime tree from production (`comfyui/`, including custom nodes)
- Workflow blueprints (`workflows/blueprints`, currently 66 blueprint files)
- Production config mirrors (`config/systemd-live`, `config/nginx-live`, `config/openwebui-local-config`, `config/ops`, `config/bin`)
- Audit manifests and checksums (`config/manifests/*.txt`)

## 2) Ports And Routing

Default host ports:

- `80` -> `nginx`
- `3000` -> `open-webui` (container `8080`)
- `11434` -> `ollama`
- `8187` -> `comfyui-flux`
- `8188` -> `comfyui-wan`

Nginx route behavior:

- `/` -> Open WebUI
- `/comfyui` -> HTTP redirect to `/comfyui/`
- `/comfyui/*` -> proxied to FLUX ComfyUI (`8187`)

The redirect is intentionally enforced to prevent the historical `/comfyui` 404 regression.

## 3) Host Requirements

- Linux host
- Docker Engine + Compose (`docker compose` or `docker-compose`)
- NVIDIA driver + NVIDIA Container Toolkit for real FLUX/WAN generation
- Recommended free disk: `>120G` (bundle + image layers + generated outputs)

## 4) Quick Start

```bash
cd /home/chris/hrm_flashrenderer/mag-portable-stack
export DOCKER_RUNTIME=nvidia
./scripts/stack-up.sh
./scripts/health.sh
```

Run full regression/E2E suite:

```bash
./tests/full-e2e.sh
```

If ports are occupied:

```bash
export HOST_HTTP_PORT=18080
export HOST_OPENWEBUI_PORT=13000
export HOST_OLLAMA_PORT=11444
export HOST_COMFY_FLUX_PORT=18187
export HOST_COMFY_WAN_PORT=18188
```

Stop stack:

```bash
./scripts/stack-down.sh
```

## 5) Sync From Production MAG

Default source: `chris@mag.tail3e02ae.ts.net`

Full sync (default, includes all ComfyUI models + full Ollama store):

```bash
MAG_HOST=mag.tail3e02ae.ts.net MAG_USER=chris ./scripts/sync-from-mag.sh --full
```

Minimal sync (only required FLUX/WAN test models):

```bash
MAG_HOST=mag.tail3e02ae.ts.net MAG_USER=chris ./scripts/sync-from-mag.sh --minimal-models
```

Protected sync (root-owned files via sudo tar; requires password env):

```bash
export MAG_HOST=mag.tail3e02ae.ts.net
export MAG_USER=chris
export MAG_SUDO_PASSWORD='...'
./scripts/sync-protected-from-mag.sh
```

## 6) Verification And Regression

Targeted `/comfyui` route regression check:

```bash
./tests/regression-comfyui-route.sh http://127.0.0.1
```

What `tests/full-e2e.sh` verifies:

- Compose containers up and running
- Ollama API health
- Open WebUI API health
- ComfyUI FLUX/WAN queue API health
- `/comfyui` redirect + path routing regression
- FLUX image generation execution
- WAN video generation execution
- GPU snapshot via `nvidia-smi`

## 7) Transfer Packaging

Create compressed transfer artifact:

```bash
./scripts/package-bundle.sh
```

This writes `dist/mag-portable-stack-<timestamp>.tar.zst`.

## 8) GitHub Push Notes

- This is a very large bundle (models + data). Plain GitHub push usually fails without LFS.
- Use Git LFS or object storage for large folders (`models/`, `open-webui-data/`).
- If pushing to any shared remote, scrub sensitive material first:

```bash
./scripts/sanitize-for-git.sh
```

Also review `config/nginx-live/tailscale/` and other mirrored production config before publishing.

## 9) Reference Docs

- `docs/ARCHITECTURE.md`
- `docs/MODEL_MANIFEST.md`
- `docs/FULL_CAPTURE.md`
- `docs/TESTING.md`
