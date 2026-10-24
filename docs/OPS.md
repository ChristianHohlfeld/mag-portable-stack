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

Certificate source: lego/acme-dns, `/etc/mag-acme/lego/certificates/mag.stardenburg.de.{crt,key}` (credentials in `/etc/mag-acme/acme-dns.json`); deployed files in **both** `/etc/nginx/mag-tls/{cert.pem,key.pem}` (old live nginx) and `/etc/mag-nginx/tls/{cert.pem,key.pem}` (cutover nginx). The deploy-hook validates name/expiry/key, stages both and restores both on failure; only then runs `nginx -t` and reloads. lego **5.5.2** always uses `lego run` with command-first flags, for both issuance and renewal.

## Incidents and rollback
The installer arms `mag-private-rollback.timer` for 180 seconds. `confirm.sh --owner-verified` is an operator attestation after **external** Owner validation; it stops that timer. Without confirmation the previous nginx config and enabled symlink state are restored by `rollback.sh`. On failed external check, manually execute rollback without waiting.

Old Caddy removal is gated on `/var/lib/mag-private-cutover/confirmed`. Do not remove Caddy first, do not delete the backup before testing, and do not confuse a 2xx TLS response with a real Owner identity.

## Quality gates
Run native tests, `test_private_nginx.py` (mock upstream + real TLS nginx), PWA/paste emulation at 375/390/412 px, gitleaks, Go gateway tests, nginx syntax and deployed Owner/Guest/fail-closed acceptance tests. CI cannot inspect MAG's actual TLS/network/device setup. Never restart the warmed `mag-llm` process during this release.

## Bot VMs: add, delete, reset (`magbotctl` = `ops/magbot`)

One CLI owns the whole lifecycle of a bot VM. Everything a bot owns is named
`magbot-<name>`: libvirt domain, `$MAGBOT_ROOT/magbot-<name>/` (overlay
`magbot-<name>.qcow2` backed by the read-only `base/golden.qcow2`, NoCloud
seed `magbot-<name>-seed.iso`, `metadata.json`), systemd units
`magbot-<name>[-*]`, and the restricted Gitea user `magbot-<name>`.

    sudo magbotctl create <name>   # clean VM from golden; atomic
    sudo magbotctl verify <name>   # hard check, non-zero on any failure
    sudo magbotctl reset  <name>   # delete + create: memory/workspace wiped
    sudo magbotctl delete <name>   # sweep by prefix; idempotent, no leftovers
    sudo magbotctl list

`ops/deploy.sh` installs it as `/usr/local/bin/magbotctl`, a symlink into
`/opt/mag-stack/current/ops/` (follows `ops/rollback.sh`).
`/usr/local/bin/magbot` remains the native `magbot-native` binary used for
guest actions (exec, browser, audit proxy), which reads both registries.

- `create` builds the golden image with `ops/vm/build-golden.sh` if it is
  missing, creates the overlay (the golden is never written), generates the
  Gitea credential and writes it, `/home/bot/MAG_CONTEXT.md`
  (`mag-room/native/bot_context.md`) and `/etc/magbot/llm.env` into the seed
  ISO (root:kvm 0640; credentials are never printed), boots, waits for
  cloud-init and runs `verify`. Any failure rolls back with a full `delete`
  sweep, so a half-created bot cannot remain. Leftovers found before a create
  are swept first.
- `verify` on a present bot: metadata `ready`, domain running, overlay backed
  by the read-only golden, cloud-init done, `MAG_CONTEXT.md` present, LLM
  endpoint reachable from the guest when `MAGBOT_LLM_URL` is set. On
  an absent bot: nothing with the prefix remains.
- create/delete/reset run as transient unit `magbot-<name>-job` (follow with
  `journalctl -u magbot-<name>-job`); `MAGBOT_FOREGROUND=1` runs inline. A
  per-bot flock serialises operations on one name, a global flock covers the
  golden build/admission. While a job runs, a second one for the same name
  fails fast because the transient unit already exists.
- Site settings go in `/etc/magbot/magbot.env` (`MAGBOT_LLM_URL`,
  `MAGBOT_VCPUS`, `MAGBOT_RAM_GIB`, `MAGBOT_DISK_GIB`, `MAGBOT_NETWORK`,
  `MAGBOT_GITEA=none` on hosts without Gitea).
- LLM endpoint: bots reach the model through the room (host audit gateway
  `127.0.0.1:8080`); no LLM port is open to `virbr0` (`mag-llm` is
  `127.0.0.1:8090`, Qwen `127.0.0.1:8091`; `192.168.122.1:8091` is the bot
  site publisher). `MAGBOT_LLM_URL` is therefore empty by default; set it in
  `/etc/magbot/magbot.env` only together with a matching firewall rule. See
  `ops/magbot.env.example` (installed once, never overwritten).
- Room buttons: "Bot hinzufügen" / "Bot zurücksetzen" / "Bot löschen" call
  `sudo magbot-agent-ctl spawn|reset|despawn bot<N>`, which runs
  `magbotctl create|reset|delete` as the transient job. The room never
  waits: the bot shows "VM wird erstellt …" until the job finishes, and a
  failure appears as an error in the room (create/reset roll back, so
  nothing is left). Reset also wipes the bot's memory. New VMs are named
  `bot<N>`.
- The legacy room bots `agent1`/`agent3` are migrated once by
  `ops/migrate-legacy-bots.sh` (run by deploy): legacy teardown via
  `magbot-native despawn`, then a fresh CLI bot with the same name
  (workspace wiped) and `magbotctl verify`. A failure stops before the next
  bot; a rerun resumes. Stamp: `/var/lib/mag-stack/migrate-legacy/done`.
  Afterwards all room bots (`agent<N>`, `bot<N>`) are CLI bots; drift removes
  unreferenced ones via `magbotctl delete`.
- Tests: `bash ops/tests/test-magbot.sh` (fake virsh/qemu-img/cloud-localds,
  no KVM). `FAIL_AT=<step>` injects a failure after a create step.
