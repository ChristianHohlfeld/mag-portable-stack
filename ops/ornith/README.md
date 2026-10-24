# Ornith-1.5-35B-A3B — one shared server for all four room bots

Owner decision (approved): every room bot uses Ornith-1.5-35B-A3B Q4_K_M, the model is
loaded ONCE and shared by the four bots. `mag-llm` (DeepSeek Q4, :8090) is unchanged and independent.

## Layout (variant A, chosen by measurement)

`mag-llm-ornith.service` → `start-shared.sh`: one llama-server on 127.0.0.1:8191,
`numactl --interleave=all --physcpubind=0-27`, `--threads 28 --numa distribute`,
`--parallel 4 --ctx-size 131072` (4 × 32k, one llama slot per bot), `--load-mode mlock`,
`--slot-save-path /var/lib/mag-llm/slots/ornith`, pinned runtime (`ops/qwen/runtime.sha`),
SHA-256 check (`/etc/mag-ornith/model.sha256`), RAM preflight (≥14 GiB per node), refuses
while Qwen/IQ3 or a legacy instance runs.

Gateway: `MAG_MODEL_ORNITH_PORTS=8191:0,8191:1,8191:2,8191:3` — bot slot N (X-Mag-Slot N-1)
→ port 8191, llama `id_slot` N-1, forced in every chat body; `/slots/<any>?action=save|restore|erase`
is rewritten to the bot's own id_slot. Slot RPC without X-Mag-Slot, unknown action or
invalid slot → 400 (fail closed). The legacy form `8191,8192,8193,8194` (one instance per
bot, no id_slot) still works for rollback.

Prefix cache: the system prompt is byte-identical for all bots up to the `[Identitaet]`
block at its end (name, persona, VM, "never claim to be Claude/Anthropic/…"). The room
prewarms every bot slot at room start, when the server becomes ready and whenever a
system prompt/tools/persona/bot set changes (watcher, ~10 s), one bot at a time. Each bot
saves `mag-prefix-ornith-slot<N>.bin`; `state/prompt-prefix.json` records which shared
prefix a file holds, so a bot whose own file is stale restores another bot's matching
file and evaluates only identity + memory + transcript instead of ~12k tokens cold.
While a prewarm runs the bot list shows "Prompt wird vorbereitet (~X min)"; during a turn
the progress bar shows the same estimate from llama's `prompt_progress`.

Deterministic mode (`model-settings.json`, default off) still sends a short identity
system prompt (name, persona, German, bootstrap context) plus the single user message,
with X-Mag-Bot/X-Mag-Slot, so each bot keeps its own name and llama slot.

## Measurement (mag, 2026-10-24; pinned runtime, mlock, synthetic German prompt, cache_prompt off)

Raw data: `ops/perf-results/ornith-shared-20261024/` (bench.py, result-A.json, result-B.json).

| test | A: 1 server, 28 thr, interleave, -np 4 | B: 2 servers (1/node), 14 thr, -np 2 | old: 4 × 7 cores |
|---|---|---|---|
| load / RSS | 54.0 s / **23.5 GiB** | 47.6 s / 43.6 GiB | 4 × 23 GB |
| cold prewarm 1 bot, 15.6k tok | **168.3 s (92.7 t/s)** | 251.1 s (62.1 t/s) | ~5.5 min (12k) |
| 1 bot, 5.2k tok + 128 gen | **56.6 s**, pp 109.4, tg 14.64 | 78.1 s, pp 76.4, tg 13.48 | pp 29–37, tg ~9.3 |
| 2 bots, same server | 58.2 s, tg ~10 | 77.9 s, tg ~9.6 | – |
| 2 bots, different servers | – | 43.1 s, tg ~14.2 | – |
| 4 bots concurrent, 2.6k + 128 | 107.8 s, tg 3.3–7.3 | **79.6 s**, tg ~9.6 | – |
| 4 cold prewarms concurrent | 686.7 s | **521.8 s** | – |

Decision: **A**. Room turns are strictly sequential (bot 2 reads bot 1's answer), so
the room only ever runs ONE request at a time; there A is 28–45 % faster (prefill
109 vs 76 t/s, gen 14.6 vs 13.5 t/s, turn 56.6 vs 78.1 s) and needs half the RAM. B only
wins with ≥3 simultaneous requests. Cold prewarm on A is serialized: bot 1 ~168 s, bots
2–4 restore its shared prefix and evaluate only their tail, ≈ 3–4 min for all four instead
of 522 s (B, parallel) or ~5.5 min each today.

Concurrent bot answers: **not enabled**. On A, 4 concurrent short turns take 107.8 s vs
~130 s sequential (−17 %), but each bot would no longer see the previous bot's answer,
which is the room's conversation contract, and generation per bot drops to 3–7 t/s.
Not a clear win, so the sequential order stays.

## Operations

```bash
sudo bash ops/ornith/cutover.sh check
sudo bash ops/ornith/cutover.sh apply      # Qwen or 4x instances -> shared; auto-rollback to the previous layout
sudo bash ops/ornith/rollback.sh instances # shared -> previous 4x Ornith instances (8191-8194)
sudo bash ops/ornith/rollback.sh           # -> Qwen
```
Old units (`mag-llm-ornith@1..4`, `instances/N.conf`, `start.sh`) stay installed and
disabled for rollback; nothing is deleted.

## Legacy 4-instance layout (rollback target)

| instance | port | cpus  | NUMA |
|----------|------|-------|------|
| 1        | 8191 | 0-6   | 0    |
| 2        | 8192 | 7-13  | 0    |
| 3        | 8193 | 14-20 | 1    |
| 4        | 8194 | 21-27 | 1    |

`mag-llm-ornith@N.service` (+ `instances/N.conf` drop-in for CPUAffinity/NUMAMask)
runs `start.sh N`: pinned runtime (`ops/qwen/runtime.sha`), SHA-256 check against
`/etc/mag-ornith/model.sha256`, refuses on low node RAM or when Qwen/IQ3 runs.

Routing: `MAG_SECONDARY_MODEL=ornith`. The room sends model `ornith-1.5-35b-a3b`,
`enable_thinking:false` and `X-Mag-Slot: <slot-1>`; the gateway maps it to
`MAG_MODEL_ORNITH_PORTS[slot-1]` (fixed, so each bot keeps its prompt cache). No slot →
instance 1; invalid slot or unknown kind → fail closed.

Room bots (ornith mode ignores `MAG_ROOM_DUAL_NUMA`/`MAG_ROOM_SINGLE_BOT`): up to four
bots, freely added/removed. Each bot has a persistent `slot` (1..4, lowest free) in
`agents.json`, so deleting a bot never moves another bot to a different instance.
On first start the room migrates once: `DeepSeek 4.0` → `Ornith 1`, `Qwen 2` →
`Ornith 2`, slots 1/2 (ids, VMs, memory unchanged). Deleting a bot never touches
`mag-llm` or any `mag-llm-ornith@N`; it archives the bot record to
`<room state>/archive/<id>-<ts>.json` and `magbot despawn` moves the VM directory to
`/var/lib/libvirt/images/magbot/archive/<vm>-<ts>` (no auto purge). The bot's Gitea
account and repos are kept, only locked (new unknown password); re-adding reuses it. Rollback to Qwen restores the fixed dual-NUMA layout.
