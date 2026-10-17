# MAG owner-only admin and backup: required operations

Existing protections: private nginx listener at 100.123.114.16:443,
Tailscale LocalAPI WhoIs, signed identity proof; guest permissions exclude
admin and backup. Local restic already runs as root with a 0700 repository.

New code: only the canonical Chris login may be Owner; native local HTTP
without verified login may not execute owner-only actions. Backup status
requires a verified WhoIs proof. Passwordless restic refuses repository
directories with non-root ownership or group/world permissions.

Final console-only transition must occur AFTER Ali finishes deployment,
never from an SSH session: see ops/private-access/console-lockdown.sh.
It requires a physical or serial MAG terminal, disables Tailscale SSH
and blocks incoming SSH TCP/22 while leaving MAG HTTPS available.
Verify https://mag.stardenburg.de/ from a separate Tailnet device
before and after this operation.

On the host physical console:

    sudo bash ops/private-access/console-lockdown.sh
    sudo nft list table inet mag_console_only

To recover locally:

    sudo systemctl disable --now mag-console-only.service

No changes to the model service. No new application runtime. Remote
installation cannot turn on console-only policy without the real console.

Guest email: a Tailscale member invitation grants broader network membership
than a MAG guest enrollment. MAG_GUEST_TAILNET_MEMBER_EMAIL is disabled
by default, pending explicit tailnet ACL review and owner authorization.
The existing auth_keys OAuth credential cannot send user-invites, and
Tailscale's published granular OAuth scopes list no user_invites scope.
Do not put an all-access Tailscale credential in the MAG process.
Maxim's seven-day MAG link must be privately sent by Chris; no email
has been sent by this code deployment.
