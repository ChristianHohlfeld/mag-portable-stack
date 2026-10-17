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
storage. If the required CNAME is absent, differs, or multiple
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
