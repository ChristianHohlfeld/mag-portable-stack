# MAG-only CI migration

Replaces the host/instance runner approach of PR #112 for this migration.
Only ChristianHohlfeld/mag-portable-stack is handled. The existing Gitea
pull mirror and all bot orgs/gateway/SSH settings stay independent. A separate
normal private repository chris/mag-portable-stack-ci accepts actual Git pushes;
this is intentional because pull-mirror synchronization is not a reliable
Actions push trigger. The one-minute bridge mirrors main and same-repository
PR heads at their exact GitHub SHA; fork PRs are not executed automatically.

Public URL: https://git.mag.stardenburg.de/
DomainFactory DNS: create `git.mag` CNAME `mag.stardenburg.de.` in the
stardenburg.de zone, provided mag's public A record is the MAG ingress address.
If the public mag record resolves elsewhere, use `git.mag` A pointing to MAG's
public ingress IPv4 instead. Forward TCP 80/443 to that interface. The private
room's Tailnet listener is not changed. No public SSH listener is added.

Deployment from the reviewed checkout, after #128's local CI passes:

1. Root-only /etc/credstore/github-ci.token: fine-grained GitHub token restricted
   to this repository, Contents read/write (mirror + green), Pull requests read,
   Commit statuses write. Never put it in Actions secrets or the runner home.
2. Root-only /etc/credstore/gitea-ci.token: Chris's Gitea token with permission
   to create/configure/write the private CI repository. Modes 0400 or 0600.
   The existing chris administrator must already exist; setup refuses otherwise.
3. Install build/test dependencies from ops/bootstrap.sh in the runner's tool
   environment, including cmake, ninja, C++ headers, Go, Node, Python/Playwright,
   Chromium, nginx, restic, jq, shellcheck and gitleaks. Dependencies are operator
   provisioning; workflow code gets no unrestricted sudo on MAG.
4. Run configure-public.sh as root with MAG_GITEA_PUBLIC_IP and MAG_ACME_EMAIL.
   This obtains and renews the separate certificate with pinned lego, preserves
   unrelated app.ini settings, requires login/2FA, and disables registration.
   Enrol Chris's authenticator in the browser. An operator cannot perform that
   personal enrolment on Chris's behalf. Existing API/SSH tokens remain separate.
5. Run install.sh as root. It installs a repository-scoped runner (mag-ci), the
   credential-isolated controller, resource slice and one-minute poll timer.
   It never starts/stops/reloads the resident model units. This is explicit
   infrastructure installation, not part of every room deploy.
6. The existing root-only blank-host bootstrap harness must run on a dedicated
   disposable runner with label mag-ci-disposable. Do not grant host sudo to
   mag-ci. That runner must be confined to a disposable VM with equivalent CPU,
   memory and IO caps; its provisioning is not implemented in this draft.
   Until present and successful, gitea/ci remains pending. No skipped/missing
   workflow is accepted as green.
7. Check the candidate PR on Gitea, returned gitea/ci status on its exact GitHub
   head, and a main run updating GitHub's green ref. GitHub branch protection
   must require gitea/ci instead of the old GitHub job contexts. The connector
   cannot modify repository administration rules from this session.
8. Compare warm latency of both :8191/:8192 with CI idle/running using the same
   prompts, slots and token counts; CPU quotas bound CPU use but cannot by
   themselves prove unchanged memory-bandwidth latency. Record before/after
   median and p95. Lower CPUQuota further or use a separate CI host if needed.

The candidate changes every .github workflow to workflow_dispatch only; do not
merge this migration before the Gitea acceptance run, runner provisioning and
required-check cutover are verified. It is deliberately not auto-merged here.
There is no change to other repositories or the GitHub budget.

Rollback: disable mag-ci-bridge.timer and mag-ci-runner.service; revert the
workflow trigger commit; restore /etc/gitea/app.ini.before-mag-ci if necessary;
remove only the mag-gitea nginx site and reload nginx after nginx -t. Keep state,
mirrors, credentials and bot accounts. Do not roll the green branch backwards.

Known validation needed on MAG: public ingress, certificate renewal, interactive
2FA, actual Gitea runner/API compatibility, disposable root runner, full suite,
GitHub status/branch protection/green flow and LLM latency. This draft is not a
claim that those production acceptance criteria have passed.
