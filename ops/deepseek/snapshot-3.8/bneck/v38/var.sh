#!/bin/bash
# Variante live schalten + Kurzbench. Aufruf (root): NAME=x IK_ENV=".." IK_ARGS=".." THREADS=16 THREADS_BATCH=28 BATCH=1024 UBATCH=1024 [IK_RTR=1] var.sh
set -euo pipefail
SRC=/home/chris/mag-stack; source $SRC/ops/deepseek/llm-config.sh
OUT=/home/chris/bneck/v38; mkdir -p $OUT; RES=$OUT/results.tsv
[ -f $RES ] || printf "time\tname\tpp_tps\ttg_tps\tload_s\tmemavail_gib\tvmlck_gib\tenv\targs\tt/tb/b/ub\n" > $RES
export ENGINE=ik IK_BIN=${IK_BIN:-/home/chris/.local/ik/8d21687cc7-mag-abe5bad6/bin/llama-server} IK_RTR=${IK_RTR:-1} \
  IK_ENV="${IK_ENV:--}" IK_ARGS="${IK_ARGS:--}" NUMA_ARGS="${NUMA_ARGS:--}" LLAMA_NUMA="${LLAMA_NUMA:--}" \
  THREADS=${THREADS:-16} THREADS_BATCH=${THREADS_BATCH:-28} BATCH=${BATCH:-1024} UBATCH=${UBATCH:-1024}
llm_config_load; llm_config_save; render_unit /etc/systemd/system/mag-llm.service
install -d /run/systemd/system/mag-llm.service.d
printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload
t0=$SECONDS; systemctl restart mag-llm.service
rm -f /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload
ok=0; for i in $(seq 1 360); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health || true)" = 200 ] && { ok=1; break; }
  a=$(awk '/^MemAvailable:/{print int($2/1048576)}' /proc/meminfo)
  (( a < 40 )) && { echo "WATCHDOG avail=$a"; break; }
  sleep 5; done
load=$((SECONDS-t0))
[ $ok = 1 ] || { echo "$NAME startet nicht"; printf "%s\t%s\tFAIL\tFAIL\t%s\t\t\t%s\t%s\t\n" "$(date +%H:%M)" "$NAME" $load "$IK_ENV" "$IK_ARGS" >> $RES; exit 3; }
r=$(runuser -u magbot -- python3 -I - <<'PY'
import json,urllib.request,random
def q(msg,n):
    b={"model":"deepseek-v4-flash-0731","messages":[{"role":"user","content":msg}],"max_tokens":n,"temperature":0,"cache_prompt":False,
       "chat_template_kwargs":{"enable_thinking":False}}
    d=json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions",json.dumps(b).encode(),
       {"Content-Type":"application/json","X-Mag-Bot":"bench-3.8"}),timeout=1200))
    assert n>8 or True
    return d["timings"],d["choices"][0]["message"].get("content") or ""
t,c=q("Antworte nur mit der Zahl: Was ist 17+25?",16); assert "42" in c, c
pp=[];tg=[]
for i in range(2):
    t,_=q(f"[{random.random()}] "+"Die Wohnung in Konstanz hat drei Zimmer und einen Balkon. "*90+"\nFasse in einem Wort zusammen.",4); pp.append(t["prompt_per_second"])
    t,_=q(f"[{random.random()}] Schreibe einen ausführlichen Text über die Geschichte des Bodensees.",192); tg.append(t["predicted_per_second"])
print(f"{sum(pp)/2:.2f}\t{sum(tg)/2:.2f}")
PY
) || r="ERR	ERR"
P=$(systemctl show -p MainPID --value mag-llm)
vml=$(awk '/VmLck/{printf "%.1f",$2/1048576}' /proc/$P/status); av=$(awk '/^MemAvailable:/{print int($2/1048576)}' /proc/meminfo)
printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s/%s/%s/%s\n" "$(date +%H:%M)" "$NAME" "$r" $load $av $vml "$IK_ENV" "$IK_ARGS" $THREADS $THREADS_BATCH $BATCH $UBATCH >> $RES
tail -n1 $RES
