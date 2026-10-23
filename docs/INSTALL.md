# Blank host -> running MAG (`ops/bootstrap.sh`)

One script turns a blank Ubuntu 24.04 server (same hardware as MAG: 2 sockets,
2 NUMA nodes, ~512 GiB RAM) into a running MAG. The only inputs are a verified
backup archive and/or a few credential files. Nothing secret is in Git, on a
command line or in the log.

The older nginx cutover/hotfix guide from 2026-10-17 (Caddy -> nginx) is kept
for reference in [PRIVATE-ACCESS-CUTOVER.md](PRIVATE-ACCESS-CUTOVER.md).

## 1. What you provide

| Input | Where | Needed for |
|---|---|---|
| Verified full archive (recommended) | `/var/backups/mag-full/<id>/` copied from the old host (contains `VERIFIED` and `repo/`) | room history, Gitea, Pages, ACME account + certificates, Tailscale node identity (keeps Tailnet IP `100.123.114.16`), `/etc/credstore`, audit CA, `/etc/mag-stack/secondary-model.env` |
| Tailscale auth key (only without archive) | `/etc/credstore/tailscale.authkey` (root, 0600) | `tailscale up --auth-key file:...` |
| acme-dns credentials (only without archive) | `/etc/credstore/mag-acme-dns.json` (root, 0600) | lego DNS-01 certificate for `mag.stardenburg.de` |
| Guest OAuth secret (optional) | `/etc/credstore/mag-guest-oauth` | guest invitations ([guest-access.md](guest-access.md)) |
| Gitea mirror tokens (optional) | `/etc/credstore/gitea-mirror.token`, `/etc/credstore/github-mirror.token` | Gitea pull mirror of the GitHub repo |
| GitHub runner token (optional) | `/etc/credstore/mag-github-runner.token` | self-hosted CI runner ([RUNNERS.md](RUNNERS.md)) |
| Pages config (optional) | `/etc/mag-pages/pages.env` + `gitea-token` (from the archive) | Gitea Pages |
| Room owner (only without archive) | `--owner <tailscale-login>` | `/var/lib/magbot/room/access.json` |

## 2. Run it

```bash
sudo apt-get update && sudo apt-get install -y git
sudo useradd --create-home --shell /bin/bash chris      # the checkout owner
sudo -u chris git clone https://github.com/ChristianHohlfeld/mag-portable-stack /home/chris/mag-stack
cd /home/chris/mag-stack
bash ops/bootstrap.sh --dry-run                          # every phase, no changes
sudo bash ops/bootstrap.sh --archive /var/backups/mag-full/<id> --download-models
#   or, without an archive:
sudo bash ops/bootstrap.sh --owner you@example.com --download-models
```

Options: `--start-llm` (start mag-llm and the NUMA1 model at the end, only if
inactive), `--no-golden`, `--no-llm-build`, `--no-deploy`,
`--restore-host-config` (legacy: also copy `etc/nginx`, `etc/systemd/system`,
`etc/cron.d` from the archive). `MAG_BUILD_JOBS=N` limits compile jobs.

Bootstrap refuses to run when `mag-120` or `mag-llm` is already active (use
`ops/deploy.sh` / `ops/update.sh` on a running host). It never restarts or
stops mag-llm and never installs or enables console lockdown.

## 3. Phases

| Phase | What happens |
|---|---|
| release-check | Ubuntu 24.04 (noble) required (`MAG_ALLOW_OTHER_RELEASE=1` overrides) |
| packages | build tools, nginx, nftables, libvirt/QEMU (`qemu-system-x86`, `virtinst`, `cloud-image-utils`, `dnsmasq-base`), Go, Node, python3-venv |
| restic | pinned restic 0.18.1 |
| users | `magbot` (uid 993), `chris` |
| restore / place-state | archive -> staging; only **state and secrets** are placed (room, Gitea, Pages, ACME, nginx TLS, credstore, audit, `/etc/mag-stack`, cron spools). Existing files win; directories are merged one level deep |
| config | repo defaults: `/etc/mag-llm/llm.env`, `/etc/mag-acme/lego.env` (from `ops/private-access/env.conf`), acme-dns credentials from credstore, `access.json` from `--owner` |
| tailscale | `ops/tailscale/install.sh`: install, restore node state, or join with the credstore auth key |
| lego | `ops/private-access/install-lego.sh`: lego v5.5.2, SHA256-pinned |
| gitea / gitea-mirror | Gitea 28.1.0 (pinned); pull mirror when both mirror tokens exist |
| build | `MAG_BUILD_TARGETS` (`ops/build-targets.sh`) + WebRTC gateway, as the checkout owner |
| voice | `ops/bootstrap-voice.sh` as chris: whisper.cpp `ops/whisper.lock` (v1.9.5 @ d1be6fd), ggml-large-v3-turbo SHA256-pinned |
| llm-runtime | `ops/deepseek/bootstrap-runtimes.sh`: llama.cpp b11683 + patches, ik_llama 8d21687 + patches, Qwen llama.cpp only if selected; renders `mag-llm.service` via `install-deepseek.sh` when absent; installs the NUMA1 unit |
| golden | `ops/vm/build-golden.sh`: bot VM base image from a pinned Ubuntu cloud image, only when absent |
| backup-install | local restic timers + first snapshot |
| audit / gateway / tailscale-egress | native audit gateway, mitmproxy web proxy + CA, VM package, nftables |
| acme | repo ACME scripts/units; issues the certificate when it is missing and acme-dns credentials exist |
| models | `--download-models`: pinned GGUF shards, SHA256-verified |
| deploy | `ops/deploy.sh` (first-install aware: nothing to snapshot, no old room to probe) |
| pages, auto-update, runners | `ops/pages/install.sh` (if configured), `ops/auto-update/install.sh`, `ops/runners/install.sh` |

Configuration that is not secret comes from the repo, not from the archive:
systemd units (deploy, installers), the nginx vhost (`deploy-nginx.sh`), ACME
units, env defaults. Re-running an installer re-applies the repo state.

## 4. Models (repo vs. live)

* NUMA node 0: **DeepSeek-V4-Flash-0731 UD-Q4_K_XL** as `mag-llm.service`
  (ik_llama, `:8090`, behind the audit gateway on `:8080`). Pinned shards and
  SHA256: `ops/backup/models.sha256`.
* NUMA node 1: chosen by `/etc/mag-stack/secondary-model.env`.
  * absent or `MAG_SECONDARY_MODEL=iq3` (**repo default**): DeepSeek IQ3_XXS,
    `mag-llm-iq3.service`, `:8091` (`ops/backup/models-iq3.sha256`).
  * `MAG_SECONDARY_MODEL=qwen` (**live on MAG** as of the October 2026 status): Qwen3.8
    Flash Next IQ4_XS, `mag-llm-qwen.service` ([ops/qwen/README.md](../ops/qwen/README.md)).
  Bootstrap never writes this file; it comes from the archive. With `qwen`
  it builds the pinned Qwen runtime, but the Qwen GGUF has no pinned download
  in the repo yet: download it, run `ops/qwen/pin-model.py`, then
  `sudo bash ops/qwen/install.sh stage`. `--download-models` fetches only the
  DeepSeek Q4 + IQ3 shards.
* The DeepSeek chat template is fetched by `install-deepseek.sh` from the
  llama.cpp commit of the built binary (or restored from the archive under
  `/home/chris/llama-templates`).
* First start: `mag-model-gate` starts `mag-llm` only after the full SHA-256
  check of the shards. `--start-llm` starts it directly if inactive.

### NUMA placement: unit template vs. llm.env

`ops/mag-llm.service` still documents `numactl --interleave=all` (the pre-3.8
layout). The unit that is actually installed is **rendered** by
`render-llm-unit.sh` from that template plus `/etc/mag-llm/llm.env`;
`ops/deepseek/llm.env.mag` sets `ENGINE=ik` and
`NUMA_ARGS="--cpunodebind=0 --membind=0"`, so the rendered unit binds Q4 to
node 0 and leaves node 1 to the secondary model. Live MAG uses this node-0
binding. Nothing in bootstrap changes a rendered unit that already exists.

## 5. After bootstrap

```bash
curl -s http://127.0.0.1:8766/readyz            # room
curl -s http://127.0.0.1:8766/readyz/model      # model through the gateway
systemctl status mag-120 mag-audit-gateway nginx tailscaled --no-pager
tailscale ip -4                                 # must be 100.123.114.16
```

* **Tailnet IP:** nginx (`ops/private-access/mag-room.nginx.conf`) and Pages
  listen on `100.123.114.16`. A restored Tailscale state keeps that IP; a
  fresh auth key gives a new IP, and the vhost/pages.env must then be changed
  deliberately (the Tailscale installer warns).
* **Owner probe:** an optional second Tailnet device for the nginx smoke
  (`/etc/mag-stack/deploy-owner-probe.conf`, see
  [owner-probe-userspace.md](owner-probe-userspace.md)).
* **Bot VMs:** need `golden.qcow2` (built in phase golden; Chrome stable is
  whatever Google ships on build day, recorded in `golden.qcow2.manifest`).
* **CI:** see [RUNNERS.md](RUNNERS.md) (billing, runner token, `MAG_RUNS_ON`).

## 6. Tested how

`ops/tests/test-bootstrap.sh` checks every phase and order in a dry run (CI).
`ops/tests/e2e-bootstrap.sh` runs the real bootstrap in a fresh Ubuntu 24.04
system container with `--test-stubs` (stub LLM, stub whisper model, no golden
image, no models) and checks `/readyz`, `/readyz/model`, the nginx ingress and
that mag-llm stayed untouched (`.github/workflows/e2e-bootstrap.yml`). Not
covered there: KVM bot VMs, real models, ACME issuance, Tailscale login.
