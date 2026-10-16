P=${1:-8091}; U=http://127.0.0.1:$P
echo "props:"; curl -s $U/props | python3 -c "import json,sys;d=json.load(sys.stdin);print({k:d.get(k) for k in ['model_path','build_info','total_slots']}, list(d)[:12])"
curl -s $U/v1/models | head -c 200; echo
echo "--- stream, thinking off"
curl -sN $U/v1/chat/completions -H 'content-type: application/json' -d '{"model":"x","stream":true,"max_tokens":40,"messages":[{"role":"user","content":"Sag Hallo in 3 Wörtern."}],"chat_template_kwargs":{"thinking":false}}' | head -c 600; echo
echo "--- stream, thinking on"
curl -sN $U/v1/chat/completions -H 'content-type: application/json' -d '{"model":"x","stream":true,"max_tokens":30,"messages":[{"role":"user","content":"2+2?"}],"chat_template_kwargs":{"thinking":true}}' | grep -o '"reasoning_content":"[^"]*"' | head -5
echo "--- abort test"
timeout 4 curl -sN $U/v1/chat/completions -H 'content-type: application/json' -d '{"model":"x","stream":true,"max_tokens":400,"messages":[{"role":"user","content":"Erzähl eine lange Geschichte."}]}' >/dev/null
sleep 3; curl -s $U/slots 2>/dev/null | head -c 0; curl -s $U/health; echo
echo "--- cache_prompt twice (non-stream)"
for i in 1 2; do curl -s $U/v1/chat/completions -H 'content-type: application/json' -d '{"model":"x","max_tokens":8,"cache_prompt":true,"messages":[{"role":"system","content":"'"$(printf 'Kontext %.0s' $(seq 300))"'"},{"role":"user","content":"ok?"}]}' | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('timings',{}).get('prompt_n'),d.get('timings',{}).get('prompt_ms'),d.get('timings',{}).get('predicted_per_second'), repr(d['choices'][0]['message'].get('content'))[:60])"; done
