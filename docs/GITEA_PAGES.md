# MAG Pages — GitHub Pages-style hosting for MAG's Gitea

MAG Pages is a standalone Python 3 (stdlib) publisher, served exclusively
through **nginx**. No Caddy, CDN, MAG room code, Gitea database mutations,
mag-llm restart or incoming public webhook endpoint.

## What gets published

Every repository visible to the configured Gitea service account is checked
once per minute by a systemd timer. If a branch named **gh-pages** exists,
its root is published. Otherwise, the **docs/** directory of the repository's
default branch is published, if present. Static HTML/CSS/JS/assets only: no
build commands or arbitrary repository code are executed.

Default hostname: **REPO--OWNER.pages.stardenburg.de**.
Example: hello--chris.pages.stardenburg.de.

The double-hyphen/flat scheme is deliberate: a single wildcard certificate for
*.pages.stardenburg.de **does not** cover repo.user.pages.stardenburg.de.
Long or DNS-unsafe repository names get a stable short hash suffix.

Git archive contents are filtered, bounded and copied into immutable
SHA-addressed directories. Symlinks/devices are rejected, and dotfiles
are not published. Changed revisions are promoted only after completing
a full scan and passing nginx -t. A failed scan keeps previously published
sites available. Deleted repos/routes disappear after a successful scan.
The Gitea token remains solely on mag, never in a Git repository.

**Visibility is Tailnet-only by default**, including Gitea public
repositories. This differs deliberately from automatically publishing private
source: only the host operator can explicitly make a Pages site public.
Existing Tailscale ACLs remain in effect.

## DomainFactory DNS template

For public sites, substitute the **actual Internet-facing IPv4**
(PUBLIC_IPV4) for the value in these DomainFactory DNS records:

| Record | Host within stardenburg.de | Value |
| --- | --- | --- |
| A | pages | PUBLIC_IPV4 |
| A | *.pages | PUBLIC_IPV4 |
| CNAME | _acme-challenge.pages | YOUR-ACME-DNS-HOST.auth.acme-dns.io. |

Use the actual delegation hostname assigned by the existing MAG acme-dns
credentials, **not** the placeholder. DNS-01 issues a single Let's Encrypt
wildcard cert for *.pages.stardenburg.de.

The nginx config's public_ip must be the IPv4 address **bound locally on mag**;
behind NAT it may differ from the DomainFactory public A record (configure
public_dns_ip with that externally advertised IPv4), in which case
forward inbound TCP 80/443 to mag. The tailnet_ip example is
100.123.114.16:443, matching the existing private MAG nginx listener.
Do not accidentally expose the existing mag.stardenburg.de room on the public
IP. SNI separates the page sites from the current room host.

Verify the DomainFactory records, from a resolver outside your DNS zone:

~~~sh
dig +short A hello--chris.pages.stardenburg.de @1.1.1.1
dig +short CNAME _acme-challenge.pages.stardenburg.de @1.1.1.1
dig +short TXT _acme-challenge.pages.stardenburg.de @1.1.1.1
curl -I --resolve hello--chris.pages.stardenburg.de:443:PUBLIC_IPV4 https://hello--chris.pages.stardenburg.de/
~~~

### Tailnet-only pages and name resolution

For private repositories, nginx listens on **Tailscale IPv4 only**.
The public wildcard DNS A record points to the public endpoint, where
a private repository has no matching nginx virtual host.

To use the exact same HTTPS hostname privately, configure split DNS for
pages.stardenburg.de to an internal resolver serving wildcard A responses
to 100.123.114.16, or add individual host-file entries to the tailnet client.
DomainFactory alone cannot route the same name to both the public interface
and Tailscale based on the repository. The certificate remains valid.

## First-time installation on MAG

Requirements: existing Gitea API at 127.0.0.1:3000, nginx, Python 3,
the already configured lego/acme-dns account and DNS-01 delegation,
certbot (for custom domains), dig, working systemd, and valid local bind IPs.
No Gitea server or MAG model restart is required.

1. Check git main and live deployments before deploying. This PR is based
   on main dcdd78a. Do **not** replace mag-room.conf.
2. In the Gitea UI, create a dedicated service-account API token with read
   access to every repository to be hosted (private ones included).
3. Prepare the config and paste the token through terminal stdin; never
   put it in an environment variable or tracked file:

~~~sh
sudo apt-get install -y nginx python3 dnsutils certbot
sudo install -d -m 0700 /etc/mag-pages
sudo install -m 0600 ops/pages/config.example.json /etc/mag-pages/config.json
sudoedit /etc/mag-pages/config.json
# Set public_ip to the local IPv4 of the public-facing interface.
# Set tailnet_ip to the current mag Tailscale IP.
# Replace operator@example.com with your actual ACME email.

sudo sh -c 'umask 077; cat > /etc/mag-pages/gitea-token'
# Paste Gitea token, then Ctrl+D
sudo chmod 0600 /etc/mag-pages/gitea-token

# After setting _acme-challenge.pages CNAME in DomainFactory:
sudo ops/pages/issue-wildcard.sh run
sudo ops/pages/install.sh
sudo systemctl status mag-pages-sync.timer
sudo journalctl -u mag-pages-sync.service -n 50 --no-pager
sudo nginx -t
~~~

The wildcard uses a dedicated /etc/mag-acme/lego-pages directory and
existing /etc/mag-acme/lego.env + acme-dns storage. Certificate/key pairs are atomically switched
under /etc/mag-pages/tls/current. Do not commit the storage JSON, credentials, PAT,
private keys or certbot account details. Renewals use
mag-pages-cert.timer; HTTPS is never downgraded to HTTP on a failure.

The installer preflights certificates and nginx config, installs standalone
timers, and starts the initial sync. No existing Gitea or mag-llm service
is restarted.

## Repository workflow

Push gh-pages/index.html, or docs/index.html to the default branch.
The next successful timer run publishes the files under
https://REPO--OWNER.pages.stardenburg.de/.

A private site's default hostname needs the Tailnet DNS rule above.

Only a root operator may change visibility:

~~~sh
sudo python3 /usr/local/lib/mag-pages/mag_pages.py visibility chris/hello public
sudo systemctl start mag-pages-sync.service
curl -I https://hello--chris.pages.stardenburg.de/

# Revoke Internet access; the site remains available privately:
sudo python3 /usr/local/lib/mag-pages/mag_pages.py visibility chris/hello tailnet
sudo systemctl start mag-pages-sync.service
~~~

The mode is stored in root-only /etc/mag-pages/visibility.json and is **not**
controlled by a repo's contents. Access is never public by implication.

## Custom domains via the CNAME file

For an *explicitly public* Pages site, commit a single hostname such as
www.example.org as the entire content of gh-pages/CNAME or docs/CNAME.

At DomainFactory (for the custom zone):

| Type | Host | Target |
| --- | --- | --- |
| CNAME | www | hello--chris.pages.stardenburg.de. |
| A | @ (apex, if desired) | PUBLIC_IPV4 |

An apex normally cannot use a CNAME; use an A record or provider-specific
ALIAS if supported. The publisher checks the custom hostname's CNAME or A
resolution before calling certbot. nginx exposes the webroot challenge on
port 80; certbot obtains a **separate** Let's Encrypt HTTP-01 certificate.
nginx serves HTTPS on the custom name only after successful issuance and
config validation. If issuance fails, the default hostname remains active;
the next timer run retries.

Note for NAT: apex A validation compares against the configured
public_dns_ip, so use CNAME for custom subdomains or ensure the A record
and local bind IP are the same for an apex. Custom domains for Tailnet-only
pages are intentionally not automatically certified by public HTTP-01.

## Safety checks, limitations and rollback

~~~sh
sudo nginx -t
sudo systemctl list-timers 'mag-pages*'
sudo journalctl -u mag-pages-sync.service --since -2h
sudo python3 -m unittest discover -s ops/pages/tests -v

# ONE command; restores pre-Pages nginx and stops only MAG Pages timers:
sudo ops/pages/rollback.sh
~~~

The rollback intentionally leaves content and certificate files available
for diagnosis; it does not stop nginx, mag-room or mag-llm.

GitHub Actions exercises archive path/symlink rejection, fallback branch
selection, public-vs-Tailnet nginx templates, 60 repositories across
repeated polling, and nginx syntax on loopback-only test addresses.
It cannot prove ACME delegation, NAT reachability, Tailscale ACL behavior
or that the Gitea service token sees all private repositories on live mag.
Those require the on-host validation above.
