# Testing Guide

This bundle has two primary test scripts under `tests/`.

## 1) Route Regression Test

Script: `tests/regression-comfyui-route.sh`

Purpose:

- Prevent recurrence of `/comfyui` path breakage (historical 404 regression)
- Assert redirect and API availability through nginx

Checks:

- `/comfyui` returns `301`/`302` and points to `/comfyui/`
- `/comfyui/` returns HTML with ComfyUI title
- `/comfyui/system_stats` returns JSON containing `comfyui_version`
- `/comfyui/queue` returns queue JSON markers

Run:

```bash
./tests/regression-comfyui-route.sh http://127.0.0.1
```

## 2) Full End-To-End Test

Script: `tests/full-e2e.sh`

Purpose:

- Validate full stack behavior from infra health to real generation tasks

Checks:

1. Compose services are running
2. Ollama health (`/api/tags`)
3. Open WebUI health (`/api/version`)
4. ComfyUI FLUX queue health
5. ComfyUI WAN queue health
6. Route regression test execution
7. FLUX image generation execution path
8. WAN video generation execution path
9. GPU snapshot (`nvidia-smi`)

Run:

```bash
./tests/full-e2e.sh
```

## Required Runtime Conditions

- Containers started (`./scripts/stack-up.sh`)
- Reachable host ports (or overridden with `HOST_*` env vars)
- For full FLUX/WAN generation: working NVIDIA runtime and GPU driver

## Practical Notes

- If GPU runtime is unavailable, health checks may pass while FLUX/WAN generation fails.
- `full-e2e.sh` is strict and exits non-zero when any check fails.
- For fast route-only validation, use the dedicated regression script.
