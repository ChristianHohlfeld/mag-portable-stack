#!/usr/bin/env bash
# NUMA1 only: the E5-2680 v4 has 14 physical cores per socket. Run 14
# inference/batch workers on node1, never migrate weights to remote RAM.
# The model has a ~27 GiB per_layer_token_embd N-gram table. With the
# llama.cpp lazy-mode auto default, random lookups can fault from disk during
# prefill. Force it resident on NUMA1 (no GPU) even after page-cache eviction.
# --load-mode mlock: no mmap. With mmap, weights are the page-cache pages left
# by an earlier read (e.g. the SHA check run as root via install.sh), which
# --membind cannot move: measured 73 of 98 GiB on node0, prefill 13 t/s.
# Read into anonymous memory instead, so every page is allocated on NUMA1.
# Refuse to start if IQ3 is running or backups are active.
set -euo pipefail
fail(){ echo "Qwen NUMA1: $*" >&2; exit 4; }
REV=$(cat /usr/local/lib/mag-qwen/runtime.sha)
BIN="/home/chris/.local/qwen4exp/$REV/bin/llama-server"
MODEL="/home/chris/models/qwen3.8-flash-next-uncensored/Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf"
[[ -x "$BIN" && -f "$MODEL" ]] || fail 'runtime / GGUF missing'
[[ $(cat "/home/chris/.local/qwen4exp/$REV/bin/source-commit") == "$REV" ]] || fail 'runtime version not pinned'
[[ $(cat /sys/devices/system/node/node1/cpulist) == "14-27,42-55" ]] || fail 'unexpected NUMA1 topology'
systemctl is-active --quiet mag-llm-iq3.service && fail 'IQ3 still running; explicit operator stop required'
# Q4 is checked as root in systemd ExecStartPre (audit firewall blocks chris on port 8090).
for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear; do
  systemctl is-active --quiet "$u.service" && fail "backup active: $u"
done
read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' /sys/devices/system/node/node1/meminfo)
usable=$((free + (file > shmem ? file-shmem : 0)))
(( usable >= 150*1024*1024 )) || fail 'NUMA1 reclaimable RAM below 150GiB (reserve for system/backup)'
bash /usr/local/lib/mag-qwen/verify-model.sh || fail 'model SHA verification failed'
exec /usr/bin/numactl --cpunodebind=1 --membind=1 "$BIN" \
  --model "$MODEL" --alias qwen3.8-flash-next-uncensored \
  --host 127.0.0.1 --port 8091 --ctx-size 32768 --parallel 1 \
  --threads 14 --threads-batch 14 --numa numactl \
  --load-mode mlock --lazy-mode off -ot per_layer_token_embd=CPU \
  --jinja --reasoning-format auto --cache-ram 16384 \
  --slot-save-path /var/lib/mag-llm/slots/qwen --no-ui
