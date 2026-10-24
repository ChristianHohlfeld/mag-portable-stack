# Action runners on MAG

| Runner | Workflows | `runs-on` | Service | User | Deploy rights |
|---|---|---|---|---|---|
| Gitea runner 5.1.1, one per bot org | `.gitea/workflows/*.yml` in repos of `bots-<agent>` only | `mag-bot` | `mag-gitea-botrunner@bots-<agent>` | `magrun-bots-<agent>` | no |
| Gitea host runner (opt-in, off by default) | repos of the one owner in `/etc/mag-stack/host-runner.owner` | `mag` | `mag-gitea-runner` | `mag-runner` | yes |
| GitHub runner 2.338.0 (optional) | `.github/workflows/*.yml` in this repo | `[self-hosted, mag]` | `mag-github-runner` | `mag-ghrunner` | yes |

`ops/runners/install.sh` installs them. It runs automatically after each
successful deploy (from `ops/update.sh` and from the auto-update), so nobody has
to log in to the server. Downloads are pinned by SHA256. It prints one
`OK:` line per bot org and a `WARN:` line for every failure, nothing else.

## Gitea Actions: bot orgs only

Owner rule: bots create and run workflows only in repos they own, i.e. in
their org `bots-<agent>` (created by `ops/gitea/bot-rights.sh`).

What enforces it:

| Where | What |
|---|---|
| `ops/gitea/enable-actions.sh` (from `install.sh`) | `[actions] ENABLED = true`, `DEFAULT_ACTIONS_URL = github` in `/etc/gitea/app.ini`; gitea restarts only when the file changed. |
| `ops/runners/bot-runners.sh` → `/usr/local/sbin/mag-gitea-bot-runners` | Per bot org a runner registered with `gitea actions generate-runner-token --scope bots-<agent>`. An org runner only receives jobs of repos owned by that org. |
| No instance-wide runner | Nothing registers a global runner; jobs of `chris` or any other owner stay *waiting* (no runner matches). `mag-gitea-runner-status` flags any runner outside the bot orgs. |
| Gitea user rights | Bots are restricted, non-admin, cannot create orgs (`bot-rights.sh`): they cannot see or push to other owners' repos, cannot register instance runners. They can create user/org runner tokens for their *own* repos only; such a runner would have to run in their own KVM VM. |
| Job token | Gitea's per-job token is scoped to the job's repo; private repos of other owners stay invisible (`REQUIRE_SIGNIN_VIEW`, restricted users). |

The Actions unit itself stays on for all repos (Gitea 28 has no per-owner
switch; `DISABLED_REPO_UNITS = repo.actions` would also disable it for the bot
orgs). Without a runner a workflow outside the bot orgs never executes.

### Registration

- At deploy (`install.sh`) and when `magbot-native` creates a bot account
  (`provision_git` in `mag-room/native/vm.cpp` calls
  `mag-gitea-bot-runners <user>`, non-fatal; the next deploy retries).
- The org token is written into a root-only temp dir under `/run`, fed to
  `gitea-runner register --token-file /dev/stdin` as the runner user and
  deleted. It is never printed, never on a command line, never in Git.
- Registered against `http://<HTTP_ADDR>:<HTTP_PORT>` from `app.ini`
  (`http://127.0.0.1:3000`). The runner uses that address as the job's
  `server_url`, so checkouts never need the tailnet `ROOT_URL`.
- Idempotent: an existing `.runner` is kept while Gitea accepts its
  credentials (`Declare` RPC); a rejected one (runner deleted in Gitea) is
  re-registered. A removed bot's runner is stopped on the next deploy.

### Sandbox (`ops/runners/mag-gitea-botrunner@.service`)

- Own system user `magrun-bots-<agent>` (group `mag-botrunner`, never
  `mag-deploy`), own state `/var/lib/mag-botrunner/<org>` (0700).
- `/var/lib` is an empty tmpfs except that state dir: no Gitea data, room
  state, VM disks, models, `/var/lib/mag-deploy` or other orgs. Also hidden:
  `/etc/credstore`, `/etc/gitea`, `/etc/mag-stack`, `/etc/mag-backup`,
  `/var/backups`, `/var/log`, `/opt`, `/srv`, `/home`, `/root`.
- `ProtectSystem=strict`, `PrivateTmp`, `PrivateDevices` (no `/dev/kvm`),
  `NoNewPrivileges`, `RestrictSUIDSGID`, empty `CapabilityBoundingSet`,
  `SystemCallFilter=@system-service`, `ProtectKernel*`, `ProtectProc=invisible`,
  `RestrictNamespaces`, `LockPersonality`.
- Network: `IPAddressDeny` for LAN, tailnet (`100.64/10`), link-local,
  multicast and `::1`; `/etc/mag-botrunner/egress.nft` (loaded by
  `ExecStartPre=+`, fail-closed) lets the runner group reach on loopback only
  Gitea (`127.0.0.1:3000`) and DNS (`127.0.0.53/54`, `100.100.100.100:53`).
  The room, the models and every other local service are rejected. Internet
  egress stays open, like the bot VMs, for `uses:` actions from github.com and
  package downloads.
- Caps: all bot runners share `mag-botrunner.slice` (`CPUQuota=200%`,
  `MemoryMax=6G`, low CPU/IO weight); per org `CPUQuota=100%`, `MemoryMax=4G`,
  `Nice=15`, idle I/O, `OOMScoreAdjust=1000`, one job at a time, 1 h timeout.
  `mag-llm` / `mag-llm-qwen` are never stopped or restarted.
- Host mode (no containers): steps run as the runner user inside this sandbox,
  beside the bot VMs, not in them. Use `uses: builtin:checkout` (native, no
  Node) or plain `run:` steps.

```yaml
# .gitea/workflows/ci.yml in a repo of bots-agent1
on: [push]
jobs:
  test:
    runs-on: mag-bot
    steps:
      - uses: builtin:checkout
      - run: make test
```

### Verify on mag

```bash
sudo mag-gitea-runner-status    # Actions on, per org unit/registration/online/labels
systemctl status 'mag-gitea-botrunner@*'
systemd-analyze security mag-gitea-botrunner@bots-agent1.service
```

The status helper creates a throw-away admin with read-only scopes, purges it
on exit and prints no token; exit 1 if anything is off.

CI: `.github/workflows/gitea-actions.yml` runs `ops/tests/e2e-gitea-actions.sh`
on a GitHub-hosted runner with the pinned Gitea and runner, the real units and
nft: org runners register and come online, a bot workflow succeeds inside the
sandbox, a workflow of another owner gets no runner.

## Host runner (deploy rights, opt-in)

The former instance-wide `mag-host` runner is gone: it would have taken bot
jobs and given them `mag-deploy`. If the owner wants Gitea workflows with
deploy rights for their own repos: `echo chris | sudo tee
/etc/mag-stack/host-runner.owner`. The next deploy registers `mag-runner` at
the scope of that user only (never a `bots-*`/`magbot-*` name), label `mag`.
Removing the file unregisters it locally, disables the unit and drops
`mag-runner` from `mag-deploy`.

## GitHub runner

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
`ops/ci/self-hosted-checks.sh` on the Gitea host runner (`runs-on: mag`,
only with the opt-in above for the mirror owner). Whether
Gitea triggers workflows for pull-mirror syncs depends on the Gitea version;
check the Actions tab after the first sync. Gitea results do not move
GitHub's `green`; the auto-update keeps following `green` on GitHub.

## Deploying from a workflow

Only the host runner (opt-in) and the GitHub runner can; bot-org runners
cannot see the trigger. Write to `/var/lib/mag-deploy/request`. `mag-deploy-request.path` then starts
`mag-auto-update.service`: fast-forward to `green` (newest all-green `main`), build, `ops/deploy.sh` with
backup, smoke test and automatic rollback. Nothing else on the host can be
changed from a job.

```yaml
# .gitea/workflows/deploy.yml in a repo of the opted-in owner
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

## Limits and isolation (host and GitHub runner)

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
journalctl -u 'mag-gitea-botrunner@*' -u mag-gitea-runner -u mag-github-runner -u mag-auto-update
sudo touch /etc/mag-stack/runners.disabled      # no further installs/updates
sudo systemctl disable --now 'mag-gitea-botrunner@*' mag-gitea-runner mag-github-runner mag-deploy-request.path
sudo rm /var/lib/mag-botrunner/bots-agent1/.runner && sudo mag-gitea-bot-runners magbot-agent1  # force re-registration
```
