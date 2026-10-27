# MAG private ingress (nginx / Tailnet)

Blank-host installation: [docs/INSTALL.md](../../docs/INSTALL.md). Historical nginx cutover/hotfix guide: [docs/PRIVATE-ACCESS-CUTOVER.md](../../docs/PRIVATE-ACCESS-CUTOVER.md). Operational verification: [docs/OPS.md](../../docs/OPS.md).

- DNS: `mag.stardenburg.de → 100.123.114.16`, only reachable on Tailnet; TLS from lego DNS-01/acme-dns. No keys in Git.
- Clients open `https://mag.stardenburg.de/` on `100.123.114.16:443`. tailscaled owns that address and forwards raw TLS to nginx `127.0.0.1:4443` (PROXY protocol). nginx proxies to `127.0.0.1:8766`, with `auth_request` to **magroom C++** `127.0.0.1:8766/_internal/tailscale/whois`. The internal `/_mag_whois` location sets **`auth_request off`** to prevent recursive authentication. Headers `X-Tailscale-*` are overwritten, and all Whois failures deny external access. SSE buffering is disabled; app shell, SW, manifest and API responses are `no-store`.
- The host keeps its existing `/etc/mag-acme/lego.env` and credentials at `/etc/mag-acme/acme-dns.json`; the installer never replaces them.
- `preflight.sh` is read-only. `deploy-hook.sh --no-reload` stages validated TLS key/cert in BOTH the old live `/etc/nginx/mag-tls` and new `/etc/mag-nginx/tls` locations without reloading; the normal hook reloads only after active `nginx -t` succeeds.
- `install.sh` snapshots the previous nginx vhost (including mode), enabled symlink, ACME scripts and renewal units. It runs `nginx -t` **with the real quoted speech include** before installing any host ACME file, then arms a **180-second automatic rollback** before reloading nginx. A successful rollback restores all files and removes stale backups/retry blockers. Run `confirm.sh --owner-verified` *only* after `/api/state` on another authenticated Tailscale device reports the correct **Owner role and login**.
- If no confirmation or wrong identity: `rollback.sh` restores old nginx without touching mag-llm. `remove-caddy.sh` rejects unconfirmed migrations, removes MAG Caddy unit/config, and removes its binary only when no other active Caddy service uses it.
- Tailscale Serve :8446 remains untouched as independent recovery access. Never restart `mag-llm`.

**Operator sequence:** `sudo bash ops/private-access/preflight.sh`; `sudo bash ops/private-access/deploy-hook.sh --no-reload`; `sudo bash ops/private-access/install.sh`; check role from another device; `sudo bash ops/private-access/confirm.sh --owner-verified`; guest verification; `sudo bash ops/private-access/remove-caddy.sh`.

CI runs real C++ magroom against a fake tailscaled Unix LocalAPI and a throwaway nginx + mock auth/backend, testing fail closed, header spoofing and no-store, in addition to native room regression tests and a secrets scan. It does *not* replace the host Owner/Guest test.

## Automatischer Release-Cutover ab 8e2d045

`ops/deploy.sh` verwendet `deploy-nginx.sh` als transaktionalen vHost-Updater. Es prüft die wirksame nginx-Auth-Konfiguration und einen echten Tailnet-Owner per SSH-Probe, statt nur `/readyz` zu prüfen. Fehlende Credentials stoppen ausschließlich Gäste-Einladungen. Bei Owner-403/5xx werden nginx und Room gemeinsam zurückgerollt. Manuell idempotent: `sudo bash ops/private-access/deploy-nginx.sh standalone`. Für vollständige Einrichtung und geschützte `/etc/credstore/mag-guest-oauth`-Konfiguration siehe [docs/guest-access.md](../../docs/guest-access.md).
\n**Userspace-Sonderfall (grokbot-box-2):** Die strikte externe Owner-Probe unterstützt optional einen auf der Box lokal gebundenen Tailscale-SOCKS5-Proxy, ohne die nginx-/C++-Whois-Anforderungen abzusenken. Einrichtung und Bedingungen: [docs/owner-probe-userspace.md](../../docs/owner-probe-userspace.md).\n