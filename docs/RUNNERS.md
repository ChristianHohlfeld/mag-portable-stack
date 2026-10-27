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
  The next deploy registers the runner and deletes the token file. Deploy does
  not use that runner. Gitea `.gitea/workflows/green.yml` promotes `green` and
  writes `/var/lib/mag-deploy/request`.

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
2. Leave GitHub Actions off the deploy path. Promotion and deploy run in
   Gitea: `.gitea/workflows/green.yml` on the host runner (`runs-on: mag`).
3. Check: a push to Gitea `main` runs the gate, moves Gitea `green`, and
   writes `/var/lib/mag-deploy/request`. `mag-auto-update` deploys that ref.

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

Gitea is the deploy source. `.gitea/workflows/green.yml` runs on the host
act_runner (`runs-on: mag`, CPUs 42-55, nice 15, idle I/O). On a green push to
main it moves the Gitea `green` ref and writes `/var/lib/mag-deploy/request`.
`mag-auto-update` fetches that ref. A passive GitHub mirror
(`ops/gitea/mirror-github.sh`) can still bring commits in; it does not promote
or deploy.

## Deploying from a workflow

Write to `/var/lib/mag-deploy/request`. `mag-deploy-request.path` then starts
`mag-auto-update.service`: fast-forward to `green` (newest all-green `main`), build, `ops/deploy.sh` with
backup, smoke test and automatic rollback. Nothing else on the host can be
changed from a job.

The merge gate is `.gitea/workflows/green.yml`. It promotes Gitea `green` and
then writes `/var/lib/mag-deploy/request`. A job writes that file only after
the ref move is confirmed.

Steps run directly on the host (host mode, no containers). Prefer plain `run:`
steps; `uses:` actions need the matching tools (e.g. Node) installed on the host.

## Limits and isolation

- Unprivileged users, `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, private `/tmp`. Writable: only the runner home and the deploy
  request.
- Not visible: `/etc/credstore`, backups, room state (`/var/lib/magbot`), model
  data (`/var/lib/mag-llm`), Gitea data and config, the other runner.
- `CPUAffinity=42-55` and `AllowedCPUs=42-55` (never 0-13 or 28-41), `Nice=15`, idle I/O (`ionice` class 3), `CPUQuota=200%`, `MemoryMax=6G`, no swap,
  `OOMScoreAdjust=1000`: under memory pressure the runner dies first, the
  models stay in RAM. `mag-llm` and `mag-llm-iq3` are never stopped or restarted.
- One job at a time per runner, 1-hour job timeout.
- Bot VMs stay isolated as before; jobs run beside them on the host, not in the VMs.

## Operations

```bash
journalctl -u mag-gitea-runner -u mag-github-runner -u mag-auto-update
sudo touch /etc/mag-stack/runners.disabled       # skip the GitHub runner
sudo touch /etc/mag-stack/gitea-runner.disabled  # skip the Gitea runner
sudo systemctl disable --now mag-gitea-runner mag-github-runner mag-deploy-request.path
```
