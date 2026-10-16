# Private access: https://mag.stardenburg.de (tailnet only)

- DNS (DomainFactory): `A mag.stardenburg.de -> 100.123.114.16` (Tailscale IP, unreachable from the internet),
  `CNAME _acme-challenge.mag.stardenburg.de -> <acme-dns fulldomain>.auth.acme-dns.io`.
- Certificate: lego (DNS-01 via acme-dns, https://auth.acme-dns.io). Credentials live only on mag in
  `/etc/mag-acme/acme-dns.json` (root, 0600, lego ACME_DNS_STORAGE_PATH format). Never in this repo.
- TLS proxy: Caddy (official release binary), bound only to 100.123.114.16:443 -> 127.0.0.1:8766
  (WebSocket/SSE pass through, flush_interval -1). `tailscale serve :8446` stays untouched.
- Origin check: the room compares `Origin` with `Host`; Caddy forwards the original Host, so no code change.
- Renewal: `mag-acme-renew.timer` (twice daily, lego decides when to renew; deploy hook restarts Caddy).

Install / issue:
    sudo ops/private-access/install.sh               # binaries are installed separately, see install.sh
    sudo ops/private-access/issue-cert.sh staging    # test against LE staging
    sudo ops/private-access/issue-cert.sh production
Rollback: `sudo ops/private-access/rollback.sh`
