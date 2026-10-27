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
  `gitea actions generate-runner-token` on the host and registered against
  `http://127.0.0.1:3000` only. `ROOT_URL` and `HTTP_ADDR` are not used: the
  Funnel and tailnet serve addresses 404 the connect path `/api/actions`.
  `[actions] ENABLED` is set true (the token CLI can succeed while that path
  is still unmounted). The unit is enabled for the next boot.
- **GitHub runner:** needs a registration token once. GitHub → repository →
  Settings → Actions → Runners → New self-hosted runner, copy the token
  (valid 1 hour) into `/etc/credstore/mag-github-runner.token` (root, 0600).
  The next deploy registers the runner and deletes the token file. Then set the
  repository variable `MAG_SELF_HOSTED=on`; from then on the `deploy-live` job
  in `.github/workflows/green.yml` deploys right after `green` moves instead of
  waiting for the 2-minute timer.

## CI when GitHub-hosted runners do not start (`startup_failure`)

Since 2026-10-20 every Actions run in this private repository ended in
`startup_failure` with zero jobs. That pattern almost always means the
account's Actions minutes or spending limit is exhausted, or a billing
problem (failed payment) exists. Consequence: `green` stops moving and the
auto-update stops deploying.

What Chris has to do (in this order):

1. **Billing check:** GitHub → Settings (personal account) → Billing and
   licensing → Usage / Budgets: Actions minutes used, spending limit, failed
   payment. If a payment failed, GitHub may block *all* Actions jobs,
   including self-hosted ones, until it is fixed. If only the included
   minutes are used up, self-hosted runners keep working (they cost no
   minutes).
2. **Register the GitHub runner on MAG:** repo → Settings → Actions → Runners
   → New self-hosted runner → copy the registration token (valid 1 hour)
   into `/etc/credstore/mag-github-runner.token` (root, 0600). The next
   deploy, auto-update or `sudo bash ops/runners/install.sh` registers it
   with the label `mag` and deletes the token file.
3. **Point the workflows at it:** repo → Settings → Secrets and variables →
   Actions → Variables → New repository variable
   `MAG_RUNS_ON` = `["self-hosted","mag"]` (JSON). The jobs in `native.yml`,
   `backup.yml`, `pages-native.yml`, `private-nginx.yml` and the
   `promote-green` job in `green.yml` use
   `runs-on: ${{ fromJSON(vars.MAG_RUNS_ON || '"ubuntu-24.04"') }}`; without
   the variable they stay on `ubuntu-24.04`. Optionally also set
   `MAG_SELF_HOSTED=on` for the instant `deploy-live` job.
4. Check: push to `main`, the runs appear under Actions with runner
   `mag-host`, `promote-green` moves `green`, MAG deploys within 2 minutes.

Differences on the self-hosted runner (unprivileged, no sudo, no apt):

- Steps that need root are skipped there (`if: runner.environment ==
  'github-hosted'`): package installation, the nft egress test, the Pages
  root sandbox, the passwordless restic migration and the nginx `-t` check.
  Build dependencies come from `ops/bootstrap.sh` on the host.
- `image-paste.yml`, `speech.yml` (Playwright browsers) and
  `e2e-bootstrap.yml` (root + podman) stay on GitHub-hosted runners. A run
  that cannot start creates no check run, so it does not block `green`.
- `ops/ci/promote-green.sh` uses `gh` when present, otherwise curl with
  `GH_TOKEN` against the GitHub API, so it works on the host without `gh`.

Gitea Actions alternative (no GitHub billing involved): mirror the repo into
the local Gitea with `sudo bash ops/gitea/mirror-github.sh` (tokens in
`/etc/credstore/gitea-mirror.token` and `/etc/credstore/github-mirror.token`,
see the script header). `.gitea/workflows/ci.yml` then runs
`ops/ci/self-hosted-checks.sh` on the Gitea runner (`runs-on: mag`). Whether
Gitea triggers workflows for pull-mirror syncs depends on the Gitea version;
check the Actions tab after the first sync. Gitea results do not move
GitHub's `green`; the auto-update keeps following `green` on GitHub.

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
- `CPUAffinity=42-55` (never 0-13 or 28-41), `Nice=15`, idle I/O (`ionice` class 3), `CPUQuota=200%`, `MemoryMax=6G`, no swap,
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
