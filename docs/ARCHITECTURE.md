# Architecture

## Core Container Topology

`docker-compose.yml` defines five services:

1. `nginx` (`HOST_HTTP_PORT`, default `80`)
2. `open-webui` (`HOST_OPENWEBUI_PORT`, default `3000`, container `8080`)
3. `ollama` (`HOST_OLLAMA_PORT`, default `11434`)
4. `comfyui-flux` (`HOST_COMFY_FLUX_PORT`, default `8187`)
5. `comfyui-wan` (`HOST_COMFY_WAN_PORT`, default `8188`)

## Routing Contract (Regression-Critical)

From `nginx/default.conf`:

- `GET /` -> proxied to `open-webui:8080`
- `GET /comfyui` -> forced redirect `301 /comfyui/`
- `GET /comfyui/*` -> rewritten and proxied to `comfyui-flux:8187`

This redirect+rewrite behavior is the fix guard for the historical `/comfyui` 404 regression.

## ComfyUI Runtime

- Both ComfyUI services are built from `comfyui/Dockerfile`
- Entrypoint: `comfyui/entrypoint-comfyui.sh`
- FLUX starts with DB: `/data/comfyui-gpu2.db` on port `8187`
- WAN starts with DB: `/data/comfyui-gpu1.db` on port `8188`

## Volume Wiring

- `./models` -> `/opt/comfyui/models` (both ComfyUI services)
- `./workflows/blueprints` -> `/opt/comfyui/blueprints` (both ComfyUI services)
- `./config` -> `/data` (ComfyUI DB and mirrored config access)
- `./comfyui/output` -> `/opt/comfyui/output`
- `./comfyui/input` -> `/opt/comfyui/input`
- `./open-webui-data/.open-webui` -> `/app/backend/data` (Open WebUI)
- `./models/ollama/.ollama` -> `/root/.ollama` (Ollama)

## Production Snapshot Mirrors

The bundle also stores non-containerized production references:

- `config/systemd-live/` (systemd unit snapshot)
- `config/nginx-live/` (nginx live config snapshot)
- `config/openwebui-local-config/`
- `config/ops/`
- `config/bin/`
- `config/manifests/` (ports/services/packages/checksums)

These mirrors are for traceability, recovery, and diffing; the portable runtime itself is Docker-based.

## Test Surface

- `tests/regression-comfyui-route.sh`: route-level regression gate for `/comfyui`
- `tests/full-e2e.sh`: stack health, API checks, route regression, FLUX generation, WAN generation, GPU snapshot
