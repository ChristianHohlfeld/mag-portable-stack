# Ornith-1.5-35B-A3B ×4 — replaces NUMA1 Qwen for all room bots

Owner decision (approved): every room bot uses Ornith-1.5-35B-A3B Q4_K_M.
`mag-llm` (DeepSeek Q4, :8090) is unchanged and independent.

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

```bash
sudo bash ops/ornith/cutover.sh check
sudo bash ops/ornith/cutover.sh apply     # auto-rollback to Qwen on any failure
sudo bash ops/ornith/rollback.sh          # manual Ornith -> Qwen
```
