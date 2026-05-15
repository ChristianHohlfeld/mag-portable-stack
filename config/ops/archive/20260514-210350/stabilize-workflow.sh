#!/usr/bin/env bash
# Idempotent stabilization script
# Fixes known issues and ensures all services are correctly configured
# Safe to run on every boot
# Usage: bash /home/chris/stabilize-workflow.sh

set -euo pipefail

echo "=== Stabilizing Workflow $(date) ==="

# --- 1. Ensure services are enabled and running ---
for svc in ollama open-webui comfyui-gpu2 comfyui-preempt-proxy ollama-preempt-proxy; do
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        echo "  ✓ $svc enabled"
    else
        echo "  ⚠ $svc NOT enabled (ok if intended)"
    fi
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "  ✓ $svc running"
    else
        echo "  ⚠ $svc NOT running, starting..."
        sudo systemctl start "$svc" 2>/dev/null || echo "  ✗ Failed to start $svc"
    fi
done

# Check ComfyUI WAN (port 8189) - may be bare process not systemd
if ! curl -s -o /dev/null http://127.0.0.1:8189/queue 2>/dev/null; then
    echo "  ⚠ ComfyUI WAN (8189) not responding"
    ps aux | grep -q "[p]ort 8189" && echo "  ✓ WAN bare process exists" || echo "  ✗ WAN process not found (start manually)"
fi

# --- 2. Wait for ollama ---
echo "  Waiting for ollama..."
for i in $(seq 1 30); do
    if curl -s -o /dev/null -w "" http://127.0.0.1:11434/api/tags 2>/dev/null; then
        echo "  ✓ ollama ready"
        break
    fi
    sleep 1
done

# --- 3. Stop any wrong models, load correct one ---
CURRENT=$(ollama ps 2>/dev/null | tail -n +2 | awk '{print $1}')
WRONG_MODELS="qwen3.6:35b-a3b nemo:13b default:latest fredrezones55/Qwen3.6-35B-A3B-APEX:Compact mirage335/Nemotron-3-Nano-30B-A3B-virtuoso:latest"
for m in $CURRENT; do
    for wm in $WRONG_MODELS; do
        if [ "$m" = "$wm" ]; then
            echo "  ✗ Wrong model $m loaded, stopping..."
            ollama stop "$m" 2>/dev/null || true
            break
        fi
    done
done

# Check if correct model is loaded
if echo "$CURRENT" | grep -q "qwen3-fast"; then
    echo "  ✓ qwen3-fast:latest already loaded"
else
    echo "  Loading qwen3-fast:latest..."
    CUDA_VISIBLE_DEVICES=0 ollama run qwen3-fast:latest "ok" &>/tmp/ollama-load.log &
    for i in $(seq 1 60); do
        sleep 1
        if ollama ps 2>/dev/null | grep -q "qwen3-fast"; then
            echo "  ✓ qwen3-fast:latest loaded"
            break
        fi
    done
fi

# --- 4. Fix DB issues ---
python3 <<'PYEOF'
import sqlite3, json

conn = sqlite3.connect('/home/chris/.open-webui/webui.db')
cur = conn.cursor()
fixed = []

# Fix tool code: __chat_id__/__message_id__ → chat_id/message_id
for tid in ['comfy_flux_image_generator', 'comfy_wan_video_generator_tool']:
    cur.execute('SELECT content FROM tool WHERE id=?', (tid,))
    r = cur.fetchone()
    if not r:
        continue
    c = r[0]
    new_c = c.replace('__chat_id__, __message_id__', 'chat_id, message_id')
    if new_c != c:
        cur.execute('UPDATE tool SET content=? WHERE id=?', (new_c, tid))
        fixed.append(f'fixed {tid} var names')

# Ensure user default model is qwen3-fast:latest
cur.execute('SELECT settings FROM "user" WHERE id="f5b66dde-f45a-40cc-84ab-11520561eecc"')
r = cur.fetchone()
if r:
    s = json.loads(r[0])
    models = s.get('ui', {}).get('models', [])
    if models != ['qwen3-fast:latest']:
        s['ui']['models'] = ['qwen3-fast:latest']
        cur.execute('UPDATE "user" SET settings=? WHERE id="f5b66dde-f45a-40cc-84ab-11520561eecc"', (json.dumps(s),))
        fixed.append('fixed user default model')

# Mark oversized models as hidden
BIG_MODELS = [
    'nemo:13b', 'default:latest',
    'fredrezones55/Qwen3.6-35B-A3B-APEX:Compact',
    'mirage335/Nemotron-3-Nano-30B-A3B-virtuoso:latest',
    'qwen3.6:35b-a3b', 'qwen3.6:35b', 'qwen3.6:27b',
    'hf.co/unsloth/Qwen3-30B-A3B-GGUF:Q3_K_M',
    'hf.co/unsloth/Qwen3-30B-A3B-GGUF:Q2_K',
    'hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ1_M',
    'hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q2_K_XL',
    'qwen3.6:35b-a3b-ctx',
]
for mid in BIG_MODELS:
    cur.execute('SELECT meta FROM model WHERE id=?', (mid,))
    r = cur.fetchone()
    if r:
        m = json.loads(r[0])
        if not m.get('hidden'):
            m['hidden'] = True
            cur.execute('UPDATE model SET meta=? WHERE id=?', (json.dumps(m), mid))
            fixed.append(f'hidden {mid}')

# Ensure qwen3-fast has toolIds and function_calling
cur.execute('SELECT meta, params FROM model WHERE id="qwen3-fast:latest"')
r = cur.fetchone()
if r:
    m = json.loads(r[0])
    p = json.loads(r[1]) if r[1] else {}
    tool_ids = m.get('toolIds', [])
    needed_tools = ['server:opencode', 'comfy_flux_image_generator', 'comfy_wan_video_generator_tool']
    changed = False
    for t in needed_tools:
        if t not in tool_ids:
            tool_ids.append(t)
            changed = True
    if changed:
        m['toolIds'] = tool_ids
        cur.execute('UPDATE model SET meta=? WHERE id="qwen3-fast:latest"', (json.dumps(m),))
        fixed.append('fixed qwen3-fast toolIds')
    if p.get('function_calling') != 'native':
        p['function_calling'] = 'native'
        p['num_ctx'] = 8192
        cur.execute('UPDATE model SET params=? WHERE id="qwen3-fast:latest"', (json.dumps(p),))
        fixed.append('fixed qwen3-fast function_calling')

conn.commit()
conn.close()
if fixed:
    for f in fixed:
        print(f'  ✓ {f}')
else:
    print('  ✓ DB already consistent')
PYEOF

# --- 5. Run patch (open-webui hotfixes) ---
if [ -f /home/chris/patch-open-webui.py ]; then
    python3 /home/chris/patch-open-webui.py 2>&1 | head -5
    echo "  ✓ open-webui patcher ran"
fi

# --- 6. Final status ---
echo ""
echo "=== Status ==="
ollama ps 2>/dev/null | head -5
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader 2>/dev/null
echo "=== Stabilization Complete ==="
