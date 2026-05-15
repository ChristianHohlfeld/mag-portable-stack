#!/usr/bin/env bash
set -Eeuo pipefail

MODEL_ID="hf.co/unsloth/Qwen3-30B-A3B-GGUF:UD-IQ2_XXS"
DB="/home/chris/.open-webui/webui.db"
FIX="/home/chris/ops/scripts/openwebui_media_fix.py"
E2E="/home/chris/ops/tests/openwebui_media_e2e_test.py"
RUNTIME_PATCH="/home/chris/ops/scripts/openwebui_tool_fallback_patch.py"

need_fix=0

check="$(python3 - <<'PY'
import json, sqlite3
db="/home/chris/.open-webui/webui.db"
mid="hf.co/unsloth/Qwen3-30B-A3B-GGUF:UD-IQ2_XXS"
con=sqlite3.connect(db); cur=con.cursor()
cfg=json.loads(cur.execute("select data from config where id=1").fetchone()[0])
m=cur.execute("select meta,params from model where id=?",(mid,)).fetchone()
chat_rows=cur.execute("select chat from chat order by updated_at desc limit 50").fetchall()
con.close()
ok=1
if not m: ok=0
else:
    meta=json.loads(m[0] or "{}")
    params=json.loads(m[1] or "{}")
    dfs=meta.get("defaultFeatureIds") or []
    ok &= (cfg.get("image_generation",{}).get("comfyui",{}).get("base_url")=="http://127.0.0.1:8187")
    ok &= (cfg.get("rag",{}).get("web",{}).get("search",{}).get("enable") is False)
    ok &= (cfg.get("auth",{}).get("enable_api_keys") is True)
    ok &= (cfg.get("ui",{}).get("default_models")==mid)
    ok &= (cfg.get("ui",{}).get("default_pinned_models")==mid)
    ok &= (cfg.get("ui",{}).get("model_order_list")==[mid])
    ok &= (meta.get("capabilities",{}).get("web_search") is False)
    ok &= (meta.get("capabilities",{}).get("image_generation") is True)
    ok &= (meta.get("capabilities",{}).get("builtin_tools") is False)
    ok &= ("image_generation" in dfs)
    ok &= (params.get("function_calling")=="native")
    ok &= (params.get("think") is False)
for (chat_raw,) in chat_rows:
    try:
        c=json.loads(chat_raw or "{}")
    except Exception:
        continue
    models=c.get("models") or []
    m0=models[0] if models else None
    if m0!=mid:
        continue
    feats=c.get("features") or {}
    tools=c.get("toolIds") or []
    p=c.get("params") or {}
    ok &= (tools == ["comfy_flux_image_generator","comfy_wan_video_generator_tool"])
    ok &= (feats.get("builtin_tools")==0)
    ok &= (feats.get("image_generation")==1)
    ok &= (p.get("function_calling")=="native")
print("ok" if ok else "fix")
PY
)"

if [[ "$check" != "ok" ]]; then
  need_fix=1
fi

if [[ "$need_fix" -eq 1 ]]; then
  echo "[guard] drift detected -> applying fix"
  "$FIX"
  sudo systemctl restart open-webui
else
  echo "[guard] config is clean"
fi

if [[ -f "$RUNTIME_PATCH" ]]; then
  patch_out="$(python3 "$RUNTIME_PATCH" || true)"
  if [[ "$patch_out" == "patched" ]]; then
    echo "[guard] middleware runtime patch applied -> restart open-webui"
    sudo systemctl restart open-webui
  fi
fi

svc_states="$(systemctl is-active open-webui ollama comfyui comfyui-image nginx || true)"
if echo "$svc_states" | grep -qv '^active$'; then
  echo "[guard] one or more services not active"
  echo "$svc_states"
  exit 1
fi

echo "[guard] services active"

# Optional lightweight API checks
curl -fsS http://127.0.0.1:8187/system_stats >/dev/null
curl -fsS http://127.0.0.1:8188/system_stats >/dev/null
curl -fsS http://127.0.0.1:11434/api/tags >/dev/null

echo "[guard] smoke ok"
