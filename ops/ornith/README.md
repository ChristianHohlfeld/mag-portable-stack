# Ornith-1.5-35B-A3B — one server per NUMA node, two bots each (variant B)

Owner decision (Chris, 2026-10-24 22:09): priorities are NON-BLOCKING and FAST answers →
**variant B**. Every room bot uses Ornith-1.5-35B-A3B Q4_K_M; two llama-servers, one per
NUMA node, two bots per server; all bots answer concurrently. DeepSeek Q4/IQ3
is retired by deploy only after the four-slot Ornith room is healthy.

## Layout (variant B)

`mag-llm-ornith-node@N.service` (N = 0|1) → `start-node.sh N`: llama-server on
127.0.0.1:819(1+N), `numactl --cpunodebind=N --membind=N --physcpubind=0-13 | 14-27`,
`--threads 14 --numa numactl`, `--parallel 2 --ctx-size 65536` (2 × 32k, one llama slot per
bot), `--load-mode mlock`, `--cache-ram 2048`, `--slot-save-path /var/lib/mag-llm/slots/ornith-nodeN`,
pinned runtime (`ops/qwen/runtime.sha`), SHA-256 check (`/etc/mag-ornith/model.sha256`),
per-node RAM preflight (≥24 GiB reclaimable on node N), refuses while Qwen/IQ3 or a legacy
instance runs. Drop-in `nodes/N.conf`: `CPUAffinity`/`AllowedCPUs`/`NUMAPolicy=bind`/`NUMAMask=N`.
Memory: ~21.8 GiB RSS per server (43.6 GiB measured for both) + 2 GiB cache-ram ≈ 23.8 GiB →
`MemoryHigh=25G` (above RSS + cache-ram), `MemoryMax=28G`, `MemorySwapMax=0`.

Gateway: `MAG_MODEL_ORNITH_PORTS=8191:0,8192:0,8191:1,8192:1` — bot slot 1 → 8191/id_slot 0,
2 → 8192/0, 3 → 8191/1, 4 → 8192/1. The first two bots sit on DIFFERENT servers, so two
bots run at full single-bot speed (43.1 s, tg ~14.2). `id_slot` is forced in every chat body;
`/slots/<any>?action=save|restore|erase` is rewritten to the bot's own id_slot on its own
server. Slot RPC without X-Mag-Slot, unknown action or invalid slot → 400 (fail closed).
The legacy form `8191,8192,8193,8194` (one instance per bot) still works for rollback.

## Concurrency design (room)

- A user message to several bots starts all of them at once (`MAG_ROOM_STAGGER` defaults
  to `off` in Ornith mode; one lane per bot slot). No bot waits for another bot; each
  bot reads the transcript as it was at the user message. Stop/cancel per bot (`/api/cancel
  {bot}`) ends only that bot's lane. Max 4 bots; a 5th is refused in German.
- Stable order: the bots of one request form a turn group (`group`, `order` = the bot's
  fixed position in the bot list) carried on `bot_start`, the live run state and the posted
  message. The UI keeps all answers and live placeholders of a group at the group's first
  position, sorted by `order`. A slow bot's placeholder therefore never drops below a
  faster bot's finished answer; its answer takes the placeholder's place. The transcript
  sent to the model stays append-only (completion order, prompt cache).
- Optional reflection round (owner room setting "Zweite Runde", `POST /api/reflect`,
  `state/reflect`, default **OFF** = least blocking): when all bots of a group have
  answered and none was stopped, each bot answers once more, concurrently, with a
  `Reflexionsrunde` note in its context tail; it then sees the others' first-round answers.
  Never chained, never autonomous (no `[[MAG:WEITER]]` owner), not for deterministic bots.
- Autonomy contract unchanged: the first selected bot is the only autonomous owner.

## Prefix cache

The system prompt is byte-identical for all bots up to the `[Identitaet]` block at its end
(name, persona, VM, "never claim to be Claude/Anthropic/…"). The room prewarms every bot
slot at room start, when the servers become ready and whenever a system prompt/tools/
persona/bot set changes (watcher, ~10 s): one bot at a time PER SERVER, both servers in
parallel. Each bot saves `mag-prefix-ornith-slot<N>.bin` into its server's slot directory;
`state/prompt-prefix.json` maps `port/file` → shared prefix key, so a bot whose own file is
stale restores the other bot's matching file on the SAME server only and evaluates just
identity + memory + transcript. While a prewarm runs the bot list shows "Prompt wird
vorbereitet (~X min)" (`MAG_ROOM_PREFILL_TPS`, default 60 t/s as measured on B).

Deterministic mode (`model-settings.json`, default off) still sends a short identity system
prompt plus the single user message, with X-Mag-Bot/X-Mag-Slot.

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

Decision: **B** (owner, 2026-10-24 22:09). With concurrent answering every bot gets
~9.6 t/s on B vs 3.3–7.3 t/s on A; 4 bots finish in 79.6 s vs 107.8 s; two bots on
different servers run at 43.1 s (A: 58.2 s); 4 cold prewarms 521.8 s vs 686.7 s; two
processes = no single point of failure. A remains faster only for a single sequential
request (56.6 vs 78.1 s) and needs half the RAM (23.5 vs 43.6 GiB) — irrelevant once bots
answer concurrently.

## Operations

```bash
sudo bash ops/ornith/cutover.sh check
sudo bash ops/ornith/cutover.sh apply      # Qwen or 4x instances -> 2 node servers; auto-rollback to the previous layout
sudo bash ops/ornith/rollback.sh instances # node servers -> previous 4x Ornith instances (8191-8194)
sudo bash ops/ornith/rollback.sh           # -> Qwen
```
Ports 8191/8192 are shared with legacy instances 1/2: cutover stops (PID 0) and checks the
ports free before starting the node servers; rollback stops both node servers before the
legacy instances start. Old units (`mag-llm-ornith@1..4`, `instances/N.conf`, `start.sh`)
stay installed and disabled for rollback; nothing is deleted.

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

## Retirement nach erfolgreichem Deploy

`ops/models/retire-deepseek.sh` prüft `secondary-model.env`, beide direkten
Ornith-Health-Endpunkte und `/readyz` des Raums. Erst dann werden alte Units
gestoppt und deaktiviert. `RefuseManualStop=yes` wird temporär ausschließlich
in `/run/systemd/system` übersteuert. `mag-120` bleibt aktiv, GGUF-Dateien bleiben
erhalten. Bei fehlender Bereitschaft: WARN + kein Stop. Rollback: `sudo bash
ops/ornith/rollback.sh qwen`, ohne DeepSeek-Abhängigkeit.
