# MAG Architecture — release dcdd78a + private ingress hardening

## Runtime boundaries
- **nginx** is the versioned private reverse proxy for `mag.stardenburg.de` on **100.123.114.16:443** (Tailnet only). It authenticates every client via `auth_request` to native magroom (127.0.0.1:8766/_internal/tailscale/whois), which queries tailscaled LocalAPI over its Unix socket. `/_mag_whois` has `auth_request off` to prevent recursive subrequests. Whois failure must fail closed.
- **mag-room** is a C++17 HTTP/SSE service on **127.0.0.1:8766**. It verifies owner/guest permissions, owns the queue, scheduler, jobs, transcript, per-bot state, and the OpenAI-compatible tool loop. Client `X-Tailscale-*` headers are untrusted; nginx replaces them with the native endpoint's response. Non-loopback direct requests are denied.
- **mag-llm / ik_llama.cpp** is a separate warmed DeepSeek-V4-Flash inference process. Room, nginx and certificate work must *never restart it*. `--jinja` tool outputs are interpreted by the same room tool loop.
- **Bot execution** uses an isolated KVM guest, browser/controller and terminal per bot. WebRTC and UDP relay use separate gateway processes and per-VM keys.
- **Jobs MCP** and the UI share `jobs.hpp` validation, store, scheduler and histories; no second application job queue.

## Data invariants
`/var/lib/magbot/room` contains state, jobs, `queue.json`, transcripts, memory and credentials not checked into Git. All queue mutations are server-persisted, SSE-notified and replayable across clients. A per-answer cancel differs from stopping the whole room. Prompt cache reuse depends on a stable system/tool prefix and stable model slot; volatile context belongs at the tail.

## Owner cutover safety
The nginx ingress switch is staged with a *180-second transient systemd rollback timer* before reloading nginx. A second Tailscale device must check `/api/state` and verify the **exact owner login and role**. Until `confirm.sh --owner-verified` is run, rollback is automatic. Caddy retirement must happen **only after confirmation**. Tailscale Serve :8446 is an independent fallback and must not be touched. A successful local host curl does not prove Tailnet identity.
