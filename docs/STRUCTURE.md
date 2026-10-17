# Repository structure (MAG, October 2026)

```text
mag-room/native/
  magroom.cpp            model loop, scheduler coordination, streaming
  routes.hpp             authenticated HTTP handlers including /api/cancel
  jobs.hpp               single shared jobs store / scheduler
  jobs_tools.hpp         fixed registered bot tool definitions
  jobs_mcp.hpp           MCP adapter through same jobs service
  access.hpp             owner/guest role checks
  tailscale_auth.hpp     native Unix-socket whois and nginx auth_request endpoint
  browser.cpp            CDP browser controller, one per VM
  vm.cpp                 guarded VM lifecycle
mag-room/web/index.html one-file responsive application and local assets
mag-room/web/sw.js      PWA service worker
mag-room/static/        local fonts, icons, xterm and PWA assets
mag-room/webrtc/        Go/WebRTC bridge to per-VM UDP
mag-room/tests/         native, PWA, reaction, paste, browser, permission tests
ops/private-access/     nginx, lego/acme-dns hooks (no helper daemon),
                        preflight/install/confirm/rollback/remove-caddy
ops/                   room deployment, rollback, health, systemd manifests
docs/                  architecture, structure, code, hardware, install, ops
.github/workflows/      native + separate private-nginx/security checks
```

The working source of MAG is `ChristianHohlfeld/mag-portable-stack`. `ChristianHohlfeld/stardenburg.de` holds complementary host deployment/docs changes; avoid treating its mirrored room sources as a second truth. No production secrets, private keys, model GGUFs or user-data snapshots in Git.
