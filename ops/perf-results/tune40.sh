#!/bin/bash
# mag-stack-4.0 speed step: ONE candidate restart (+rollback only if not faster).
# Waits for >=15 min idle room (no turn, no queue) before touching mag-llm.
set -uo pipefail
LOG=/home/chris/bneck/v38/tune40.log; exec >>"$LOG" 2>&1
echo "== $(date -Is) tune40 start, waiting for 15 min idle"
BK=/home/chris/bneck/v38/backup-$(date +%H%M); mkdir -p $BK
cp -a /etc/systemd/system/mag-llm.service /etc/mag-llm/llm.env $BK/
idle=0
while (( idle < 30 )); do
  if [ "$(python3 /tmp/isidle.py 2>/dev/null)" = 1 ] && ! pgrep -f "shellretest.sh" >/dev/null; then idle=$((idle+1)); else idle=0; fi
  sleep 30
done
echo "== $(date -Is) idle 15 min -> baseline bench (no restart)"
bench() { runuser -u magbot -- python3 -I - <<'PY'
import json,urllib.request,random
def q(msg,n):
    b={"model":"deepseek-v4-flash-0731","messages":[{"role":"user","content":msg}],"max_tokens":n,"temperature":0,"cache_prompt":False,"chat_template_kwargs":{"enable_thinking":False}}
    d=json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions",json.dumps(b).encode(),{"Content-Type":"application/json","X-Mag-Bot":"bench-4.0"}),timeout=1200))
    return d["timings"],d["choices"][0]["message"].get("content") or ""
t,c=q("Antworte nur mit der Zahl: Was ist 17+25?",16); assert "42" in c, c
pp=[];tg=[]
for i in range(2):
    t,_=q(f"[{random.random()}] "+"Die Wohnung in Konstanz hat drei Zimmer und einen Balkon. "*90+"\nFasse in einem Wort zusammen.",4); pp.append(t["prompt_per_second"])
    t,_=q(f"[{random.random()}] Schreibe einen ausführlichen Text über die Geschichte des Bodensees.",192); tg.append(t["predicted_per_second"])
print(f"{sum(pp)/2:.2f} {sum(tg)/2:.2f}")
PY
}
read BPP BTG < <(bench) || { echo "baseline bench failed"; exit 1; }
echo "BASE pp=$BPP tg=$BTG"
[ "$(python3 /tmp/isidle.py)" = 1 ] || { echo "room became busy - abort before restart"; exit 0; }
IKB=/home/chris/.local/ik/8d21687cc7-mag-c96826ab/bin/llama-server
NAME=amb512-ger IK_BIN=$IKB IK_ENV="MAG_NUMA_LOCAL=16" IK_ARGS="-amb 512 -ger" bash /home/chris/bneck/v38/var.sh
L=$(tail -1 /home/chris/bneck/v38/results.tsv); echo "CAND $L"
CPP=$(echo "$L" | cut -f3); CTG=$(echo "$L" | cut -f4)
keep=$(python3 -c "import sys
try: b=(float('$BPP'),float('$BTG'));c=(float('$CPP'),float('$CTG'))
except: print(0);sys.exit()
print(1 if c[1]>=b[1]*1.03 and c[0]>=b[0]*0.97 else 0)")
if [ "$keep" = 1 ]; then echo "KEEP candidate (faster)";
else echo "ROLLBACK candidate (not faster)"; NAME=rollback IK_BIN=$IKB IK_ENV="MAG_NUMA_LOCAL=16" IK_ARGS="-" bash /home/chris/bneck/v38/var.sh; echo "AFTER $(tail -1 /home/chris/bneck/v38/results.tsv)"; fi
curl -s -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:8090/health
echo "== $(date -Is) tune40 done"
