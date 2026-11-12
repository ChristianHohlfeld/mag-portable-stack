# Operating MAG

Day-to-day work on the running host. Architecture:
[ARCHITECTURE.md](ARCHITECTURE.md). First install:
[INSTALL.md](INSTALL.md). Rules for every change: [`AGENTS.md`](../AGENTS.md).

- [Deploy](#deploy)
- [Rollback](#rollback)
- [Models](#models)
- [Bot VMs](#bot-vms)
- [Backups](#backups)
- [Health and logs](#health-and-logs)
- [Access, TLS and ingress](#access-tls-and-ingress)
- [CPU and the MI50](#cpu-and-the-mi50)
- [Where state lives](#where-state-lives)
- [Never](#never)

## Deploy

```bash
cd ~/mag-stack && git checkout -q main && git pull -q --ff-only origin main \
  && taskset -c 28-55 sudo -n bash ops/deploy.sh
```

One run builds, takes a backup, stages `/opt/mag-stack/releases/<id>`
(`<id>` is the UTC time, `20261112T091500Z`), switches
`/opt/mag-stack/current`, restarts the room, its gateways and nginx, checks
`/readyz` and the ingress, and on any failure restores the previous release
and nginx. Then it installs the independent layers (models, STT, voices,
scenes, Idol, bot sites, timers); a failure there is a warning, not a
rollback. The deploy has a hard deadline of 25 minutes
(`MAG_DEPLOY_DEADLINE_S`) and one deploy runs at a time
(`/run/lock/mag-private-deploy.lock`).

What a deploy does not do:

- it never drains and never waits for a running answer, a model or a job;
- it never starts, stops or restarts a model; a changed model launch spec is
  reported as "restart pending" and applied with `mag-modeld ctl`;
- it never fails because a bot is downloading, loading, paused or stopped;
- it never costs a running MI50 job or film render: their workers restart
  only when their code changed, and their stages keep running
  (`KillMode=process`).

A turn the restart cut off: if nothing of the answer could be seen yet, the
bot answers after the restart; an answer that was cut off in view stays as it
is ("Gestoppt").

Release pruning keeps the new release and one fallback.

## Rollback

```bash
sudo bash ops/rollback.sh <RELEASE_ID>
```

`<RELEASE_ID>` is the release you want to leave (the deploy prints the
command at its end). It restores the units, `magroom`'s release link,
`magbot` and the audit gateway from `/var/backups/mag-stack/<RELEASE_ID>`,
restarts the room, the audit gateway and the WebRTC gateway, rolls nginx
back if that release changed it, and waits for `/readyz`. Room data is not
rolled back; the copy taken before that deploy stays in
`/var/backups/mag-stack/<RELEASE_ID>/room`. No model is touched.

## Models

```bash
/usr/local/lib/mag-models/mag-modeld status                 # both bots, as JSON
sudo /usr/local/lib/mag-models/mag-modeld ctl lena restart  # start | stop | restart | freeze | thaw
```

- `stop` frees the model's memory; the weights stay on disk. This is
  "switched off" in the bot's sheet and holds across reboots.
- `freeze` / `thaw` suspend and resume the server process.
- A pause (the dot before a bot's name, or its sheet) is a room setting:
  the model stays loaded and gets no requests.
- States in `/run/mag-models/status.json`: missing, downloading, verifying,
  loading, ready, frozen, stopped, failed. Downloads resume on their own
  (`mag-model-fetch@<bot>-<kind>.service`).
- To change a model or its arguments, edit `ops/models/models.json` in a
  pull request, deploy, then `mag-modeld ctl <bot> restart`.

Never start, stop or enable `mag-model@<bot>.service` by hand or from a
script, and never delete a model file, including retired ones.

The trash button erases the slot of every server and restores only the
saved prefix state; no model is reloaded.

## Bot VMs

Each bot's sheet in the room has its VM: the live browser and terminal, its
files, "Zurücksetzen" and "Sicherungen der VM". The command line needs root
and takes the VM name (`agent1`, `agent2`, …):

```bash
sudo magbot status                          # every VM: state, vCPUs, RAM, disk
sudo magbot saves agent1                    # saves of one VM, newest first (JSON)
sudo magbot save agent1                     # a save now (kind manual); the VM keeps running
sudo magbot save-all manual                 # a save of every VM
sudo magbot restore agent1 <SAVE_ID> <TOKEN>
sudo magbot doctor                          # KVM present, number of bots
```

- **Saves** are written to
  `/var/lib/libvirt/images/magbot/saves/<vm>/<stamp>-<kind>/` (compressed
  overlay and `META.json`). Kinds: `daily` (`mag-bot-saves.timer`, 04:15),
  `manual` (sheet or CLI), `chat` (backup on request),
  `vor-wiederherstellung` (the disk a restore replaced). The newest four are
  kept per VM. A save needs the overlay's size plus 30 GiB free.
- **Restore** from the sheet is the normal way: the room ends the bot's
  turn, keeps it out of the chat until the VM is back, and repeats the
  restore after its own restart. On the command line `<SAVE_ID>` is a
  directory name from `magbot saves` and `<TOKEN>` any 8-64 hex characters
  that name this restore (`openssl rand -hex 8`); running it again with the
  same token does nothing. The disk the restore replaces becomes a save of
  its own, so a restore can be undone by restoring that one.
- **Reset** ("Zurücksetzen" in the sheet, one question) gives the bot its
  first day: its turn ends, memories, prompt log and slot are emptied, the
  VM is deleted and made anew from the golden image under the same name,
  and its site is emptied. Nothing is archived; saves stay. Name, address,
  model and a pause stay as they were. Do it from the sheet: the room keeps
  the reset in the bot's record and finishes it after a restart.
- **Internet for bots** off is a host nft table (`magbot-netmode`), set in
  the settings.
- Test VM lifecycle changes on a disposable VM, never on Lena's or Noah's.

## Backups

What is in a backup: the room state, Gitea (database, repositories,
`app.ini`), nginx, TLS and ACME configuration, `/etc/magbot`,
`/etc/default/mag-room`, `/etc/mag-stack`, the audit configuration, the MAG
and Gitea units, cron and the Tailscale node state. What is not: model
files, VM disks (they have their own saves), the MI50 job folders, scene
job folders, `/opt` installs. Archives are local, under
`/var/backups/mag-full/<stamp>-<kind>/`, each a complete restic repository;
the last three verified ones and the current release's pre-deploy archive
are kept.

Timers: daily 03:30, weekly check Monday 04:30, monthly 10 % data check and
restore test on the 1st, hourly health monitor (unhealthy after 26 hours
without success). A deploy takes one before it switches.

**On request:** "Jetzt alles sichern" in the settings, or ask a bot (its
`backup` tool). That starts `mag-backup-chat.service` and
`mag-bot-saves@chat.service`. From a shell:

```bash
sudo systemctl start --no-block mag-backup-chat.service mag-bot-saves@chat.service
cat /var/lib/mag-backup/status.json
```

**Restore** (`/usr/local/lib/mag-backup/restore.sh`, also in
`ops/backup/restore.sh`). Parts: `room`, `gitea`, `pages`, `cron`,
`tailscale`, `config`, `all`. Always restore into a staging folder first:

```bash
sudo /usr/local/lib/mag-backup/restore.sh --part room --target /var/tmp/mag-restore
```

`--snapshot` takes `latest`, a restic snapshot id, or an archive name of
kind `daily`, `deploy` or `clear`. A live restore writes to `/` and needs
the affected service stopped first (`mag-120` for `room`, `gitea` for
`gitea`, `tailscaled` for `tailscale`):

```bash
sudo systemctl stop mag-120
sudo /usr/local/lib/mag-backup/restore.sh --part room --apply
sudo systemctl start mag-120
```

## The whole system (new hardware)

`ops/backup/system.sh` backs up all of mag for a restore on another machine:
the root file system (configuration, services, `/usr/local`, `/opt`,
`/var/lib`, `/home` with venvs and runtimes, `/root`), the golden image, the
VM archive and a fresh save of every bot VM, plus a description of the host
in `/var/lib/mag-backup/system-meta` (packages, disks, users, libvirt
definitions, a consistent Gitea database, the model manifest). Models and
weights only with `--with-models`, and never onto mag's own disk.

- When: weekly, Sunday 05:00 (`mag-backup-system.timer`); "Gesamtsystem
  sichern" in the settings; a bot's `backup` tool with scope `system`.
- Where, the first that applies: `MAG_SYSTEM_REPO` in
  `/etc/mag-backup/system.env` (any restic repository; a remote one with
  `RESTIC_PASSWORD_FILE`), a disk mounted at `/mnt/mag-backup`, else
  `/var/backups/mag-system` (two states, only with 170 GiB free for the
  first; this protects against mistakes, not against a broken disk).
- State: `/var/lib/mag-backup/system-status.json`, `journalctl -u mag-backup-system`.
- Restore on a fresh Ubuntu 24.04 from a checkout of this repository:
  `sudo bash ops/backup/system-restore.sh --repo REPO` (dry run), then with
  `--apply`, then reboot. Packages from the backup's list, the files without
  what belongs to the new machine (fstab, netplan, boot loader, apt/dpkg
  state), every VM from its newest save, models by their manifest.
- Without any backup, a fresh server is installed with `sudo ./install.sh`
  ([INSTALL.md](INSTALL.md)).

## Health and logs

```bash
sudo systemctl start mag-stack-check.service; journalctl -u mag-stack-check -n 20
curl -s http://127.0.0.1:8766/readyz
systemctl list-timers 'mag*'
```

`mag-stack-check.timer` runs `ops/health.sh` every minute. It fails only
when the room, nginx, tailscaled, the audit gateway or mag-modeld is down;
model states are printed, not judged.

| Unit | What to look for |
| --- | --- |
| `journalctl -u mag-120` | the room: turns, tools, VM calls, errors as one line |
| `journalctl -u mag-modeld` | downloads, verification, starts |
| `journalctl -u mag-model@lena`, `-u mag-model@noah` | llama-server load and timings |
| `journalctl -u mag-audit-gateway`, `-u mag-audit-webproxy` | the audit layer |
| `journalctl -u mag-idol` | MI50 jobs; a stage's own log is in its job folder |
| `journalctl -u mag-scene` | scene builds and film renders |
| `journalctl -u mag-stt` | dictation (numbers only, never text) |
| `journalctl -u mag-browser-webrtc` | VM live link |
| `journalctl -u mag-bots-publish` | bot site uploads |
| `journalctl -u 'mag-bot-saves@*'`, `-u 'magbot-save-*'`, `-u 'magbot-restore-*'` | VM saves and restores |
| `journalctl -u 'mag-backup-*'` | backups |
| `journalctl -u nginx`, `-u tailscaled`, `-u gitea`, `-u mag-gitea-runner` | ingress, Gitea, CI |

The audit log is below `/var/log/mag-audit` (members of
`mag-audit-readers`):

```bash
mag-audit-tail            # today, compact
mag-audit-tail -f         # follow
mag-audit-tail --verify   # check the hash chains
```

The journal never gets transcripts, tokens or what was dictated. If a
secret ever reaches it, `ops/security/secrets-scan.sh` finds where and
`ops/security/journal-purge.sh` removes it.

## Access, TLS and ingress

- **Who gets in.** Only the logins in `owners` of
  `/var/lib/magbot/room/access.json`. The room re-reads the file when it
  changes. There are no guests.
- **How.** Clients open `https://mag.stardenburg.de/` on the host's
  Tailscale address. tailscaled owns port 443 there and forwards raw TLS
  with PROXY v2 to nginx on 127.0.0.1:4443. nginx admits only tailnet
  addresses and checks every request with `auth_request` against the room's
  native whois (`/_internal/tailscale/whois`), which asks tailscaled's
  LocalAPI who the client is and signs the answer with an HMAC. Headers sent
  by a client are overwritten; a failed whois denies. The internal
  `/_mag_whois` location has `auth_request off` to avoid recursion.
- **The vhost** is `ops/private-access/mag-room.nginx.conf`, installed as
  `/etc/nginx/sites-available/mag-room.conf` by every deploy through
  `ops/private-access/deploy-nginx.sh` (preflight, stage, activate, smoke,
  commit, rollback). To re-apply it without a release:
  `sudo bash ops/private-access/deploy-nginx.sh standalone`. If
  `/etc/mag-stack/deploy-owner-probe.conf` exists and the nginx config
  changes, the smoke test is an owner request from that second tailnet
  device; otherwise it is the local ingress check.
- **Certificates.** The room's certificate comes from Let's Encrypt by
  DNS-01 through acme-dns, with the pinned lego
  (`ops/private-access/install-lego.sh`). `mag-acme-renew.timer` runs
  `/usr/local/lib/mag-acme/issue-cert.sh renew` twice a day; its deploy hook
  validates the pair, writes it to `/etc/mag-nginx/tls` and
  `/etc/nginx/mag-tls`, runs `nginx -t` and reloads nginx, and restores the
  previous pair on any failure. Settings are in `/etc/mag-acme/lego.env`,
  the acme-dns account in `/etc/mag-acme/acme-dns.json`. The bot zone's
  wildcard is renewed by `ops/bots/issue-wildcard.sh renew` on every deploy.
  The `ts.net` certificate for Gitea comes from `tailscale cert`
  (`mag-tailscale-cert.timer`).
- **Gitea** is published through Tailscale Funnel on the host's `ts.net`
  name; `mag-gitea-funnel.timer` re-asserts the serve config. Room, bot
  sites and other vhosts on the same socket admit tailnet clients only.

## CPU and the MI50

- Cores 0-13 are Lena's, 14-27 Noah's. Everything else runs on CPUs 28-55:
  builds (`taskset -c 28-55`), deploys, one-off jobs, workers, VMs. Backups
  and the CI runner use 42-55.
- Start any ad hoc heavy command with `taskset -c 28-55`.
- The MI50 belongs to `mag-idol` alone. Its power cap stays at 72 W:
  `ops/idol/power-cap.sh` only lowers it. Nothing may raise it, and no bot
  model and no scene uses the card.
- Anything that would use the card takes `/var/lib/mag-idol/gpu.lock`
  first.

## Where state lives

| Path | Content | In the restic backup |
| --- | --- | --- |
| `/var/lib/magbot/room` | messages, files, prompt logs, bots, settings, skills, plugins, `access.json` | yes |
| `/var/lib/mag-models` | verification stamps, control verbs, prefix slot files | no |
| `/home/chris/models` | GGUF model files | no, never deleted |
| `/var/lib/mag-idol/jobs` | MI50 jobs, one folder each | no |
| `/home/chris/idol-mi50` | Idol, TripoSG and video environments and weights | no |
| `/var/lib/mag-scene/jobs` | scenes and films | no |
| `/var/lib/libvirt/images/magbot` | `base/golden.qcow2`, `bots/<id>/` overlays, `saves/<vm>/` | no (saves are the VM backup) |
| `/var/lib/mag-bots/www` | the bots' published sites | no |
| `/var/lib/gitea`, `/etc/gitea` | Gitea | yes |
| `/var/log/mag-audit` | audit log | no |
| `/opt/mag-stack/releases`, `/opt/mag-stack/current` | releases | release id only |
| `/var/backups/mag-stack/<id>` | rollback copy per release | no |
| `/var/backups/mag-full` | backup archives | n/a |
| `/opt/mag-tts`, `/opt/mag-scene`, `/opt/mag-stt`, `/opt/mag-audit` | installed layers | no |
| `/etc/mag-acme`, `/etc/mag-nginx`, `/etc/nginx`, `/etc/magbot`, `/etc/mag-audit` | configuration | yes |

## Never

- delete a model file, including retired ones;
- start, stop or enable `mag-model@*.service` by hand or from a deploy; use
  `mag-modeld ctl`;
- start a second download of a file another process has open;
- raise the MI50 power cap, or give a bot model or a scene the card;
- run heavy work on cores 0-27;
- make a deploy drain, wait for a model or restart a running job;
- write a 3D job off as interrupted, or delete a job folder of a job that
  still has a message;
- rewrite a prompt log;
- use Lena's or Noah's VM as a destructive test target;
- put secrets, tokens or chat content into Git, a log or tool output.
