#!/usr/bin/env bash
# Pinned-schema, single-repo transaction; no general SQL from configuration.
set +x
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
/usr/local/bin/gitea --version | grep -Fq 28.1.0 || { echo 'CI permission migration requires reviewed Gitea 28.1.0 schema' >&2;exit 1; }
db=$(awk '/^\[database\]/{inside=1;next} /^\[/{inside=0} inside&&/^PATH *=/{sub(/^PATH *= */,"");print;exit}' /etc/gitea/app.ini)
[[ -n $db && $db == /* && -f $db ]] || exit 1
fingerprint() {
 runuser -u git -- sqlite3 -bail "$db" "SELECT setting_value FROM user_setting WHERE user_id=(SELECT id FROM user WHERE lower_name='mag-ci') AND setting_key='actions.config'; SELECT config FROM repo_unit WHERE type=10 AND repo_id=(SELECT r.id FROM repository r JOIN user u ON u.id=r.owner_id WHERE u.lower_name='mag-ci' AND r.lower_name='mag-portable-stack');" | sha256sum
}
before=$(fingerprint)
install -d -m 0700 /var/lib/mag-ci/rollback
umask 077
sqlite3 "file:$db?mode=ro" ".backup '/var/lib/mag-ci/rollback/gitea-permissions.db'"
# The owner setting is cached in Gitea; installation restarts Gitea once after
# applying this ceiling and before accepting any local CI job.
runuser -u git -- sqlite3 -bail "$db" < /usr/local/lib/mag-ci/token-permissions.sql

after=$(fingerprint)
if [[ $before == "$after" ]];then printf '0\n';else printf '1\n';fi
