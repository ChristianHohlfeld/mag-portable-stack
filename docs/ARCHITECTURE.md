# MAG Architecture — Ornith, NUMA 0/1

**Stand 29.10.2026 (gilt vor dem Ornith-Abschnitt unten):** Zwei Bots, ein Modell pro
NUMA-Knoten. NUMA0 DeepSeek V4 Flash (`mag-llm-ds-node@0`, :8191), NUMA1 GLM-5.3-Flash
(`mag-llm-glm`, :8192, Layout `glm`). Das gewünschte Layout steht im Repo in
`ops/room/layout` und wird bei jedem Deploy transaktional von `ops/qwen/layout.sh`
angewendet (Details und Rollback: `ops/glm/README.md`). Qwen3.8 ist aus dem Stack
genommen, Gewichte und Unit bleiben auf der Platte (`MAG_ROOM_LAYOUT=mixed`).

**Aktueller Betriebsstand (Oktober 2026):** Vier Ornith-1.5-35B-A3B-Bots, zwei NUMA-Server
(`mag-llm-ornith-node@0/1.service`, 127.0.0.1:8191/8192, je zwei llama-Slots).
Audit-Gateway :8080, C++-Raum `mag-120.service` :8766, nginx.
DeepSeek Q4/IQ3, mag-model-gate und mag-model-pin sind aus dem aktiven Stack
ausgemustert. Gewichte bleiben auf der Platte. Bei Ornith-Ausfall:
`sudo bash ops/ornith/rollback.sh qwen` (Qwen allein, kein DeepSeek).
Deploy ausschließlich nach grüner lokaler CI: `bash ops/update.sh --no-pull`
im passenden Checkout; darin enthalten ist `sudo bash ops/deploy.sh`.
Das Retirement läuft nur bei gesundem Ornith-Raum und beiden Backends.
Alte 3.8-/Q4-/IQ3-Abschnitte im folgenden Text sind **historisch**.


## Runtime boundaries
- **nginx** is the versioned private reverse proxy for `mag.stardenburg.de`. Tailnet clients use **100.123.114.16:443**; tailscaled forwards to nginx on **127.0.0.1:4443**, where SNI selects the room and the other tailnet names. It authenticates every client via `auth_request` to native magroom (127.0.0.1:8766/_internal/tailscale/whois), which queries tailscaled LocalAPI over its Unix socket. `/_mag_whois` has `auth_request off` to prevent recursive subrequests. Whois failure must fail closed.
- **mag-room** is a C++17 HTTP/SSE service on **127.0.0.1:8766**. It verifies owner/guest permissions, owns the queue, scheduler, jobs, transcript, per-bot state, and the OpenAI-compatible tool loop. Client `X-Tailscale-*` headers are untrusted; nginx replaces them with the native endpoint's response. Non-loopback direct requests are denied.
- **Ornith** runs as two NUMA-bound llama.cpp servers (:8191/:8192) with four stable bot slots. DeepSeek :8090 and its units are retired. Only the audit gateway (:8080) reaches the model ports. The Qwen unit is the independent rollback.
- **Bot execution** uses an isolated KVM guest, browser/controller and terminal per bot. WebRTC and UDP relay use separate gateway processes and per-VM keys.
- **Jobs MCP** and the UI share `jobs.hpp` validation, store, scheduler and histories; no second application job queue.

## Data invariants
`/var/lib/magbot/room` contains state, jobs, `queue.json`, transcripts, memory and credentials not checked into Git. All queue mutations are server-persisted, SSE-notified and replayable across clients. A per-answer cancel differs from stopping the whole room. Prompt cache reuse depends on a stable system/tool prefix and stable model slot; volatile context belongs at the tail.

## Owner cutover safety
The nginx ingress switch is staged with a *180-second transient systemd rollback timer* before reloading nginx. A second Tailscale device must check `/api/state` and verify the **exact owner login and role**. Until `confirm.sh --owner-verified` is run, rollback is automatic. Caddy retirement must happen **only after confirmation**. Tailscale Serve :8446 is an independent fallback and must not be touched. A successful local host curl does not prove Tailnet identity.
