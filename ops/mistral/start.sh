#!/usr/bin/env bash
# Mistral Large 4 CPU-only launcher on NUMA0; never starts alongside Qwen room.
set -euo pipefail
fail(){ echo "Mistral NUMA0: $*" >&2; exit 4; }
REV=$(cat /usr/local/lib/mag-mistral/runtime.sha)
BIN="/home/chris/.local/mistral/$REV-mag-cpu/bin/llama-server"
DIR=/home/chris/models/Mistral-Large-4-1T-A52B-UD-IQ1_S
MODEL="$DIR/Mistral-Large-4-1T-A52B-UD-IQ1_S-00001-of-00006.gguf"
PORT=8191
THREADS=${MISTRAL_THREADS:-14}
MIN_RAM_GIB=${MISTRAL_MIN_RAM_GIB:-225}
[[ $REV =~ ^[a-f0-9]{40}$ && -x "$BIN" && -f "$MODEL" ]] || fail 'pinned runtime / first GGUF shard missing'
[[ $(cat "$(dirname "$BIN")/source-commit") == "$REV" ]] || fail 'runtime commit mismatch'
[[ $(cat /sys/devices/system/node/node0/cpulist) == '0-13,28-41' ]] || fail 'unexpected NUMA0 topology'
[[ $THREADS =~ ^[1-9][0-9]*$ && $MIN_RAM_GIB =~ ^[1-9][0-9]*$ ]] || fail 'invalid thread/RAM setting'
systemctl is-active --quiet mag-llm-qwen-room.service && fail 'Qwen room service still active; refuse co-residency on NUMA0'
systemctl is-active --quiet mag-llm-glm@q1.service && fail 'GLM-Q1 still active; room transaction must retire it first'
systemctl is-active --quiet mag-llm-glm@q2.service || fail 'GLM-Q2 must remain running'
curl --noproxy '*' -fsS --max-time 5 -o /dev/null http://127.0.0.1:8192/health || fail 'GLM-Q2 :8192 unhealthy; no restart attempted'
for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear; do
  systemctl is-active --quiet "$u.service" && fail "backup active: $u"
done
read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' /sys/devices/system/node/node0/meminfo)
usable=$((free + (file > shmem ? file-shmem : 0)))
(( usable >= MIN_RAM_GIB*1024*1024 )) || fail "NUMA0 reclaimable RAM below ${MIN_RAM_GIB}GiB"
bash /usr/local/lib/mag-mistral/verify-model.sh || fail 'six-shard SHA256 verification failed'
# Deliberately use mmap (the llama.cpp default). Do not mlock 217 GiB.
# Keep the default FP16 KV cache until measured evidence justifies q8_0.
exec /usr/bin/numactl --physcpubind=0-13,28-41 --membind=0 "$BIN" \
  --model "$MODEL" --alias mistral-large4 --host 127.0.0.1 --port "$PORT" \
  --ctx-size 16384 --parallel 1 --threads "$THREADS" --threads-batch "$THREADS" \
  --numa numactl --batch-size 128 --ubatch-size 64 --mmap --jinja \
  --temp 1.0 --top-p 0.95 --top-k 0 --min-p 0.0 --slots --slot-save-path /var/lib/mag-llm/slots/mistral --metrics
