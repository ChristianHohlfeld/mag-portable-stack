#!/usr/bin/env bash
set -euo pipefail

PASS=0
FAIL=0

pass() { PASS=$((PASS+1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL+1)); echo "  FAIL: $1"; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OLLAMA_PORT="${HOST_OLLAMA_PORT:-11434}"
OPENWEBUI_PORT="${HOST_OPENWEBUI_PORT:-3000}"
COMFY_FLUX_PORT="${HOST_COMFY_FLUX_PORT:-8187}"
COMFY_WAN_PORT="${HOST_COMFY_WAN_PORT:-8188}"
HTTP_PORT="${HOST_HTTP_PORT:-80}"

docker_compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    return 127
  fi
}

http_check() {
  local name="$1"
  local url="$2"
  local expect="${3:-200}"
  local marker="${4:-}"
  local body
  body="$(mktemp)"
  local code
  code="$(curl -sS -o "$body" -w "%{http_code}" "$url" 2>/dev/null || echo 000)"
  if [ "$code" != "$expect" ]; then
    fail "$name returned HTTP $code (expected $expect)"
    rm -f "$body"
    return
  fi

  if [ -n "$marker" ] && ! grep -q "$marker" "$body"; then
    fail "$name missing marker: $marker"
  else
    pass "$name"
  fi
  rm -f "$body"
}

echo "============================================"
echo "  Portable Stack E2E Test Suite"
echo "  $(date)"
echo "============================================"
echo

echo "--- 1. Container Health ---"
if docker_compose ps --services --filter "status=running" >/tmp/mag_stack_ps.txt 2>/dev/null; then
  running_cnt="$(grep -c . /tmp/mag_stack_ps.txt || true)"
  if [ "$running_cnt" -ge 5 ]; then
    pass "containers running ($running_cnt)"
  else
    fail "expected >=5 running containers, got $running_cnt"
  fi
else
  fail "docker compose ps failed"
fi
echo

echo "--- 2. Local HTTP Health ---"
http_check "Ollama /api/tags" "http://127.0.0.1:${OLLAMA_PORT}/api/tags" "200" '"models"'
http_check "open-webui /api/version" "http://127.0.0.1:${OPENWEBUI_PORT}/api/version" "200" '"version"'
http_check "ComfyUI FLUX /queue" "http://127.0.0.1:${COMFY_FLUX_PORT}/queue" "200" '"queue_running"'
http_check "ComfyUI WAN /queue" "http://127.0.0.1:${COMFY_WAN_PORT}/queue" "200" '"queue_running"'
echo

echo "--- 3. Regression: /comfyui Route ---"
if [ -x ./tests/regression-comfyui-route.sh ]; then
  if ./tests/regression-comfyui-route.sh "http://127.0.0.1:${HTTP_PORT}"; then
    pass "Regression test script"
  else
    fail "Regression test script"
  fi
else
  fail "Missing ./tests/regression-comfyui-route.sh"
fi
echo

echo "--- 4. ComfyUI FLUX Image Generation ---"
FLUX_RES="$(python3 <<PY
import asyncio, aiohttp, random, time

async def main():
    ts = int(time.time() * 1000)
    seed = random.randint(1, 2**31-1)
    wf = {
        '1': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': 'flux1-schnell-Q3_K_S.gguf'}},
        '2': {'class_type': 'DualCLIPLoaderGGUF', 'inputs': {'clip_name1': 'clip_l.safetensors', 'clip_name2': 't5xxl_fp8_e4m3fn.safetensors', 'type': 'flux'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'ae.safetensors'}},
        '4': {'class_type': 'CLIPTextEncodeFlux', 'inputs': {'clip': ['2', 0], 'clip_l': f'regression_flux_{ts}', 't5xxl': f'regression_flux_{ts}', 'guidance': 3.5}},
        '5': {'class_type': 'CLIPTextEncodeFlux', 'inputs': {'clip': ['2', 0], 'clip_l': '', 't5xxl': '', 'guidance': 3.5}},
        '6': {'class_type': 'FluxGuidance', 'inputs': {'conditioning': ['4', 0], 'guidance': 3.5}},
        '7': {'class_type': 'FluxDisableGuidance', 'inputs': {'conditioning': ['5', 0]}},
        '8': {'class_type': 'EmptyFlux2LatentImage', 'inputs': {'width': 512, 'height': 512, 'batch_size': 1}},
        '9': {'class_type': 'KSampler', 'inputs': {'model': ['1', 0], 'seed': seed, 'steps': 4, 'cfg': 1, 'sampler_name': 'euler', 'scheduler': 'simple', 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['8', 0], 'denoise': 1}},
        '10': {'class_type': 'VAEDecode', 'inputs': {'samples': ['9', 0], 'vae': ['3', 0]}},
        '11': {'class_type': 'SaveImage', 'inputs': {'images': ['10', 0], 'filename_prefix': 'regression_flux'}}
    }

    async with aiohttp.ClientSession() as s:
        async with s.post('http://127.0.0.1:${COMFY_FLUX_PORT}/prompt', json={'prompt': wf}) as r:
            if r.status != 200:
                print('ERR:queue_status_%s' % r.status)
                return
            pid = (await r.json()).get('prompt_id')

        for _ in range(120):
            await asyncio.sleep(1)
            async with s.get(f'http://127.0.0.1:${COMFY_FLUX_PORT}/history/{pid}') as r:
                h = await r.json()
            if pid not in h:
                continue

            row = h[pid]
            status = row.get('status', {})
            if status.get('status_str') in ('error', 'failed'):
                print('ERR:execution_failed')
                return

            outputs = row.get('outputs') or {}
            for _, output in outputs.items():
                if not isinstance(output, dict):
                    continue
                for _, val in output.items():
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict) and item.get('filename'):
                                print('OK:%s' % item['filename'])
                                return

        print('ERR:timeout')

asyncio.run(main())
PY
)"
if echo "$FLUX_RES" | grep -q '^OK:'; then
  pass "FLUX generated $(echo "$FLUX_RES" | head -n1)"
else
  fail "FLUX generation failed: $(echo "$FLUX_RES" | tail -n1)"
fi
echo

echo "--- 5. ComfyUI WAN Video Generation ---"
WAN_RES="$(python3 <<PY
import asyncio, aiohttp, random, time

async def main():
    ts = int(time.time() * 1000)
    seed = random.randint(1, 2**31-1)
    wf = {
        '37': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'wan2.2_ti2v_5B_fp16.safetensors', 'weight_dtype': 'default'}},
        '38': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': 'umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'type': 'wan', 'device': 'default'}},
        '39': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'wan2.2_vae.safetensors'}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': f'regression_wan_{ts}', 'clip': ['38', 0]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'bad quality', 'clip': ['38', 0]}},
        '48': {'class_type': 'ModelSamplingSD3', 'inputs': {'model': ['37', 0], 'shift': 8}},
        '55': {'class_type': 'Wan22ImageToVideoLatent', 'inputs': {'vae': ['39', 0], 'width': 640, 'height': 352, 'length': 25, 'batch_size': 1}},
        '3': {'class_type': 'KSampler', 'inputs': {'model': ['48', 0], 'seed': seed, 'steps': 5, 'cfg': 5, 'sampler_name': 'uni_pc', 'scheduler': 'simple', 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['55', 0], 'denoise': 1}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['39', 0]}},
        '57': {'class_type': 'CreateVideo', 'inputs': {'images': ['8', 0], 'fps': 24}},
        '58': {'class_type': 'SaveVideo', 'inputs': {'video': ['57', 0], 'filename_prefix': 'regression_wan', 'format': 'mp4', 'codec': 'h264'}}
    }

    async with aiohttp.ClientSession() as s:
        async with s.post('http://127.0.0.1:${COMFY_WAN_PORT}/prompt', json={'prompt': wf}) as r:
            if r.status != 200:
                print('ERR:queue_status_%s' % r.status)
                return
            pid = (await r.json()).get('prompt_id')

        for _ in range(180):
            await asyncio.sleep(1)
            async with s.get(f'http://127.0.0.1:${COMFY_WAN_PORT}/history/{pid}') as r:
                h = await r.json()
            if pid not in h:
                continue

            row = h[pid]
            status = row.get('status', {})
            if status.get('status_str') in ('error', 'failed'):
                print('ERR:execution_failed')
                return

            outputs = row.get('outputs') or {}
            for _, output in outputs.items():
                if not isinstance(output, dict):
                    continue
                for _, val in output.items():
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict) and item.get('filename'):
                                print('OK:%s' % item['filename'])
                                return

        print('ERR:timeout')

asyncio.run(main())
PY
)"
if echo "$WAN_RES" | grep -q '^OK:'; then
  pass "WAN generated $(echo "$WAN_RES" | head -n1)"
else
  fail "WAN generation failed: $(echo "$WAN_RES" | tail -n1)"
fi
echo

echo "--- 6. GPU Snapshot ---"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader | sed 's/^/  /'
  pass "nvidia-smi snapshot"
else
  fail "nvidia-smi not available"
fi
echo

echo "============================================"
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================"

[ "$FAIL" -eq 0 ]
