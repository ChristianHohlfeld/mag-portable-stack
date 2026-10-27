# MAG: public Gitea and one-repository Actions migration

Scope is **only `ChristianHohlfeld/mag-portable-stack`**. GitHub remains the
review/source authority. Other repositories, their workflows and the $75 budget
are unchanged. This change does not stop/restart either resident LLM.

## Public endpoint, DNS and credentials

Public URL: **https://git.mag.stardenburg.de/** (HTTPS Git and Gitea login).
The room stays at the existing Tailnet-only `mag.stardenburg.de` listener.
The parent name currently resolves to `100.123.114.16`; a CNAME to that parent
would **not** make Git public.

DomainFactory record: **`git.mag.stardenburg.de. 300 IN A <MAG_PUBLIC_IPV4>`**.
`MAG_PUBLIC_IPV4` must be the externally reachable IPv4 of the MAG host/router,
not `100.123.114.16` or another private address. No AAAA record until the IPv6
listener/firewall is explicitly configured. If MAG is behind NAT, TCP 80/443
must reach its nginx. The current repository does not establish that public
address or router forwarding, so no numerical IP is invented here. The installer
prints the exact evaluated A record from the non-secret config and refuses a
DNS mismatch. It never changes the room's existing DNS record.

Copy `public.env.example` to `/etc/mag-ci/public.env`, root-owned (0600 or 0640),
with that IPv4 and the ACME email. TLS uses the already pinned lego v5 installer,
Let's Encrypt HTTP-01, nginx's challenge directory and an automatic renewal
timer. No DomainFactory API credential is needed. There is no Caddy dependency.
HTTP serves only the challenge and a fixed-host HTTPS redirect; unknown HTTPS
hosts are rejected. Registration is off, sign-in is required and **two-factor
authentication is enforced for every human account** by Gitea itself.

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
counts as success. GitHub workflow files become **workflow_dispatch only**, with
their existing diagnostics retained; no push/PR/schedule invokes a paid runner.

Only a fully successful validated Gitea run can advance GitHub `green`, and only
if its SHA is still the current GitHub main. Updates never force an older green
ref backwards. The existing `mag-auto-update` timer follows the same GitHub
`green` branch and existing deployment/rollback path. Jobs have no deployment
credentials or root host trigger.

GitHub required-status sources must permit these commit-status contexts. If a
repository ruleset pins a context specifically to the GitHub Actions App, its
source must be switched to the approved external publisher without removing
the required context. The connector cannot read administration-protected rules;
this is a live rollout prerequisite, not an excuse to mark a skipped check green.

## Installation and live acceptance (Ali)

After merge, provision the scoped credential/non-secret config and DNS above,
then run the normal **`bash ops/update.sh`**. The shared runners installer selects
this migration when `public.env` exists, and bootstrap/auto-update use the same
path. The first isolated base image builds asynchronously and only while the
models are idle. A failed public Gitea/nginx cutover restores the previous
app.ini/nginx site; bot account/repository identities are retained.

Acceptance is deliberately **live** after deployment:

1. Public external HTTPS `/user/login` has a valid LE chain; no registration;
   Chris completes a real sign-in with the required second factor. Anonymous
   repository/API access stays denied. Verify existing bot read/push/API access
   in an explicitly disposable repo/account, not by altering a user's VM.
2. Open/update a GitHub PR. All ten contexts for its exact **head SHA** link to
   the matching Gitea run and pass. A deliberately failing disposable PR stays
   red; skipped suites, wrong SHA and an older main never promote green.
3. Verify no GitHub Actions push/PR/schedule run exists for the acceptance SHA;
   only Gitea ran those checks. Manual workflow_dispatch remains an explicit
   optional cost, not part of normal CI. Other repos' workflows are unchanged.
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
