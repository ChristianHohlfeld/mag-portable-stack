#!/usr/bin/env bash
# Comprehensive end-to-end test script
# Tests: services, ollama, ComfyUI FLUX, ComfyUI WAN, DB integrity
# Usage: bash /home/chris/test-workflow.sh

set -euo pipefail
PASS=0 FAIL=0

pass() { PASS=$((PASS+1)); echo "  ✅ PASS: $1"; }
fail() { FAIL=$((FAIL+1)); echo "  ❌ FAIL: $1"; }
check() { if [ "$1" -eq 0 ]; then pass "$2"; else fail "$2"; fi; }

echo "============================================"
echo "  End-to-End Workflow Test Suite"
echo "  $(date)"
echo "============================================"
echo

echo "--- 1. Service Health (systemd) ---"
for svc in ollama open-webui comfyui-gpu2; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        pass "$svc service (systemd)"
    else
        fail "$svc service (systemd)"
    fi
done
echo

echo "--- 2. HTTP Health Checks ---"
for pair in "Ollama:11434/api/tags" "ComfyUI-FLUX:8190/queue" "ComfyUI-WAN:8189/queue" "open-webui:3000/api/version"; do
    name="${pair%%:*}"
    port="${pair##*:}"
    port="${port#*:}"
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port" 2>/dev/null || echo "000")
    [ "$code" = "200" ] && pass "$name (:$port)" || fail "$name (:$port) returned $code"
done
echo

echo "--- 3. Ollama Model Check ---"
MODEL_INFO=$(ollama ps 2>/dev/null || true)
if echo "$MODEL_INFO" | grep -q "qwen3-fast"; then
    MODEL_SIZE=$(echo "$MODEL_INFO" | grep qwen3-fast | awk '{print $3}')
    MODEL_PCT=$(echo "$MODEL_INFO" | grep qwen3-fast | awk '{print $5}')
    pass "qwen3-fast:latest loaded ($MODEL_SIZE, $MODEL_PCT GPU)"
else
    fail "qwen3-fast:latest NOT loaded"
fi
echo

echo "--- 4. Ollama Tool-Calling Test ---"
TOOL_RESP=$(CUDA_VISIBLE_DEVICES=0 ollama run qwen3-fast:latest \
  'You have a tool called generate_flux_image. Call it with prompt="a test" steps=2. Return only the function name.' \
  2>/dev/null | head -5 || true)
if echo "$TOOL_RESP" | grep -qi "generate_flux_image"; then
    pass "Model recognized tool name"
else
    fail "Model did not mention generate_flux_image (got: $(echo "$TOOL_RESP" | head -1))"
fi
echo

echo "--- 5. ComfyUI FLUX Image Generation ---"
FLUX_RES=$(python3 -c "
import asyncio, aiohttp, time, random
async def t():
    ts = int(time.time() * 1000000)
    seed = random.randint(0,999999)
    async with aiohttp.ClientSession() as s:
        wf = {
            '1': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': 'flux1-schnell-Q3_K_S.gguf'}},
            '2': {'class_type': 'DualCLIPLoaderGGUF', 'inputs': {'clip_name1': 'clip_l.safetensors', 'clip_name2': 't5xxl_fp8_e4m3fn.safetensors', 'type': 'flux'}},
            '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'ae.safetensors'}},
            '4': {'class_type': 'CLIPTextEncodeFlux', 'inputs': {'clip': ['2', 0], 'clip_l': f'flux_{ts}', 't5xxl': f'flux_{ts}', 'guidance': 3.5}},
            '5': {'class_type': 'CLIPTextEncodeFlux', 'inputs': {'clip': ['2', 0], 'clip_l': '', 't5xxl': '', 'guidance': 3.5}},
            '6': {'class_type': 'FluxGuidance', 'inputs': {'conditioning': ['4', 0], 'guidance': 3.5}},
            '7': {'class_type': 'FluxDisableGuidance', 'inputs': {'conditioning': ['5', 0]}},
            '8': {'class_type': 'EmptyFlux2LatentImage', 'inputs': {'width': 512, 'height': 512, 'batch_size': 1}},
            '9': {'class_type': 'KSampler', 'inputs': {'model': ['1', 0], 'seed': seed, 'steps': 4, 'cfg': 1, 'sampler_name': 'euler', 'scheduler': 'simple', 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['8', 0], 'denoise': 1}},
            '10': {'class_type': 'VAEDecode', 'inputs': {'samples': ['9', 0], 'vae': ['3', 0]}},
            '11': {'class_type': 'SaveImage', 'inputs': {'images': ['10', 0], 'filename_prefix': 'test_flux'}}
        }
        for attempt in range(3):
            async with s.post('http://127.0.0.1:8190/prompt', json={'prompt': wf}) as r:
                if r.status >= 400:
                    if attempt < 2:
                        await asyncio.sleep(2)
                        continue
                    print('ERR:queue ' + (await r.text())[:200])
                    return
                j = await r.json()
                pid = j.get('prompt_id','')
                break
        for _ in range(60):
            await asyncio.sleep(1)
            async with s.get(f'http://127.0.0.1:8190/history/{pid}') as r:
                h = await r.json()
            if pid in h:
                st = h[pid].get('status',{})
                outputs = h[pid].get('outputs') or {}
                if st.get('status_str') in ('error','failed'):
                    for m in st.get('messages',[]):
                        if m[0]=='execution_error':
                            print('ERR:'+m[1].get('exception_message',''))
                            return
                    print('ERR:unknown failure')
                    return
                if outputs:
                    for no, o in outputs.items():
                        if isinstance(o,dict):
                            for v in o.values():
                                if isinstance(v,list):
                                    for item in v:
                                        if isinstance(item,dict) and 'filename' in item:
                                            print('OK:' + item['filename'])
                                            return
                print('WAIT')
                continue
            print('WAIT')
        print('TIMEOUT')
asyncio.run(t())" 2>&1)
if echo "$FLUX_RES" | grep -q "^OK:"; then
    pass "FLUX generated image: $(echo "$FLUX_RES" | grep "^OK:" | head -1)"
else
    fail "FLUX generation failed: $(echo "$FLUX_RES" | tail -3)"
fi
echo

echo "--- 6. ComfyUI WAN Video Generation (quick test, 5 steps) ---"
WAN_RES=$(python3 -c "
import asyncio, aiohttp, time, random
async def t():
    ts = int(time.time() * 1000000)
    seed = random.randint(0,999999)
    async with aiohttp.ClientSession() as s:
        wf = {
            '37': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'wan2.2_ti2v_5B_fp16.safetensors', 'weight_dtype': 'default'}},
            '38': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': 'umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'type': 'wan', 'device': 'default'}},
            '39': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'wan2.2_vae.safetensors'}},
            '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': f'wan_{ts}', 'clip': ['38', 0]}},
            '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'bad', 'clip': ['38', 0]}},
            '48': {'class_type': 'ModelSamplingSD3', 'inputs': {'model': ['37', 0], 'shift': 8}},
            '55': {'class_type': 'Wan22ImageToVideoLatent', 'inputs': {'vae': ['39', 0], 'width': 640, 'height': 352, 'length': 25, 'batch_size': 1}},
            '3': {'class_type': 'KSampler', 'inputs': {'model': ['48', 0], 'seed': seed, 'steps': 5, 'cfg': 5, 'sampler_name': 'uni_pc', 'scheduler': 'simple', 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['55', 0], 'denoise': 1}},
            '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['39', 0]}},
            '57': {'class_type': 'CreateVideo', 'inputs': {'images': ['8', 0], 'fps': 24}},
            '58': {'class_type': 'SaveVideo', 'inputs': {'video': ['57', 0], 'filename_prefix': 'test_wan', 'format': 'mp4', 'codec': 'h264'}}
        }
        for attempt in range(3):
            async with s.post('http://127.0.0.1:8189/prompt', json={'prompt': wf}) as r:
                if r.status >= 400:
                    if attempt < 2:
                        await asyncio.sleep(2)
                        continue
                    print('ERR:queue ' + (await r.text())[:200])
                    return
                j = await r.json()
                pid = j.get('prompt_id','')
                break
        for _ in range(90):
            await asyncio.sleep(1)
            async with s.get(f'http://127.0.0.1:8189/history/{pid}') as r:
                h = await r.json()
            if pid in h:
                st = h[pid].get('status',{})
                outputs = h[pid].get('outputs') or {}
                if st.get('status_str')=='error':
                    for m in st.get('messages',[]):
                        if m[0]=='execution_error':
                            print('ERR:'+m[1].get('exception_message',''))
                            return
                if outputs:
                    for no, o in outputs.items():
                        if isinstance(o,dict):
                            for v in o.values():
                                if isinstance(v,list):
                                    for item in v:
                                        if isinstance(item,dict) and 'filename' in item:
                                            print('OK:'+item['filename'])
                                            return
                print('WAIT')
                continue
            print('WAIT')
        print('TIMEOUT')
asyncio.run(t())" 2>&1)
if echo "$WAN_RES" | grep -q "^OK:"; then
    pass "WAN generated video: $(echo "$WAN_RES" | grep "^OK:" | head -1)"
else
    fail "WAN generation failed: $(echo "$WAN_RES" | tail -3)"
fi
echo

echo "--- 7. open-webui API Check ---"
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6ImY1YjY2ZGRlLWY0NWEtNDBjYy04NGFiLTExNTIwNTYxZWVjYyIsImV4cCI6MTc4MTA2MjA2NCwianRpIjoiYmE0NzhkMDQtNDk1MC00Yjk5LWFjYmMtN2IzYTFiNDgyNjQ5IiwiaWF0IjoxNzc4NjQyODY0fQ.KrdOexI-CtH5fPAOzakURr5Rc9A0Ty8Hs0BaOKuwuJ0"
API_CHECK=$(curl -s -H "Authorization: Bearer $TOKEN" -w "\n%{http_code}" http://127.0.0.1:3000/api/models 2>/dev/null)
API_CODE=$(echo "$API_CHECK" | tail -1)
API_BODY=$(echo "$API_CHECK" | head -n-1)
if [ "$API_CODE" = "200" ]; then
    MODELS_CNT=$(echo "$API_BODY" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null || echo "?")
    pass "open-webui API: $MODELS_CNT models available"
else
    fail "open-webui API returned $API_CODE"
fi
echo

echo "--- 8. DB Tool Code Integrity ---"
DB_CHECK=$(python3 -c "
import sqlite3
conn = sqlite3.connect('/home/chris/.open-webui/webui.db')
cur = conn.cursor()
cur.execute('SELECT id, content FROM tool')
issues = []
for r in cur.fetchall():
    tid = r[0]
    c = r[1]
    if '__chat_id__' in c or '__message_id__' in c:
        issues.append(f'{tid}: still has __chat_id__/__message_id__ references')
    if 'chat_id' not in c and 'message_id' not in c:
        issues.append(f'{tid}: missing chat_id/message_id extraction')
    if 'upload_image' not in c:
        issues.append(f'{tid}: missing upload_image call')
conn.close()
if issues:
    print('ISSUES: ' + '; '.join(issues))
else:
    print('OK')
" 2>&1)
if echo "$DB_CHECK" | grep -q "^OK"; then
    pass "DB tool code integrity check"
else
    fail "DB tool issues: $DB_CHECK"
fi
echo

echo "--- 9. GPU Memory Check ---"
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader 2>/dev/null | while IFS=',' read -r idx name used total; do
    used_num=$(echo "$used" | tr -d ' MiB')
    total_num=$(echo "$total" | tr -d ' MiB')
    pct=$((used_num * 100 / total_num))
    if [ "$pct" -gt 95 ]; then
        echo "  ⚠️  GPU${idx} ($name): ${used}/${total} (${pct}%) - HIGH"
    else
        echo "  ✅ GPU${idx} ($name): ${used}/${total} (${pct}%)"
    fi
done
echo

echo "============================================"
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
