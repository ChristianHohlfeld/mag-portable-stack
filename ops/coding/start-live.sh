#!/usr/bin/env bash
# NUMA-bound production launcher: one dedicated slot/port per bot.
# Flags follow the llama.cpp server README (tools/server/README.md at the
# pinned ops/qwen/runtime.sha) and the official model cards:
#  - Qwen3.8-Flash-Next (qwen4exp): https://huggingface.co/Qwen/Qwen3.8-Flash-Next
#  - Ornith-1.5-35B-A3B (qwen35moe, BOTH nodes since 2026-11-04):
#    https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B ; GGUF
#    https://huggingface.co/bartowski/Ornith-1.5-35B-A3B-GGUF (Q4_K_M, SHA256 in
#    models.json). Card sampling (general tasks): temperature 0.6, top_p 0.95,
#    top_k 20. Official chat_template.jinja (ops/coding/ornith-chat.jinja,
#    verbatim): enable_thinking=false pre-fills an empty <think></think>.
#    Engine: mainline llama.cpp (qwen35moe since ggml-org/llama.cpp#19468);
#    ik_llama.cpp's hybrid-model checkpoint reuse is not merged yet.
#  - Flint (flint, 2026-11-03..04, GGUF stays on disk):    https://huggingface.co/RecursiveSI/flint
#    GGUF https://huggingface.co/ggml-org/Flint-GGUF ; card sampling
#    temperature 1.0, top_p 0.95, top_k 20 (server defaults; the room sends the same).
#    Needs the separately pinned llama.cpp of ops/coding/runtime.sha (flint arch,
#    ggml-org/llama.cpp#30928), installed as /usr/local/lib/mag-coding/runtime-coder.sha.
# Both use the GGUF's own Jinja chat template (--jinja); reasoning is split
# into reasoning_content (--reasoning-format auto) and never reaches the room text.
set -euo pipefail
which=${1:?usage: start-live.sh 0|1}
case "$which" in
  # Owner 2026-11-03 12:08: Flint Q4_K_M on BOTH sockets, one instance per
  # node, never spanning (the NUMA interconnect is the bottleneck). Qwen3.8's
  # GGUF stays on disk. ctx = Flint card's documented 262,144-token window
  # (13 of 49 layers are full attention, 4 KV heads: ~7 GB f16 KV per node).
  # Owner 2026-11-04: Ornith-1.5-35B-A3B on both nodes (ctx = card's
  # native 262,144 tokens; only the full-attention layers hold KV).
  # 2026-11-04 19:xx: MTP self-speculation (llama.cpp --spec-type draft-mtp).
  # mtp=embedded: the GGUF carries the Ornith nextn block itself (bartowski
  # Q4_K_M: blk.40, nextn_predict_layers=1); mtp=<file>: separate MTP head GGUF
  # (AtomicChat quants are converted --no-nextn). -np 1 is already the layout
  # (one lane per node), which is MTP's upstream requirement.
  # NUMA0 (Lena): AtomicChat AD-Q6_K-Q5_K for code quality (card KLD 0.0158 /
  # top-1 94.85 % vs Q4_K_M 0.0477 / 91.0 %) + its pinned Q8_0 MTP head;
  # NUMA1 (Noah): bartowski Q4_K_M + embedded MTP (fast lane). Both files stay
  # on disk; switching back is a models.json/start-live.sh change.
  0) node=0; cpus=0-13; port=8191; name=general; ctx=262144
     alias=ornith-numa0; file=Ornith-1.5-35B-A3B-AD-Q6_K-Q5_K.gguf; mtp=mtp-Ornith-1.5-35B-A3B-Q8_0.gguf
     extra=(--temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0) ;;
  1) node=1; cpus=14-27; port=8192; name=coder; ctx=262144
     alias=ornith; file=Ornith-1.5-35B-A3B-Q4_K_M.gguf; mtp=embedded
     extra=(--temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0) ;;
  *) echo 'coding: invalid NUMA node' >&2; exit 4 ;;
esac
# Both nodes: the pinned flint runtime (ops/coding/build-runtime.sh).
# (The qwen4exp runtime of ops/qwen/runtime.sha stays installed for rollback.)
REV=$(cat /usr/local/lib/mag-coding/runtime-coder.sha)
[[ $REV =~ ^[a-f0-9]{40}$ ]] || exit 4
DIR="/home/chris/.local/mag-coding-rt/$REV/bin"
BIN="$DIR/llama-server"
MODEL="/home/chris/models/mag-coding/$name/$file"
[[ -x $BIN && -f $MODEL && ! -L $MODEL ]] || { echo "coding $node: runtime/GGUF missing" >&2; exit 4; }
[[ $(cat "$DIR/source-commit" 2>/dev/null || true) == "$REV" ]] || exit 4
[[ $(cat "/sys/devices/system/node/node$node/cpulist") == $( [[ $node == 0 ]] && echo '0-13,28-41' || echo '14-27,42-55' ) ]] || {
  echo "coding $node: NUMA topology mismatch" >&2; exit 4;
}
# A reboot must never load unchecked GGUF bytes. Hash on the target node so
# the page cache the model is then loaded from is node-local.
numactl --cpunodebind="$node" --membind="$node" python3 -I /usr/local/lib/mag-coding/stage.py --model "$name"
SLOT="/var/lib/mag-llm/slots/coding/$name"
[[ -d $SLOT && -w $SLOT ]] || { echo 'coding: slot directory unavailable' >&2; exit 4; }
# Engine switch (owner 2026-11-03 19:43): ik_llama.cpp is live on both nodes.
# Mainline fallback without a deploy:
#   echo mainline | sudo tee /etc/mag-stack/coding-engine; systemctl restart mag-llm-coding@0 @1
# ik: its own slot dir (mainline slot files do not load: "mismatched layer
# count"), flags translated per ik --help (no --load-mode/--slots/--no-ui/
# --checkpoint-min-step/--no-reasoning-preserve), --swa-compress needs -np 1,
# no -rtr (ik #2611), no -sp (ik #2825). Fused MoE is on by default (no -fmoe flag).
IK_REV=c8873ad5bd0a8947aeac64c349fb8f8e905f6efa
ENGINE=$(tr -d '[:space:]' < /etc/mag-stack/coding-engine 2>/dev/null || true)
# Ornith runs on mainline (default); "ik" is only honoured for the Flint GGUF.
ENGINE=${ENGINE:-mainline}
[[ $file == Flint-* ]] || ENGINE=mainline
IK_BIN="/home/chris/.local/mag-coding-ik/$IK_REV/bin/llama-server"
if [[ $ENGINE == ik && -x $IK_BIN && $(cat "$(dirname "$IK_BIN")/source-commit" 2>/dev/null || true) == "$IK_REV" ]]; then
  IK_SLOT="$SLOT/ik"
  # Owner 2026-11-04: chat turns without hidden thinking. Flint's GGUF template
  # ignores enable_thinking; flint-chat.jinja is that template verbatim except
  # the generation prompt, which pre-fills a closed <think>...</think> block
  # when enable_thinking=false. Code turns keep thinking (enable_thinking=true).
  # Server reasoning budget stays -1; the room sets it per request.
  TEMPLATE_ARGS=()
  TPL=/usr/local/lib/mag-coding/flint-chat.jinja
  [[ -r $TPL ]] && TEMPLATE_ARGS=(--chat-template-file "$TPL")
  [[ -d $IK_SLOT ]] || mkdir -p "$IK_SLOT"
  exec numactl --physcpubind="$cpus" --membind="$node" "$IK_BIN" \
    --model "$MODEL" --alias "$alias" --host 127.0.0.1 --port "$port" \
    --ctx-size "$ctx" --parallel 1 --threads 14 --threads-batch 14 --numa numactl \
    --mlock "${extra[@]}" \
    --jinja --reasoning-format deepseek "${TEMPLATE_ARGS[@]}" \
    --cache-ram 16384 --ctx-checkpoints 48 --ctx-checkpoints-interval 1024 \
    -fa on --batch-size 2048 --ubatch-size 512 --cache-type-k f16 --cache-type-v f16 \
    --swa-compress \
    --slot-save-path "$IK_SLOT" --webui none
fi
[[ $ENGINE == ik ]] && echo "coding $node: ik runtime $IK_REV missing, using mainline" >&2
SPEC_ARGS=()
if [[ $mtp == embedded ]]; then
  SPEC_ARGS=(--spec-type draft-mtp --spec-draft-n-max 3)
elif [[ -n $mtp ]]; then
  DRAFT="/home/chris/models/mag-coding/$name/$mtp"
  [[ -f $DRAFT && ! -L $DRAFT ]] || { echo "coding $node: MTP head missing" >&2; exit 4; }
  SPEC_ARGS=(--spec-type draft-mtp --spec-draft-n-max 3 --model-draft "$DRAFT")
fi
exec numactl --physcpubind="$cpus" --membind="$node" "$BIN" \
  --model "$MODEL" --alias "$alias" --host 127.0.0.1 --port "$port" \
  --ctx-size "$ctx" --parallel 1 --threads 14 --threads-batch 14 --numa numactl \
  --load-mode mlock --lazy-mode off "${extra[@]}" \
  --jinja --reasoning-format auto --chat-template-file /usr/local/lib/mag-coding/ornith-chat.jinja \
  --cache-ram 16384 --ctx-checkpoints 48 --checkpoint-min-step 1024 --no-reasoning-preserve \
  --flash-attn on --batch-size 2048 --ubatch-size 512 --cache-type-k f16 --cache-type-v f16 \
  "${SPEC_ARGS[@]}" \
  --slots --slot-save-path "$SLOT" --no-ui
