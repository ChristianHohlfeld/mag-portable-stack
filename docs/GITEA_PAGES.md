# MAG Pages – native C++ plus nginx, lego 5.5.2

The publisher is already compiled into magroom (C++17); there is no
Python/Node publisher or new timer. Nginx is the static serving layer.
The read-only Gitea service token stays on the host at
/etc/mag-pages/gitea-token (root:magbot 0640, read:repository scope).
install.sh uses the existing token and never overwrites its contents.

## Default: Tailnet-only DomainFactory records

Set these records in the stardenburg.de zone:

| Type | Host | Value |
| --- | --- | --- |
| A | pages | 100.123.114.16 |
| A | *.pages | 100.123.114.16 |
| CNAME | _acme-challenge.pages | YOUR-REGISTERED-UUID.auth.acme-dns.io. |

The CNAME value is NOT a placeholder to copy. Run the wildcard script
first to register with the existing MAG acme-dns provider. On the first
registration it prints the actual UUID and exits 78, until the DomainFactory
CNAME is visible in public DNS. Both the wildcard and apex DNS-01
challenges use that delegation. Public port 80/443 is not needed for
DNS-01; nginx only adds the private Tailscale listener on
100.123.114.16:443.

Verification commands:

~~~sh
dig +short A hello--alice.pages.stardenburg.de @1.1.1.1
dig +short A pages.stardenburg.de @1.1.1.1
dig +short CNAME _acme-challenge.pages.stardenburg.de @1.1.1.1
curl -I --resolve hello--alice.pages.stardenburg.de:443:100.123.114.16 \
  https://hello--alice.pages.stardenburg.de/
~~~

The first two commands should show 100.123.114.16; the CNAME must show
the registered UUID. The curl command needs a device connected to the
appropriate Tailscale network. DNS visibility does not grant Tailnet
access; existing Tailscale ACLs still apply.

## Optional public setup (separate from the default)

Only when deliberately exposing Pages on the Internet:

| Type | Host | Value |
| --- | --- | --- |
| A | pages | PUBLIC_IPV4 |
| A | *.pages | PUBLIC_IPV4 |
| CNAME | _acme-challenge.pages | SAME-REGISTERED-UUID.auth.acme-dns.io. |

Set PAGES_PUBLIC_BIND to the actual IPv4 bound on MAG's public-facing
local interface and PAGES_PUBLIC_DNS to the Internet-facing IPv4.
If NAT applies, forward ports 80 and 443 to MAG. Public Bind must be
different from PAGES_TAILNET_BIND and must not be on tailscale0.

Making Pages public requires BOTH the explicitly configured public bind
AND a per-repository public entry in the root-owned
/etc/mag-pages/visibility.json. A public bind alone does not publish
sites. Default policy is Tailnet-only. The C++ publisher also checks
MAG_PAGES_PUBLIC_BIND (a systemd environment drop-in produced only
when public bind is explicitly set). Nginx public virtual hosts do
not exist with empty PAGES_PUBLIC_BIND.

In public DNS mode, use Tailscale split DNS to resolve private Pages
hostnames to 100.123.114.16 from Tailnet clients.

## Repair and activation on MAG

Dependencies: existing nginx, lego 5.5.2, MAG acme-dns credentials,
native C++ publisher, jq and dig utilities. No additional runtime
services, Python processes, or Caddy.

~~~sh
sudo apt-get install -y jq dnsutils
sudo ls -l /etc/mag-pages/gitea-token
sudo -u magbot test -r /etc/mag-pages/gitea-token
# Keep the token already there; DO NOT create a new one.

sudoedit /etc/mag-pages/pages.env
# For Tailnet-only, set:
# PAGES_PUBLIC_BIND=
# PAGES_PUBLIC_DNS=
# PAGES_TAILNET_BIND=100.123.114.16
# PAGES_ZONE=pages.stardenburg.de
# PAGES_ACME_EMAIL=your-real-email@example.net

# The first attempt may register acme-dns, print the CNAME, exit 78:
sudo ops/pages/issue-wildcard.sh run
# Configure the printed UUID in DomainFactory, verify propagation:
dig +short CNAME _acme-challenge.pages.stardenburg.de @1.1.1.1
# Repeat after delegation is visible:
sudo ops/pages/issue-wildcard.sh run
sudo ops/pages/install.sh
sudo nginx -t
sudo ss -ltnp | grep -E ':(80|443)[[:space:]]'
~~~

The old pages.env may contain 192.0.2.10 in PAGES_PUBLIC_BIND.
Remove it explicitly; do not use the old example's placeholder.

The installer does not restart mag-llm or even mag-120. To activate the
already deployed C++ publisher, install the existing
ops/pages/mag-pages-room.conf into
/etc/systemd/system/mag-120.service.d/30-pages.conf and restart mag-120
in an idle window after daemon-reload. Nothing in this PR touches
the inference process.

## lego 5 syntax and renewal

lego v5 has no renew subcommand. Both first issuance and renewal use:

~~~sh
lego run --accept-tos --email YOUR_EMAIL --path DIR --dns acmedns \
  --dns.resolvers 8.8.8.8:53,1.1.1.1:53 \
  -d '*.pages.stardenburg.de' -d pages.stardenburg.de
~~~

The Pages script sources the existing /etc/mag-acme/lego.env, preserving
ACME_DNS_API_BASE and ACME_DNS_STORAGE_PATH, and uses a separate
/etc/mag-acme/lego-pages certificate directory. Using jq, it reads
ONLY fulldomain values for the registered Pages domain from that
storage. If lego registered only the wildcard or apex name, its root-only
JSON account entry is safely shared between both identifiers (one CNAME
for the two DNS-01 TXT values), preserving the other MAG registrations. If the required CNAME is absent, differs, or multiple
incompatible targets exist, issuance halts before changing nginx.

The existing mag-acme-renew.service invokes
refresh-custom.sh run through ExecStartPost. This calls
issue-wildcard.sh run. No new timer is installed.
Custom-domain certificates also use lego run with HTTP-01 webroot;
custom HTTP-01 is disabled when the Public Bind is empty.

For a public repo, a CNAME file containing e.g. www.example.org and
an external DNS CNAME from www.example.org to the canonical
repo--owner.pages.stardenburg.de host can provision an HTTPS vhost.
Only explicit public sites use this mechanism.

## Per-repository visibility and rollback

Edit /etc/mag-pages/visibility.json (root:magbot 0640) as root:

~~~json
{"alice/public-site":"public","alice/internal":"tailnet"}
~~~

The default for absent entries is tailnet. Changes are applied by the
C++ publisher on its next polling cycle. An empty public bind
prevents public symlinks even when the policy says public.

Rollback Pages without restarting mag-llm:

~~~sh
sudo ops/pages/rollback.sh
~~~

Rollback restores nginx config, removes Pages-specific systemd
drop-ins and restarts only mag-120, preserving token, certs and files.
CI covers the lego v5 command arguments, first-run DNS gate, private
and opt-in public nginx templates, existing C++ tests and gitleaks.
Live DNS, ACME issuance and Tailscale access require host checks.

## Complete lifecycle and automatic cleanup

The native C++ publisher treats Gitea as source of truth. Removal of a repo,
gh-pages and docs, or a source-level PAGES_DISABLED marker unpublishes the
site. On each successful scan it removes obsolete static symlinks, SHA
releases and staging files, and records retired Pages. A missing or invalid
API response never means deletion; a valid empty list means the last repo
was removed. The pending GC marker is retried until root cleanup succeeds.

The existing root-owned pages-gc.sh reconciles the custom-domain nginx
configuration, runs nginx -t and reloads, and only THEN removes retired
per-custom-domain lego accounts, TLS certificates/keys, and obsolete
visibility.json entries. Pending visibility policies for not-yet-published
repos are preserved. No per-domain systemd timer exists. The shared
wildcard certificate, TLS current symlink and wildcard acme-dns account
are expressly excluded from deletion. Full Pages deactivation with
sudo ops/pages/rollback.sh also deletes Pages webroots, state and custom
cert/account directories, but preserves that shared wildcard and the
host-only read:repository token.

## Automatic TLS issuance and renewal

The existing mag-acme-renew.timer runs twice per day. Pages attaches
refresh-custom.sh run followed by pages-reconcile.sh to that SAME service.
lego 5.5.2 is always invoked as lego run with flags after run (never
lego renew). Wildcard plus apex use DNS-01 via existing acme-dns,
the issued cert is installed through the nginx-referenced TLS symlink,
and nginx -t plus reload follow. Every active publicly enabled CNAME
gets its own HTTP-01 webroot certificate with automatic renewal;
removed CNAME records are removed from nginx before their cert/account
directory is deleted. The one-time DNS registration requires the
actual DomainFactory _acme-challenge.pages CNAME supplied by
issue-wildcard.sh; after that no periodic manual renewal is needed.

## Six native DeepSeek Pages tools

The existing magroom native tool mechanism exposes pages_create,
pages_update, pages_list, pages_delete, pages_set_visibility and
pages_set_domain to bots with a provisioned VM. pages_create sets up an
own private Gitea repo when absent and commits gh-pages/index.html;
pages_update commits another safe relative static path; pages_delete
removes gh-pages and the default-branch docs while preserving the
original repository. pages_set_domain commits a CNAME file; the host
ACME reconciler does the rest. pages_set_visibility changes only the
operator-owned policy; public still needs an explicit public host bind.
pages_list enumerates repositories owned by that bot's actual Gitea login.

Each VM already has a dedicated Gitea account named magbot-VMID and
Git credentials in its private home. The C++ adapter derives the
owner from the server-side VM ID, validates every repo/path/domain and
copies the trusted pages-vm.sh helper on demand. The VM helper verifies
its authenticated /user identity and constructs Gitea URLs only for
that account. Credentials remain in the VM and are never returned to
DeepSeek. The host read:repository token at
/etc/mag-pages/gitea-token stays root:magbot 0640 and is never used for
mutating requests. Root policy changes are validated JSON requests under
/var/lib/mag-pages/request.json; the only sudoers privilege is the
**exact** /usr/bin/systemctl start mag-pages-reconcile.service command.
That root oneshot runs outside mag-120's ProtectSystem=full read-only
/etc mount namespace. No wildcard sudo arguments or arbitrary root shell
are available to DeepSeek.

Re-run ops/pages/install.sh to install root-owned GC, policy and
reconcile scripts, the fixed-command systemd oneshot
mag-pages-reconcile.service, its exact-command sudoers grant and the
existing ACME timer hook. There is **one new short-lived oneshot**, not
a new polling daemon or timer. Deploy compiled magroom while idle
(restart mag-120 only, never mag-llm). No new Python/Node service or Caddy.

CI covers schema and ownership validation, obsolete SHA/repo cleanup,
root cleanup of a constructed site through deletion with zero custom
TLS/key/account, nginx-map, or visibility remnants, and verifies the
shared wildcard remains. Live Gitea and public ACME DNS checks require
deployment-side verification.

## Sandbox review and synchronous completion (PR #30 follow-up)

**Why not ReadWritePaths:** The live mag-120.service has
User=magbot and ProtectSystem=full. sudo inside the unit inherits that
read-only /etc mount namespace: becoming root does not make /etc writable.
Changing ReadWritePaths would expand the web-facing C++ room process's
writable host surface. We instead delegate through PID 1 to a root oneshot
with its own namespace and a single fixed-name sudoers permission.

The native publisher serializes Gitea state/webroot reconciliation and
initiates /usr/bin/systemctl start mag-pages-reconcile.service from within
the sandbox. The root unit reads at most one validated JSON policy request
from /var/lib/mag-pages/request.json, updates visibility.json in /etc,
or reconciles nginx custom vhosts and custom certificates. A policy request
is applied first, then C++ republishes according to the new policy, then
a second root oneshot activates certificates and reloads nginx. Each tool
awaits completion and returns an error on failures. A successful
pages_delete has zero corresponding nginx routes, stale release trees,
visibility entry or custom-domain lego account after root reconciliation.
Wildcard TLS material is never deleted by a site cleanup.

On pages_set_domain and pages_set_visibility public, custom HTTPS is
attempted *immediately* after the publisher has materialized the site and
the DNS CNAME exists; no 12-hour delay to first issuance. A DNS record
that has not propagated or failed ACME issuance gives a clear error.
The existing mag-acme-renew.timer remains the retry/renew path.

The publisher now verifies that the **actual MAG read:repository token**
can list and fetch each newly published private VM-owned Gitea repository
before changing any site webroots. If not, it fails closed and reports an
actionable token/ACL error; token bytes are never logged or exposed.
CI exercises the authenticated private repo visibility and denied-scope
logic with deterministic Gitea API mock replies, but CI cannot prove
permissions of the token installed on the private MAG host. Ali should
do a temporary pages_create/private repo smoke test after installing.

The sandbox regression uses real systemd-run units with
ProtectSystem=full to confirm even root inside the sandbox cannot write
/etc, while the exact-command root oneshot can apply policy/cleanup.
The test validates immediate deletion, nginx -t plus reload, intact
wildcard certs, and idempotent reruns without a second runtime daemon.
Run sudo bash ops/pages/tests/test-pages-sandbox.sh on a disposable host
for direct reproduction. On MAG, use systemctl status and journalctl
-u mag-pages-reconcile.service to diagnose root failures without
restarting mag-llm.
