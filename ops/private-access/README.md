# Private access: https://mag.stardenburg.de (tailnet only)

- DNS (DomainFactory): `A mag.stardenburg.de -> 100.123.114.16` (Tailscale IP, unreachable from the internet),
  `CNAME _acme-challenge.mag.stardenburg.de -> <acme-dns fulldomain>.auth.acme-dns.io`.
- Certificate: lego (DNS-01 via acme-dns, https://auth.acme-dns.io). Credentials live only on mag in
  `/etc/mag-acme/acme-dns.json` (root, 0600, lego ACME_DNS_STORAGE_PATH format). Never in this repo.
- TLS proxy: existing nginx 1.24+ server, bound only to 100.123.114.16:443 -> 127.0.0.1:8766
  (WebSocket/SSE pass through, flush_interval -1). `tailscale serve :8446` stays untouched.
- Origin check: the room compares `Origin` with `Host`; nginx forwards the original Host, so no code change.
- Renewal: `mag-acme-renew.timer` (twice daily, lego decides when to renew; deploy hook validates certificates and reloads nginx).
- Identity: nginx `auth_request` calls mag-whois using the real socket peer; 2xx is required, otherwise the external request is blocked:
  client `X-Tailscale-*` headers are deleted, `forward_auth` calls `mag-whois`
  (`tailscale-whois.py` on 127.0.0.1:8765), which asks tailscaled LocalAPI
  `whois?addr=<client-ip>:<port>` and returns `X-Tailscale-User` (LoginName),
  `X-Tailscale-Login` and `X-Tailscale-Name` (display name, percent-encoded).
  A non-2xx from the helper fails closed (no anonymous pass-through).
  magroom trusts those headers only when the TCP peer is 127.0.0.0/8.
  Any other peer is denied, even with the same headers. Loopback without a
  header stays the owner (deploy, curl, tests). `tailscale serve :8446` is not used.
- Roles: `/var/lib/magbot/room/access.json` (template `access.json.example`, never
  committed with a real login). `owners` is the Tailscale login. Permissions
  `offline`, `stop`, `jobs`, `memory`, `reset`, `delete`, `autotalk`,
  `connectors`, `browser` are owner-only unless the file lists `"guest"`.
  The file is re-read when it changes; mag-llm is not involved.

Install / issue:
    sudo ops/private-access/install.sh               # binaries are installed separately, see install.sh
    sudo ops/private-access/issue-cert.sh staging    # test against LE staging
    sudo ops/private-access/issue-cert.sh production
Rollback: `sudo ops/private-access/rollback.sh`
