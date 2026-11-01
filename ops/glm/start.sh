#!/usr/bin/env bash
# Q1 stays on the reviewed mainline runtime. Q2 can select its dedicated,
# pinned ik_llama.cpp build, with mainline retained as an explicit rollback.
set -euo pipefail
instance=${1:?usage: start.sh q1|q2}
[[ $instance == q1 || $instance == q2 ]] || exit 4
source "/etc/mag-glm/$instance.env"
fail(){ echo "GLM $instance: $*" >&2; exit 4; }

MAINLINE_REV=$(cat /usr/local/lib/mag-glm/runtime.sha)
[[ $MAINLINE_REV =~ ^[a-f0-9]{40}$ ]] || fail 'mainline runtime sha invalid'
MAINLINE_BIN="/home/chris/.local/qwen4exp/$MAINLINE_REV-mag-cp1/bin/llama-server"
TEMPLATE=/usr/local/lib/mag-glm/chat-template.jinja
[[ -f $TEMPLATE ]] || fail 'chat template missing'
[[ $(cat "$(dirname "$MAINLINE_BIN")/source-commit" 2>/dev/null) == "$MAINLINE_REV" ]] || fail 'mainline runtime version not pinned'

[[ $GLM_NODE =~ ^[01]$ && $GLM_PORT =~ ^819[12]$ ]] || fail 'bad node/port'
[[ ( $instance == q1 && $GLM_NODE == 0 && $GLM_PORT == 8191 ) ||
   ( $instance == q2 && $GLM_NODE == 1 && $GLM_PORT == 8192 ) ]] || fail 'instance routing mismatch'

ENGINE=mainline
if [[ $instance == q2 ]]; then
  ENGINE=${GLM_ENGINE:-ik}
  [[ $ENGINE == mainline || $ENGINE == ik ]] || fail 'GLM_ENGINE must be mainline or ik'
else
  [[ ${GLM_ENGINE:-mainline} == mainline ]] || fail 'GLM-Q1 is mainline-only'
fi

if [[ $ENGINE == ik ]]; then
  IK_REF=8d21687cc7358de10264d4180ba9b863024d5c4
  PATCH="$(
    sha256sum /usr/local/lib/mag-glm/ik/0001-glm5next-legacy-6block-hparams.patch 2>/dev/null |
      awk '{print $1}' | sha256sum | cut -c1-12
  )"
  IK_DIR="/home/chris/.local/glm-ik/${IK_REF:0:10}-mag-$PATCH"
  BIN="$IK_DIR/bin/llama-server"
  [[ -x $BIN ]] || fail "pinned ik runtime missing: $BIN"
  [[ $(cat "$IK_DIR/source-commit" 2>/dev/null) == "$IK_REF" ]] || fail 'ik source commit mismatch'
  [[ $(cat "$IK_DIR/patch-identity" 2>/dev/null) == "$PATCH" ]] || fail 'ik patch identity mismatch'
  [[ -f "$IK_DIR/SHA256SUMS" ]] || fail 'ik binary checksum missing'
  (cd "$IK_DIR" && sha256sum --check --strict --status SHA256SUMS) || fail 'ik binary checksum mismatch'
  ENGINE_COMMIT=$IK_REF
else
  BIN=$MAINLINE_BIN
  ENGINE_COMMIT=$MAINLINE_REV
fi
[[ -x $BIN ]] || fail 'selected runtime missing'

# Expand shard-1 from a literal GGUF or glob; never choose a later split shard.
shopt -s nullglob
candidates=( $GLM_MODEL )
(( ${#candidates[@]} > 0 )) || fail "GGUF missing: $GLM_MODEL"
MODEL=${candidates[0]}
[[ -f $MODEL && $(head -c 4 "$MODEL") == GGUF ]] || fail 'invalid GGUF first shard'
[[ -d $GLM_SLOT_DIR && ! -L $GLM_SLOT_DIR ]] || fail 'slot directory missing'

NODE_CPU_TOPOLOGY=${GLM_NODE_CPU_TOPOLOGY:-$GLM_CPU_TOPOLOGY}
[[ $(cat "/sys/devices/system/node/node$GLM_NODE/cpulist") == "$NODE_CPU_TOPOLOGY" ]] || fail 'unexpected NUMA topology'
if [[ $instance == q2 ]]; then
  # NUMA1 physical cores only. SMT siblings 42-55 are deliberately excluded.
  [[ $GLM_CPU_TOPOLOGY == 14-27 && $NODE_CPU_TOPOLOGY == 14-27,42-55 ]] || fail 'Q2 must use only NUMA1 physical cores 14-27'
fi

for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear; do
  systemctl is-active --quiet "$u.service" && fail "backup active: $u"
done

THREADS_BATCH=28
if [[ $instance == q1 ]]; then
  FORMAT='glm-5.3-flash-iq1-m:kv-f16-f16:ctx32768:slot0'
  legacy="mag-prefix-glm-5.3-flash-iq1-m-slot1.bin"
  binding=(--physcpubind=0-13,28-41 --membind=0)
else
  FORMAT='glm-5.3-flash:kv-f16-f16:ctx32768:slot0'
  legacy="mag-prefix-glm-5.3-flash-slot2.bin"
  binding=(--physcpubind=14-27 --membind=1)
  THREADS_BATCH=14
  # Original NUMA1 IQ2_XS memory gate from #231.
  read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' /sys/devices/system/node/node1/meminfo)
  usable=$((free + (file > shmem ? file-shmem : 0)))
  (( usable >= 145*1024*1024 )) || fail 'NUMA1 reclaimable RAM below 145GiB'
  bash /usr/local/lib/mag-glm/verify-model.sh || fail 'IQ2 model verification failed'

  # Fingerprint the complete, pinned 15-shard model without rereading 92 GB
  # on every start: model.sha256 contains the immutable per-shard SHA256 pins.
  SHA256S=/usr/local/lib/mag-glm/model.sha256
  [[ -f $SHA256S && $(grep -c . "$SHA256S") == 15 ]] || fail '15-shard model SHA256 manifest missing'
  MODEL_SHA=$(awk 'NF {print $1}' "$SHA256S" | sha256sum | awk '{print $1}')
  CTX=32768
  MARKER=$(printf 'engine=%s\nengine_commit=%s\nmodel_sha=%s\nctx=%s\n' \
    "$ENGINE" "$ENGINE_COMMIT" "$MODEL_SHA" "$CTX")
  marker_file="$GLM_SLOT_DIR/.kv-marker"
  if [[ ! -f $marker_file || $(cat "$marker_file") != "$MARKER" ]]; then
    # A stale or unknown marker must never restore incompatible KV state.
    find "$GLM_SLOT_DIR" -maxdepth 1 -type f -name 'mag-slot-*.bin' -delete
    rm -f -- "$GLM_SLOT_DIR/.kv-format"
    printf '%s' "$MARKER" > "$marker_file.tmp"
    mv -f -- "$marker_file.tmp" "$marker_file"
  fi
fi

# Broadwell: 14 decode workers; Q1 retains its existing SMT batch binding.
# Q2 is intentionally restricted to its 14 physical NUMA1 cores.
exec /usr/bin/numactl "${binding[@]}" "$BIN" \
  --model "$MODEL" --alias "$GLM_ALIAS" \
  --host 127.0.0.1 --port "$GLM_PORT" --ctx-size 32768 --parallel 1 \
  --threads 14 --threads-batch "$THREADS_BATCH" --numa numactl \
  --load-mode dio -fa on -b 4096 -ub 1024 \
  --jinja --chat-template-file "$TEMPLATE" --reasoning-format auto \
  --temp 1.0 --top-p 0.95 --cache-ram 16384 --ctx-checkpoints 64 --checkpoint-min-step 1024 \
  --slots --slot-save-path "$GLM_SLOT_DIR" --no-ui
