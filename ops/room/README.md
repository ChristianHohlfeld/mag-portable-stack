# Room: DeepSeek V4 Flash, one bot per NUMA node

`model.env` is the shared room/gateway contract, installed as
`/etc/mag-stack/room-model.env`. `MAG_ROOM_MODEL_PORTS=8191:0,8192:1`
means **port:NUMA**, not llama slot. Both servers have one llama slot (`0`), explicitly pinned by room and gateway for chat and prewarm.
`MAG_ROOM_MAX_BOTS` must be `2`. Thinking follows `MAG_ROOM_THINKING` and the
per-message switch; it is no longer forced off by a model name.

`presets.json` supplies the constellation picker. Adding a bot uses the next
free persistent slot/NUMA node automatically. Existing IDs, names chosen by
the owner, VMs and memory survive; surplus bots remain parked. A third bot
is refused with `Maximum 2 Bots erreicht`. Prefix files contain model ID and
bot slot, so a different model cannot restore the previous model's KV state.

Bootstrap installs `mag-llm-ds-node@.service`, enables @0/@1, and only starts
inactive servers when explicitly passed `--start-llm`. Regular deploy stages
the definitions and refreshes room/gateway; it never restarts a model.
Ornith must already be stopped (as on mag on 26 October 2026); the installer
disables its old units without touching model files. It removes obsolete
room drop-ins `20-deepseek-audit`, `30-dual-numa`, `50-two-bots` and replaces
the legacy secondary-model selection. Existing `room-model.env` and
`ds-node.env` are preserved. Runtime/model/template paths are in
`/etc/mag-llm/ds-node.env`; bootstrap records the runtime it just built.

These units reproduce the supplied live settings (14 threads, one slot,
CPU/memory bound per node). The server-only snapshot
`/workspace/mag/room-ds-2bots-20261026/` was unavailable in this checkout;
these are repo definitions, not a verified byte-for-byte copy of that snapshot.

## TLS finding (not yet reproduced on mag)

The repository's `ops/private-access/mag-room.nginx.conf` does not set
`ssl_ecdh_curve`. It may inherit a host-wide setting; neither the effective
host config nor nginx's linked OpenSSL version is available in this session.
No nginx config is changed by this PR.

On mag, inspect `nginx -V` (built/running OpenSSL), `openssl version -a`,
and locally inspect the `ssl_ecdh_curve`/`ssl_conf_command` directives in
`nginx -T` without sharing the complete output. Compare the same SNI/address
with TLS 1.2, TLS 1.3 + X25519, and, with a supporting OpenSSL client,
TLS 1.3 + X25519MLKEM768. Compare a Chrome 156 handshake and nginx error log.
The reported difference alone does not prove a server curve bug: a larger
ClientHello can also expose a transport/MTU or intermediary problem.

An explicit server curve list such as `X25519:prime256v1` is only a candidate
mitigation after reproducing the failure; it does not remove the client's
large first ClientHello. Do not claim it fixes a transport failure. Any host
change needs `nginx -t`, a fresh Chrome connection and a TLS 1.2 comparison.
Keep TLS 1.3 and certificate validation enabled.

Reference: [nginx ssl_ecdh_curve documentation](https://nginx.org/en/docs/http/ngx_http_ssl_module.html#ssl_ecdh_curve).

## Cache and interactive turns

The previous repo unit omitted the live `-rtr`, `-crs 0.20`, checkpoint interval
128, batch 2048 and prefetch/memory tuning. The gateway also stripped
`id_slot`, invoking the runtime's generic slot/cache selector. Restore the live
settings and pin each dedicated server to slot 0 with `cache_prompt=true`
(except explicitly stateless deterministic requests). Do not restart a model
during deployment just to apply unit changes.

Prewarm and chat use identical system/tools/persona ordering and the same
slot. Clock/files/status remain after the transcript. A warm RAM slot is not
restored from disk; user input cancels any background prewarm immediately.
Actual `n_past`/time-to-first-token still needs measurement on the real
DeepSeek instance; CPU throughput cannot be proved by the fake-model CI.

Queue updates have persistent revisions and sender-scoped retry IDs. Stop
closes active backend requests, preserves partial output and continues the
queue. Explicit queue pause holds existing items; new input stays available.
Queued user messages are visible immediately. UI order uses submit/start
timestamps instead of completion time or reserved group positions.

Thinking defaults and per-bot overrides are stored server-side. SSE replay
is isolated per bot. Reasoning channels and split `<think>` blocks stay out
of answer deltas. With Thinking off the prompt phase is only a fixed-size
opacity pulse; with Thinking on the thought header is collapsible with a
duration and closes after thinking completes. GitHub tools (including GitHub
MCP bindings) require explicit per-bot enablement. Switching access off
changes booleans only and preserves server-side credentials.
