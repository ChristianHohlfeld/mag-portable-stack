# Host inventory: everything the running stack needs outside the release

The room release (`/opt/mag-stack/releases/<id>`, built and installed by
`ops/deploy.sh`) is only part of the host. This directory records the rest so
that mag can be rebuilt from Git plus a backup archive, and so that drift
between mag and `main` is visible.

| File | Content |
| --- | --- |
| `host-files.tsv` | installed path -> repo source (copy/render), installer (generated) or backup (secret) |
| `boot-units.txt` | units that must be enabled (or must stay disabled) for a cold boot, plus the boot order |
| `runtimes.lock` | llama.cpp / ik_llama.cpp binaries: commit, MAG patch, sha256, build recipe |
| `drift-check.sh` | read-only comparison of the host against this checkout |

```sh
sudo bash ops/inventory/drift-check.sh     # exit 1 on DRIFT/MISSING/BOOTERR/ORDERERR
```

The check never starts, stops or reloads a unit and never prints file
contents; secret files are only checked for existence and mode.

## Models (never in Git)

`ops/coding/models.json` is the model manifest. Ornith-1.5-35B-A3B runs on
both NUMA nodes (`general` = NUMA0 :8191, `coder` = NUMA1 :8192):

- File `Ornith-1.5-35B-A3B-Q4_K_M.gguf`, 21 864 081 056 bytes,
  SHA256 `12d8d5c01bae7f23ea4822b2f96ba069d531f827d02a57c31002f2f95e72614a`
- Source: <https://huggingface.co/bartowski/Ornith-1.5-35B-A3B-GGUF/resolve/main/Ornith-1.5-35B-A3B-Q4_K_M.gguf>
- Chat template: `ops/coding/ornith-chat.jinja`, verbatim `chat_template.jinja`
  of <https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B> (blob/sha256 in models.json)
- Placement: `/home/chris/models/mag-coding/{general,coder}/<file>`, one
  hard link to `/home/chris/models/ornith-1.5-35b-a3b/<file>` (no second copy)

`ops/coding/stage.py --model general|coder` verifies (and with `--download`
fetches) the file against that SHA256. `start-live.sh` re-verifies on every
start, so a reboot never loads unchecked bytes. GGUFs, VM images and slot
caches are excluded from backups and from Git.

## Runtimes

`ops/coding/runtime.sha` pins mainline llama.cpp (both nodes run Ornith on
it); `ops/coding/build-runtime.sh` rebuilds it as `chris` on the build cores
28-55, applying `ops/qwen/end-checkpoint.patch`. `/etc/mag-stack/coding-engine`
(`ops/coding/coding-engine`) selects `mainline`; `ik` only applies to Flint.

## Secrets

Secrets are never committed. Each `secret` row in `host-files.tsv` names an
example file or the installer that creates it (for the room:
`mag-room/.env.example` documents every key of `/etc/default/mag-room`).
They are captured by the daily/pre-deploy restic archives
(`ops/backup/backup.sh`, `/var/backups/mag-full/<time>-<kind>/`), which hold
`/etc/default/mag-room`, `/etc/mag-stack`, `/etc/mag-audit`, `/etc/credstore`,
`/etc/gitea`, `/etc/mag-pages`, `/etc/mag-acme`, `/etc/magbot`,
`/etc/systemd/system` and `/var/lib/tailscale`. Restore only the
configuration set of one archive, first into a staging directory, then live:

```sh
sudo bash ops/backup/restore.sh --part config --snapshot <time>-<daily|deploy> --target /root/restore-stage
sudo bash ops/backup/restore.sh --part config --snapshot <time>-<daily|deploy> --apply
```

See `docs/backup.md` for the archive format and verification. The archives are
local and passwordless (docs/backup.md, security notice): copy one off-host
if mag's disk itself must be survivable.

## Rebuilding mag from Git + backup

1. Restore the configuration set (above), then `ops/bootstrap.sh`.
2. `ops/coding/build-runtime.sh` (as chris) and
   `python3 -I ops/coding/stage.py --model general --download` / `--model coder`.
3. `ops/deploy.sh` installs the room, audit drop-ins, cpu-tune, nginx and runs
   `ops/boot/ensure-boot.sh`, which enables `mag-llm-coding@0/1` for boot.
4. `sudo bash ops/inventory/drift-check.sh` must report no drift.

## Not part of the stack (left on mag, intentionally not in Git)

Leftovers of the removed Python/Ollama/ComfyUI stack and of the retired
DeepSeek/GLM/Qwen/Mistral model units: `/usr/local/sbin/mag-backup-now`,
`mag-backup-maintenance`, `mag-healthcheck`, `mag-restore-fresh` (check ports
11434/8188), `/etc/default/mag-chat`, `/etc/sudoers.d/magbot-pm2-chris`,
`gitea-backup.{service,timer}` (disabled; replaced by mag-backup),
`/etc/systemd/system/mag-auto-update.service.d/force.conf`
(`MAG_AUTO_UPDATE_FORCE` is not read by any code), the hotspot sysctl/firewall
and the disabled `mag-llm*`, `mag-llm-ds-node@`, `mag-llm-glm*`,
`mag-llm-ornith*`, `mag-llm-qwen*`, `mag-llm-mistral-room` units.
