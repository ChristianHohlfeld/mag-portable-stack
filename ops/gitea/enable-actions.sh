#!/usr/bin/env bash
# Gitea Actions on (idempotent, root): [actions] ENABLED = true and
# DEFAULT_ACTIONS_URL = github in app.ini. Gitea is restarted only when the
# file changed, then this waits for /api/healthz. Silent when nothing changed.
# Who can run jobs is decided by the runners: only org-scoped runners for the
# bot orgs bots-<agent> exist (ops/runners/bot-runners.sh); there is no
# instance-wide runner, so jobs of any other owner never get a runner.
# DEFAULT_ACTIONS_URL=self would resolve `uses:` against ROOT_URL (tailnet),
# which the runner sandbox cannot reach; github is Gitea's default.
set -euo pipefail
ini=${MAG_GITEA_INI:-/etc/gitea/app.ini}
[[ -r $ini ]] || { echo "WARN: Gitea Actions not enabled: $ini missing" >&2; exit 1; }
new=$(mktemp)
trap 'rm -f "$new"' EXIT
awk '
  BEGIN { n = split("ENABLED=true DEFAULT_ACTIONS_URL=github", kv, " ")
          for (i = 1; i <= n; i++) { split(kv[i], p, "="); key[i] = p[1]; want[p[1]] = p[2] } }
  function missing(   i) { for (i = 1; i <= n; i++) if (!(key[i] in seen)) print key[i] " = " want[key[i]] }
  /^[ \t]*\[/ { if (insec) missing(); insec = ($0 ~ /^[ \t]*\[actions\][ \t]*$/); if (insec) found = 1; print; next }
  insec && /^[ \t]*[A-Za-z_]+[ \t]*=/ {
    k = $0; sub(/^[ \t]*/, "", k); sub(/[ \t]*=.*/, "", k); k = toupper(k)
    if (k in want) { if (!(k in seen)) print k " = " want[k]; seen[k] = 1; next }
  }
  { print }
  END { if (insec) missing()
        if (!found) { print ""; print "[actions]"; for (i = 1; i <= n; i++) print key[i] " = " want[key[i]] } }
' "$ini" > "$new"
cmp -s "$new" "$ini" && exit 0
# Keep owner and mode (root:git 0640 on mag); replace atomically.
cp -p "$ini" "$ini.mag-prev"
cat "$new" > "$ini.mag-next"
chown --reference="$ini" "$ini.mag-next"; chmod --reference="$ini" "$ini.mag-next"
mv -f "$ini.mag-next" "$ini"
[[ -n ${MAG_GITEA_NO_RESTART:-} ]] && { echo 'gitea: Actions enabled in app.ini'; exit 0; }
systemctl restart gitea.service
addr=$(sed -n 's/^HTTP_ADDR *= *//p' "$ini" | head -1); port=$(sed -n 's/^HTTP_PORT *= *//p' "$ini" | head -1)
[[ -z $addr || $addr == 0.0.0.0 ]] && addr=127.0.0.1
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null --max-time 2 "http://$addr:${port:-3000}/api/healthz"; then
    echo 'gitea: Actions enabled (gitea restarted)'; exit 0
  fi
  sleep 1
done
echo 'WARN: gitea not healthy 60 s after enabling Actions' >&2
exit 1
