# MAG owner-only admin and backup

Private nginx listens on 100.123.114.16:443. Tailscale LocalAPI WhoIs plus a
signed identity proof is the only way a browser becomes a user. Guest
permissions never include admin or backup.

The owner login is `chris.hohlfeld@gmail.com`. That string must also be in
`access.json`. Another login in the allowlist is not owner. A request that
arrives on 127.0.0.1 without a login is not owner: `/healthz`, `/readyz` and
`/readyz/model` stay open for deploy probes, and every other owner route
returns 403. `/api/backup/status` also requires a nonempty login and a valid
WhoIs proof.

Unit tests set `MAG_TEST_ALLOW_LOCAL_OWNER=1` and, when they send a login,
`MAG_OWNER_CANONICAL_LOGIN` to that fixture login. Production does not set
the test flag.

Passwordless restic refuses to start when `/var/backups/mag-full` exists and
is not a root-owned directory with no group or world access. Each run is a
separate repository. See the README backup section.

`ops/private-access/console-lockdown.sh` is not part of deploy. Run it from a
physical or serial console after remote work is finished. It disables
Tailscale SSH and rejects inbound TCP/22 except on loopback. Recover on the
console with:

```sh
sudo systemctl disable --now mag-console-only.service
```

Guest invites are an auth key and `invite_url` only. MAG does not send email
and does not call Tailscale `user-invites`.
