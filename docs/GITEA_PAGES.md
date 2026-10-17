# MAG Pages — native C++ + nginx

**Invariant: MAG is a C++ stack.** The publisher runs as a thread within
the existing C++17 magroom executable (native/pages.hpp). No independent
Python, Node, Go, webhook or polling service is added.

## Publishing

- Every 60 seconds magroom reads the Gitea API on loopback (127.0.0.1:3000)
  with a host-local read-only token.
- Priority: gh-pages branch root; otherwise /docs on default branch.
- libarchive rejects path traversal, symlinks and special files; files and
  sizes are bounded. Only static files are served, never executed.
- Files are staged as immutable releases; only atomic symlinks within
  /var/lib/mag-pages/public, /tailnet and /custom are changed on pushes.
  nginx is NOT reloaded for normal pushes.
- Standard URL: https://REPO--OWNER.pages.stardenburg.de/.
  A flat hostname is essential: *.pages.stardenburg.de wildcard TLS does
  not cover repo.owner.pages.stardenburg.de.
- Default is always Tailnet-only, regardless of the Gitea source visibility.
  To publish publicly, the root operator sets owner/repo to public in
  /etc/mag-pages/visibility.json (format: JSON object of repo => public or
  tailnet). The magbot service can read this file but cannot modify it.
  Reversion to tailnet removes public and custom webroot links at next poll.
- On a scan error, last functioning routes remain in place. A Gitea API token
  must have read access to all repositories you want to publish.

## DomainFactory DNS template

| Record | Host in stardenburg.de | Value |
| --- | --- | --- |
| A | pages | PUBLIC_IPV4 |
| A | *.pages | PUBLIC_IPV4 |
| CNAME | _acme-challenge.pages | ASSIGNED-ACME-DNS-NAME.auth.acme-dns.io. |

Use the true assigned acme-dns hostname, not the placeholder, and set
PUBLIC_IPV4 to the Internet-facing endpoint. If NAT applies, forward ports
80/443 to the local address configured in PAGES_PUBLIC_BIND. The nginx
private vhost listens only on PAGES_TAILNET_BIND, e.g. 100.123.114.16.
For ordinary HTTPS names from tailnet, use split DNS to resolve *.pages
to that Tailscale IPv4 (public DomainFactory DNS points to PUBLIC_IPV4).

DNS and HTTP check:

~~~sh
dig +short A hello--alice.pages.stardenburg.de @1.1.1.1
dig +short CNAME _acme-challenge.pages.stardenburg.de @1.1.1.1
curl -I --resolve hello--alice.pages.stardenburg.de:443:PUBLIC_IPV4 https://hello--alice.pages.stardenburg.de/
~~~

## Install (host operator / Ali, not GitHub CI)

New compile-time dependencies: libarchive-dev and libcurl4-openssl-dev,
besides the existing MAG OpenSSL, ICU and C++17 toolchain. The existing
lego / acme-dns binary and nginx remain the certificate/delivery stack.

~~~sh
sudo apt-get install -y libarchive-dev libcurl4-openssl-dev nginx dnsutils
sudo install -d -m 0750 -o root -g magbot /etc/mag-pages
sudo install -m 0600 ops/pages/pages.env.example /etc/mag-pages/pages.env
sudoedit /etc/mag-pages/pages.env
# Set actual host-local IPv4, public DNS IPv4, Tailnet IPv4 and ACME email.
# Issue a dedicated read-only Gitea API token for all repositories to publish:
sudo sh -c 'umask 077; cat > /etc/mag-pages/gitea-token'
# Paste token then Ctrl+D. Never put it in Git.
# Delegate _acme-challenge.pages in DomainFactory first:
sudo ops/pages/issue-wildcard.sh run
sudo ops/pages/install.sh

# Install the C++ magroom binary using existing ops/deploy.sh first.
# Enable the native publisher only after mag-120 has drained:
sudo install -d /etc/systemd/system/mag-120.service.d
sudo install -m 0644 ops/pages/mag-pages-room.conf /etc/systemd/system/mag-120.service.d/30-pages.conf
sudo systemctl daemon-reload
sudo systemctl restart mag-120.service
sudo nginx -t
sudo journalctl -u mag-120.service --since -5min
~~~

No mag-llm restart. The optional room drop-in enables the worker; the
installer alone does not touch the live MAG model or room process.

## Per-repository visibility

Initially all sites are Tailnet-only. Edit the root-controlled file:

~~~json
{"alice/hello":"public","alice/internal":"tailnet"}
~~~

~~~sh
sudoedit /etc/mag-pages/visibility.json
sudo chown root:magbot /etc/mag-pages/visibility.json
sudo chmod 0640 /etc/mag-pages/visibility.json
~~~

Only explicit public enables an Internet-facing symlink. Changes take up
to one 60-second polling cycle, not an nginx reload.

## Custom domains (Gitea CNAME + lego)

For a repo explicitly marked public, commit CNAME containing just a hostname
(e.g. www.example.org) at the published root. In DomainFactory for that
domain set CNAME www -> hello--alice.pages.stardenburg.de.

Only exact DNS CNAME pointing to the canonical repository host is accepted;
apex A-only custom domains are excluded to prevent cross-repo takeover.
The C++ worker maintains /var/lib/mag-pages/custom/[hostname] links and
writes validated custom-domains.tsv. A root-owned shell helper invokes the
already installed lego CLI with HTTP-01 webroot via nginx (port 80), then
validates the new certificate and nginx config before reloading nginx.

Certificate reconciliation is attached to the **existing**
mag-acme-renew.service using an ExecStartPost drop-in; NO new timer or
runtime service is created. Default periodic renewal means custom TLS may
take until the next existing ACME check (normally twice daily); run an
immediate check after DNS propagates using:

~~~sh
sudo /usr/local/lib/mag-pages/refresh-custom.sh custom-only
~~~

Remove CNAME or revert a repository to Tailnet-only to revoke access on
the next C++ poll: nginx's old custom vhost then points to a missing
custom symlink and returns 404 without waiting for a certificate refresh.

## Rollback and checks

~~~sh
sudo ops/pages/rollback.sh
sudo nginx -t
sudo journalctl -u mag-120.service --since -1h | grep 'MAG Pages'
~~~

Rollback removes Pages-specific nginx vhosts and systemd drop-ins and
restarts only mag-120, not mag-llm. Site files and credentials remain on
the host for diagnosis.

CI checks the C++ archive safety and atomic publication, links the native
MAG binary, parses nginx configuration using nonprivileged loopback ports,
runs Bash syntax checks and gitleaks. CI cannot verify live Gitea private
repo permissions, actual DomainFactory delegation, ACME or Tailscale
connectivity; those must be checked on MAG at install time.
