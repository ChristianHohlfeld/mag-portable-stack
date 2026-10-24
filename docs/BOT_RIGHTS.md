# Bot rights (GitHub, Gitea, room unit)

Bots are never administrators. Enforced in code and by `ops/deploy.sh`.

## Gitea on mag (primary place for bot-owned repos)
- `magbot-native` creates bot accounts **without** `--admin` and then runs
  `/usr/local/sbin/mag-gitea-bot-rights <user>` (`ops/gitea/bot-rights.sh`).
- Every deploy re-applies it to all `magbot-agent*` users: `admin=false`,
  `restricted=true`, `allow_create_organization=false`, repo limit 50, and an
  own private org `bots-<agent>` with the bot as owner. Restricted users see
  only their own repos/orgs, so a bot reads/writes only there.
- The script uses a throw-away admin account that is purged with its token on
  exit; no existing admin credential is read. That admin creates the org (Gitea
  refuses `POST /admin/users/{u}/orgs` for a user without org creation) and adds
  the bot to its `Owners` team, then leaves the org. Reruns are no-ops; a
  failing user is reported, the others are still applied, and the script exits
  non-zero (deploy prints `WARN: Gitea bot rights not applied`).
- Gitea Actions are enabled, but only the bot orgs have runners: one
  org-scoped runner per `bots-<agent>` (`ops/runners/bot-runners.sh`, label
  `mag-bot`), registered at deploy and when a bot account is created. Jobs of
  every other owner find no runner. The runner is an unprivileged user outside
  `mag-deploy` in a strict systemd sandbox (no host state, no other orgs, no
  local services except Gitea). An owner-scoped host runner with deploy rights
  exists only on explicit opt-in and never for a bot. Details: docs/RUNNERS.md.

## GitHub
- Personal connector (`connectors/github.token`) must be a **fine-grained
  read-only** token (Contents/Issues/Pull requests/Metadata: read). The built-in
  `github` tool only issues GET with it; any write is refused in
  `github_api()` regardless of the token's scopes.
- Optional bot org split (disabled unless configured): put the org name in
  `connectors/github-mag-bots.org` and an org-scoped token in
  `connectors/github-mag-bots.token` (mode 0600, owner magbot). Then
  `create_repo`, `create_issue`, `comment_issue` work **only** for repos inside
  that org. Workflows in those repos are the bots' own; Chris's repos stay
  read-only.
- MCP servers with `auth=connector` send `X-MCP-Readonly: true` and are never
  auto-approved for tools that are not annotated read-only, whatever the bot
  binding (`auto`/`always`) says.

## Room unit
`ops/mag-120.d/40-hardening.conf` adds kernel/clock/namespace/address-family
restrictions. `NoNewPrivileges`, `RestrictSUIDSGID` and `ProtectSystem=strict`
are intentionally omitted: the room's sudo VM helpers inherit the sandbox and
would break. The narrow sudoers file is the privilege boundary.
