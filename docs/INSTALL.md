# Host installation — private nginx access, fail-safe

**Owner:** Ali. **Target:** `mag`. **Base release:** `ad0ad69` (includes #22, #23, #24 and #25) plus this security PR after CI passes. Nothing here modifies model weights or restarts `mag-llm`.

## 0. Keep a rescue channel
Maintain an open existing SSH/Tailscale session to MAG and a *separate* Tailnet client logged into Chris's real Tailscale identity. Ensure the old `https://mag.tail3e02ae.ts.net:8446/` endpoint remains independent and reachable. Do not do this from S23; use a desktop or second authenticated client. Do **not** remove legacy Caddy yet.

## 1. Read-only host inventory
From the checked-out, reviewed PR (after merge):
```bash
sudo bash ops/private-access/preflight.sh
sudo nginx -t
sudo systemctl status nginx tailscaled mag-120 mag-llm --no-pager
sudo grep -E 'ssl_certificate(_key)? ' /etc/nginx/sites-available/mag-room.conf
sudo ls -l /etc/mag-acme/lego/certificates/mag.stardenburg.de.*
```
The target is **100.123.114.16:443 → 127.0.0.1:8766**, native magroom Whois at **127.0.0.1:8766/_internal/tailscale/whois**, domain `mag.stardenburg.de`. Check for any other nginx virtual hosts bound to 100.123.114.16:443; an overlapping `server_name` is a blocker. The existing `/etc/mag-acme/lego.env` and acme-dns storage are root-owned host state and **not overwritten** by the installer.

**Owner allowlist required before cutover:** Check `/var/lib/magbot/room/access.json` on MAG. It must contain `owners: ["<actual LoginName from tailscale whois>"]` (no guessed addresses or CI placeholder); verify file owner/permissions and that Chris's real login is present. The installer refuses a missing or invalid allowlist. The template `ops/private-access/access.json.example` is **not** an authoritative identity list and must never be copied unedited into production.

## 2. Prepare production TLS separately
If lego already has `/etc/mag-acme/lego/certificates/mag.stardenburg.de.crt` and `.key`, stage validated copies **without reloading nginx**:
```bash
sudo bash ops/private-access/deploy-hook.sh --no-reload
sudo openssl x509 -in /etc/mag-nginx/tls/cert.pem -noout -dates -checkhost mag.stardenburg.de
```
If lego certificates are missing, repair the DNS-01 `acme-dns` delegation/credentials and issue through `issue-cert.sh staging` then `issue-cert.sh production` under operator control. Renewal selects `lego renew` where lego v4 exposes it, or `lego run` on lego v5 (which removed the old `renew` command). The production deploy-hook always targets nginx. **Do not proceed unless the domain, certificate and key match.**

## 3. Stage nginx + 180-second automatic rollback
```bash
sudo bash ops/private-access/install.sh
```
**First deploy the new native magroom binary via the normal room-only release (`mag-120`, never `mag-llm`) and ensure magroom can access `/var/run/tailscale/tailscaled.sock`.** `install.sh` checks that the C++ endpoint is already active and fails before modifying nginx otherwise. This backs up the previous vhost to `/etc/nginx/sites-available/mag-room.conf.pre-whois`, preserves its enabled symlink state, does not create any Python/service helper, verifies TLS and nginx config, **arms a transient rollback timer before nginx reload**, then reloads **nginx only**. No secrets or Caddy binaries are changed. An unconfirmed cutover restores the earlier vhost after 180 seconds.

## 4. External Owner verification — required before disarming
Immediately, **from a different authenticated Tailscale client**:
```bash
curl --fail-with-body --silent --show-error https://mag.stardenburg.de/api/state
```
Inspect `you.role == "owner"` and the exact expected Tailscale `you.login`. Confirm UI and authenticated SSE, not just TLS health. A 401/403/5xx, missing identity, guest role or wrong login means **do not confirm**. Run `sudo bash ops/private-access/rollback.sh` immediately or let the timer restore it. An on-host localhost request cannot prove this check.

Only after a successful Owner check:
```bash
sudo bash ops/private-access/confirm.sh --owner-verified
sudo systemctl is-active mag-acme-renew.timer
```

## 5. Verify guest access, then retire Caddy
From a separate **guest** Tailnet identity, verify shared chat and sender name; admin/job/memory/stop remain forbidden. Confirm nginx whois failure returns denial (arrange a guarded outage drill without changing `mag-llm`), and that `/api/events` streams without buffering. Then:
```bash
sudo bash ops/private-access/remove-caddy.sh
sudo nginx -t
sudo systemctl status nginx mag-120 mag-acme-renew.timer --no-pager
```
The legacy `mag-whois.service` and `tailscale-whois.py` host copies are disabled and removed by `confirm.sh` only after Owner verification. Caddy unit, `/etc/mag-caddy`, and its binary are removed only after confirmed success; if another active Caddy service shares the binary, it is deliberately retained and reported for manual review. The independent Tailscale Serve :8446 route and `mag-llm` remain untouched.

## 6. Recovery
If the Owner check fails or SSH disconnects **before confirmation**, the 180-second rollback timer restores the previous vhost. An operator may recover immediately:
```bash
sudo bash ops/private-access/rollback.sh
sudo nginx -t
```
Never remove `.pre-whois` until the new ingress is proven over real Tailnet sessions. If rollback itself cannot reload nginx, use the persistent SSH/rescue route to inspect `journalctl -u nginx` and restore the exact backup; never restart `mag-llm`.
