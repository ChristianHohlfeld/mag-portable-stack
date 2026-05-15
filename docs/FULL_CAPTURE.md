# Full Capture Mapping

This document maps production source paths to bundle destinations.

Default source host: `chris@mag.tail3e02ae.ts.net`

## Synced By `scripts/sync-from-mag.sh`

| Production Source | Bundle Destination | Notes |
|---|---|---|
| `/home/chris/ComfyUI/` | `comfyui/` | Excludes `venv`, `.git`, and `models` |
| `/home/chris/ComfyUI/blueprints/` | `workflows/blueprints/` | Workflow blueprints |
| `/home/chris/ComfyUI/user/comfyui-gpu1.db` | `config/comfyui-gpu1.db` | WAN DB |
| `/home/chris/ComfyUI/user/comfyui-gpu2.db` | `config/comfyui-gpu2.db` | FLUX DB |
| `/home/chris/ComfyUI/user/comfyui.db` | `config/comfyui.db` | Generic DB |
| `/etc/nginx/sites-available/openwebui-tailnet` | `nginx/openwebui-tailnet.conf` | Runtime nginx reference |
| `/home/chris/.open-webui/` | `open-webui-data/.open-webui/` | Open WebUI data snapshot |
| `/home/chris/openwebui-local-config/` | `config/openwebui-local-config/` | Local Open WebUI helpers |
| `/home/chris/ops/` | `config/ops/` | Production ops scripts/tests |
| `/home/chris/.local/bin/` | `config/bin/` | Local executable scripts |
| `/etc/systemd/system/` | `config/systemd-live/` | Systemd unit snapshot |
| `/etc/nginx/` | `config/nginx-live/` | Nginx live tree snapshot |
| `/home/chris/ComfyUI/models/` (`--full`) | `models/` | Full ComfyUI model mirror |
| `/usr/share/ollama/.ollama/` (`--full`) | `models/ollama/.ollama/` | Full Ollama store |

## Synced By `scripts/sync-protected-from-mag.sh`

Archive-based sudo export of root-owned files:

- `/etc/nginx/tailscale/*`
- `/etc/nginx/sites-available/openwebui-tailnet`
- `/etc/systemd/system/comfyui-image.service`
- `/etc/systemd/system/comfyui.service`
- `/etc/systemd/system/open-webui.service`
- `/etc/systemd/system/ollama.service`
- `/etc/systemd/system/set-gpu-compute-mode.service`
- `/usr/share/ollama/.ollama`

Extract destination:

- `config/protected/`

## Included In Docker Runtime

Files used directly by `docker-compose.yml` runtime:

- `models/`
- `workflows/blueprints/`
- `config/`
- `open-webui-data/.open-webui/`
- `comfyui/input/`
- `comfyui/output/`
- `nginx/default.conf`

## Auditable Evidence

Generated manifests in `config/manifests/` include:

- running services (`services_running.txt`)
- listening ports (`ports.txt`)
- package snapshots (`apt_packages.txt`, `pip` freeze files)
- hash manifests (`model_sha256.txt`, `infra_sha256.txt`)
- capture timestamp (`manifest_generated_at.txt`)
