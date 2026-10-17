# Action runners on MAG

Two runners execute GitHub-style workflows directly on the MAG host. Bots may
build, test and deploy with them; that is their area of responsibility.

| Runner | Workflows | `runs-on` | Service | User |
|---|---|---|---|---|
| Gitea runner 5.1.1 | `.gitea/workflows/*.yml` in any Gitea repo (bots included) | `mag` | `mag-gitea-runner` | `mag-runner` |
| GitHub runner 2.338.0 (optional) | `.github/workflows/*.yml` in this repo | `[self-hosted, mag]` | `mag-github-runner` | `mag-ghrunner` |

`ops/runners/install.sh` installs both. It runs automatically after each
successful deploy (from `ops/update.sh` and from the auto-update), so nobody has
to log in to the server. Downloads are pinned by SHA256.

## Installation

- **Gitea runner:** fully automatic. A runner token is generated with
  `gitea actions generate-runner-token` on the host and registered against the
  local Gitea (`HTTP_ADDR`/`HTTP_PORT` from `/etc/gitea/app.ini`). Gitea Actions
  must not be disabled in `app.ini` (enabled by default).
- **GitHub runner:** needs a registration token once. GitHub → repository →
  Settings → Actions → Runners → New self-hosted runner, copy the token
  (valid 1 hour) into `/etc/credstore/mag-github-runner.token` (root, 0600).
  The next deploy registers the runner and deletes the token file. Then set the
  repository variable `MAG_SELF_HOSTED=on`; from then on the `deploy-live` job
  in `.github/workflows/green.yml` deploys right after `green` moves instead of
  waiting for the 2-minute timer.

## Deploying from a workflow

Write to `/var/lib/mag-deploy/request`. `mag-deploy-request.path` then starts
`mag-auto-update.service`: fast-forward to `green` (newest all-green `main`), build, `ops/deploy.sh` with
backup, smoke test and automatic rollback. Nothing else on the host can be
changed from a job.

```yaml
# .gitea/workflows/deploy.yml in a Gitea repo
name: deploy
on: [push]
jobs:
  deploy:
    runs-on: mag
    steps:
      - run: make test
      - run: date -u +%FT%TZ > /var/lib/mag-deploy/request
```

Steps run directly on the host (host mode, no containers). Prefer plain `run:`
steps; `uses:` actions need the matching tools (e.g. Node) installed on the host.

## Limits and isolation

- Unprivileged users, `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, private `/tmp`. Writable: only the runner home and the deploy
  request.
- Not visible: `/etc/credstore`, backups, room state (`/var/lib/magbot`), model
  data (`/var/lib/mag-llm`), Gitea data and config, the other runner.
- `Nice=15`, idle I/O, `CPUQuota=200%`, `MemoryMax=6G`, no swap,
  `OOMScoreAdjust=1000`: under memory pressure the runner dies first, the
  models stay in RAM. `mag-llm` and `mag-llm-iq3` are never stopped or restarted.
- One job at a time per runner, 1-hour job timeout.
- Bot VMs stay isolated as before; jobs run beside them on the host, not in the VMs.

## Operations

```bash
journalctl -u mag-gitea-runner -u mag-github-runner -u mag-auto-update
sudo touch /etc/mag-stack/runners.disabled      # no further installs/updates
sudo systemctl disable --now mag-gitea-runner mag-github-runner mag-deploy-request.path
```
