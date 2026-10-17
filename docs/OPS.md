# MAG operations & release validation

## Daily diagnostics
```bash
sudo systemctl status nginx mag-120 mag-acme-renew.timer --no-pager
sudo nginx -t
sudo journalctl -u nginx -u mag-120 --since -30min --no-pager
curl -fsS http://127.0.0.1:8766/healthz
```
The loopback health check confirms only backend health, **not** Tailnet Owner identity. External `/api/state` identity must be checked with a real authenticated second client.

## nginx/TLS invariants
Versioned server block: `ops/private-access/mag-room.nginx.conf` corresponds to `/etc/nginx/sites-available/mag-room.conf`. Tailnet bind is **100.123.114.16:443**, upstream **127.0.0.1:8766**, whois **127.0.0.1:8766/_internal/tailscale/whois** (native C++/Unix LocalAPI; no helper daemon). The global `auth_request` fails closed; internal `/_mag_whois` must explicitly set `auth_request off` to prevent recursion. Strip inbound `X-Tailscale-*` and insert only whois result. HTML, manifest, SW and APIs have `no-store`; SSE has `proxy_buffering off`.

Certificate source: lego/acme-dns, `/etc/mag-acme/lego/certificates/mag.stardenburg.de.{crt,key}` (credentials in `/etc/mag-acme/acme-dns.json`); deployed files `/etc/mag-nginx/tls/{cert.pem,key.pem}`. Use `deploy-hook.sh --no-reload` before cutover; normal hook validates name, expiry and key pair, then `nginx -t && systemctl reload nginx`. Timer invokes the version-aware wrapper: lego v4 `renew`, lego v5 `run` (which also renews).

## Incidents and rollback
The installer arms `mag-private-rollback.timer` for 180 seconds. `confirm.sh --owner-verified` is an operator attestation after **external** Owner validation; it stops that timer. Without confirmation the previous nginx config and enabled symlink state are restored by `rollback.sh`. On failed external check, manually execute rollback without waiting.

Old Caddy removal is gated on `/var/lib/mag-private-cutover/confirmed`. Do not remove Caddy first, do not delete the backup before testing, and do not confuse a 2xx TLS response with a real Owner identity.

## Quality gates
Run native tests, `test_private_nginx.py` (mock upstream + real TLS nginx), PWA/paste emulation at 375/390/412 px, gitleaks, Go gateway tests, nginx syntax and deployed Owner/Guest/fail-closed acceptance tests. CI cannot inspect MAG's actual TLS/network/device setup. Never restart the warmed `mag-llm` process during this release.
