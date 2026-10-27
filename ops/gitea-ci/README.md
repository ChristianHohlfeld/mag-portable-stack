# MAG: public Gitea and one-repository Actions migration

Scope is **only `ChristianHohlfeld/mag-portable-stack`**. GitHub remains the
review/source authority. Other repositories, their workflows and the $75 budget
are unchanged. This change does not stop/restart either resident LLM.

## Public endpoint

Public URL: **https://mag.tail3e02ae.ts.net/** (HTTPS Git and Gitea login).
Tailscale Funnel serves that name on port **443** and proxies to nginx on
`127.0.0.1:3010`, which proxies to Gitea on `127.0.0.1:3000`. Funnel presents
the `*.ts.net` certificate, so Gitea does not use lego, a public A record, or
a forwarded router port. There is no Caddy dependency. nginx stays.

The existing tailnet-only serves stay as they are and are not funneled:

| Serve | Target | Exposure |
| --- | --- | --- |
| `:443` | `http://127.0.0.1:3010` | public Funnel (Gitea nginx) |
| `:8443` | `http://127.0.0.1:18767` | tailnet only |
| `:8445` | `http://127.0.0.1:3000` | tailnet only (Gitea direct) |
| `:8446` | `http://127.0.0.1:8766` | tailnet only (room) |

Port 443 is the Funnel listener because it is the default HTTPS port GitHub
can call for webhooks and status links. `:8443` is already a private serve.
The room is not on the public listener. `ROOT_URL` is
`https://mag.tail3e02ae.ts.net/`.

Registration is off, sign-in is required and **two-factor authentication is
enforced for every human account** by Gitea itself. nginx limits login and
two-factor attempts per Funnel client address (5/minute) and the rest of the
site more loosely, so git clones still work. A host fail2ban jail cannot see
Funnel clients: their TCP connection arrives through tailscaled. The webhook
keeps its installer-generated HMAC-SHA256 secret; the bridge rejects a bad
signature. Unknown Host values are rejected.

The existing root `/etc/credstore` + systemd `LoadCredential` mechanism is used:

| File (root 0600, not symlink) | Purpose |
| --- | --- |
| `mag-ci-github.token` | Fine-grained GitHub credential selected for **this repository only**: Contents read/write (green ref), Commit statuses read/write, Pull requests read, Webhooks read/write. No Actions execution/billing or account permissions. |
| `mag-ci-gitea.token` | Installer-generated non-admin Bot token. The restricted `mag-ci` account owns **only** `mag-ci/mag-portable-stack`, cannot create organizations and has a one-repository quota; scopes write:repository/read:user. |
| `mag-ci-webhook.secret` | Installer-generated HMAC-SHA256 secret, used only by the GitHub webhook and host bridge. |
| `gitea-chris.password` | Generated only if Chris has no existing Gitea account. No password is reset on update. Delivered to Chris via the owner's secure channel; never printed. |

Chris is the Gitea human administrator. First sign-in changes a newly generated
password and requires enrollment of Chris's own authenticator/passkey. The
installer never pre-enrolls a fake factor or disables 2FA to complete setup.
Optional GitHub OAuth is deliberately not required for the migration.

## Existing bot access

`mag-git-gateway` remains `192.168.122.1:3001 -> 127.0.0.1:3000`. Existing account
IDs, repositories, isolated `bots-agent*` organizations, metadata and VM disks
are retained. Global two-factor enforcement also restricts the API of unenrolled
human users, so VM accounts must use Gitea's noninteractive **Bot** type.

Ready owned VMs receive a scoped access token atomically through the existing
native guest-file writer; only then is the same account converted to Bot.
`admin=false`, `restricted=true`, no organization creation and the existing own
organization permissions remain in force. Stopped VMs upgrade on their next
spawn/git-sync. A bot account can never sign into the human web UI. Teardown
sets `prohibit_login=true`, revoking use of **all** old/new credentials without
deleting the account or repositories; re-add explicitly unlocks the same account.
The new `git-auth-sync` command updates Git authentication only, without a VM,
browser or model restart. Do not use existing user VMs as destructive test targets.

## CI trust boundary and resource limits

Host execution runners and their deploy-trigger path are disabled when the new
migration is installed. The CI worker is a **separate disposable QEMU/KVM VM**,
not a bot VM, a privileged host runner or a Docker application. Each job boots a
fresh overlay over a root-owned Ubuntu 24.04 base. The base download is checked
against Ubuntu's signed SHA256SUMS using the distribution's cloud-image keyring.
Runner 5.1.1 is hash-pinned; the ephemeral runner is registered for this one
Gitea repository. Its reusable registration secret stays on the host; the
identity passed into the guest is revoked when a job is assigned. The guest
powers off after that one job. No host filesystem/device sharing or deploy path
is exposed to it. Root-required tests run **inside this disposable VM**.

The host cgroup limits the VM **and initial image builder** to one CPU's quota,
affinity `54 55` (SMT siblings on NUMA 1), low CPU/I/O weight, idle scheduling,
Nice 19, 14 GiB maximum host memory and no swap. The guest has 2 vCPUs/12 GiB.
Both NUMA nodes' physical cores are already occupied by inference; SMT siblings
are not magically spare physical cores. A host guard reads both model `/slots`
endpoints and freezes the CI workload slice when either is busy or the state is
unknown/unreachable. It thaws only after five quiet seconds; stopping the guard
leaves the workload frozen. Ordinary bot requests take precedence over CI,
which can therefore take much longer during conversation. Polling is bounded
(150 ms per backend, 200 ms interval); it is not an atomic request-start hook.
Actual unchanged LLM latency must be measured on MAG; it is not asserted from
static cgroup settings or fake tests.

The dedicated `172.31.255.0/30` tap/bridge is separate from libvirt/bot networks.
Guest traffic to host services is denied except the Gitea proxy on port 3000.
Private, link-local and Tailnet destinations are denied; public HTTP(S)/DNS
allow test dependency downloads. No global firewall table is flushed.

## Mirror, checks and green

The native C++ bridge accepts HMAC-verified GitHub push/PR webhooks and reconciles
via GitHub's API as recovery. The upstream repository is hard-coded and policy
mismatches fail closed. GitHub heads are copied to the dedicated managed private
Gitea replica, including fork PR heads fetched through **GitHub's target-repo
`refs/pull/N/head`**, never an arbitrary clone URL from webhook input. Objects
also get immutable `refs/ci/source/<sha>` refs. `mag-ci/*` is a reserved control
namespace. Existing `chris/*` pull mirrors and other Gitea repositories are not
taken over or modified.

A separate `mag-ci/control` branch contains the reviewed, installed workflow and
harness. Candidate changes to workflow files cannot select a different control ref. Guest root is not an integrity enclave: as with sudo-enabled hosted CI, the job can alter its own guest environment, so repository changes still require review.
The bridge dispatches it with the exact candidate SHA and base SHA, records the
returned run ID and expected control commit, then validates run/job/worker
identity and every required step before posting GitHub statuses. GitHub source,
status, webhook and green-write credentials never enter the worker. A job has
only its temporary read-only Gitea task token. The dedicated owner's and repository's maximum token permissions are clamped to code-read, with cross-repository access disabled, so a candidate workflow requesting write-all still cannot modify the control branch. Gitea 28.1.0 exposes that ceiling only through its UI; the version-pinned SQLite migration applies it transactionally to the dedicated Bot/repo and preserves unrelated records. A private DB backup is retained and Gitea is restarted once to clear its settings cache before CI starts. Setup credentials are removed
before candidate code executes.

All ten contexts are preserved: `native`, `pages`, `nginx`, `recovery`, `room`,
`chromium`, `speech`, `e2e-bootstrap`, `gitleaks`, `secrets`. Their original shell
steps are recorded in `coverage.json` and migrated to `suites/`; package setup
moves into the VM image. Native builds are reused across suites. Required suites
run even after an earlier failure, and a skipped/missing/duplicate step never
counts as success. GitHub workflows run on `pull_request` only when their path
filter matches, and on every push to `main` (a full run). `green` and the
publish workflow stay `workflow_dispatch`. On the Gitea VM a suite whose files
did not change exits 0 without rerunning, so its required context stays
success; that is an unaffected pass, not a Gitea `skipped` conclusion. Push to
main uses the same sha for base and head, so every suite runs.

Only a fully successful validated Gitea run can advance GitHub `green`, and only
if its SHA is still the current GitHub main. Updates never force an older green
ref backwards. The bridge does that directly, without a GitHub-hosted job.
`ops/ci/promote-green.sh` accepts the same result: when any of the ten required
contexts exists as a GitHub commit status, that status set decides those names.
A missing, pending, failed or duplicate context does not count as success, and a
same-named Actions check-run does not override the status. With no Gitea
statuses, check-runs alone decide, as before. The existing `mag-auto-update`
timer follows the same GitHub `green` branch and existing deployment/rollback
path. Jobs have no deployment credentials or root host trigger.

GitHub required-status sources must permit these commit-status contexts. If a
repository ruleset pins a context specifically to the GitHub Actions App, its
source must be switched to the approved external publisher without removing
the required context. The connector cannot read administration-protected rules;
this is a live rollout prerequisite, not an excuse to mark a skipped check green.

## Installation and live acceptance (Ali)

After merge, place the scoped GitHub credential in the credstore, then run the
normal **`bash ops/update.sh`**. Bootstrap and deploy apply the Funnel config.
The shared runners installer selects this migration when that credential or the
`gitea-ci.enabled` marker exists. The first isolated base image builds
asynchronously and only while the models are idle. A failed public Gitea/nginx
cutover restores the previous app.ini/nginx site; bot account/repository
identities are retained.

Acceptance is deliberately **live** after deployment:

1. Public `https://mag.tail3e02ae.ts.net/user/login` has a valid Funnel chain;
   no registration; Chris completes a real sign-in with the required second
   factor. Anonymous repository/API access stays denied. The room URL on
   `:8446` does not answer on the public name. Verify existing bot
   read/push/API access in an explicitly disposable repo/account, not by
   altering a user's VM.
2. Open/update a GitHub PR. All ten contexts for its exact **head SHA** link to
   the matching Gitea run and pass. A deliberately failing disposable PR stays
   red; skipped suites, wrong SHA and an older main never promote green.
3. A typical PR runs the native context and skips suites whose paths did not
   change. Push to main runs every suite. `green` is still moved by the bridge,
   not by a GitHub-hosted promote job. Other repos' workflows are unchanged.
4. Compare warmed bot TTFT/latency using ordinary append-only room turns before
   and during CI, for both ports. The guard must freeze CI when a request is
   active. Use `latency-check.py` on captured samples (median + p95) and retain
   evidence; do not run synthetic prompts directly on resident slot 0 or erase
   its warm cache. The previous <10 s warm-hi target also needs a host measurement.
5. After a successful main run, verify GitHub green equals that tested SHA and
   auto-update picks it up. Verify reboot persistence and a fresh worker overlay
   for the next job. No LLM restart is part of this migration.

Roll back the public UI using `/var/lib/mag-ci/rollback` and disable the `mag-ci-*`
units if needed; keep the previous root-owned app.ini/nginx backups private.
Re-enabling paid GitHub automation or host execution is an explicit owner change,
never an automatic error fallback.
