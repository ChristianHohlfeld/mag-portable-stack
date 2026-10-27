#!/usr/bin/env bash
set +x
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
file=/etc/gitea/app.ini
[[ -s $file ]] || exit 1
# Values are all non-secret constants. Existing signing keys, database,
# repository root and SSH/VM listener settings are retained byte-for-byte.
setini() {
 local section=$1 key=$2 value=$3 tmp
 tmp=$(mktemp /etc/gitea/.public.XXXXXXXX)
 awk -v section="$section" -v key="$key" -v value="$value" '
 function finish() {if (inside&&!written) {print key " = " value;written=1}}
 /^\[/ {finish();inside=($0=="[" section "]");if(inside)seen=1;print;next}
 {if(inside&&$0 ~ "^[[:space:]]*" key "[[:space:]]*=") {if(!written)print key " = " value;written=1}else print}
 END {finish();if(!seen)print "\n[" section "]\n" key " = " value}
 ' "$file" > "$tmp"
 chown root:git "$tmp";chmod 640 "$tmp";mv -f "$tmp" "$file"
}
setini server ROOT_URL https://git.mag.stardenburg.de/
setini server DOMAIN git.mag.stardenburg.de
setini server HTTP_ADDR 127.0.0.1
setini server LOCAL_ROOT_URL http://127.0.0.1:3000/
setini security TWO_FACTOR_AUTH enforced
setini security INSTALL_LOCK true
setini service DISABLE_REGISTRATION true
setini service REQUIRE_SIGNIN_VIEW true
setini service ENABLE_REVERSE_PROXY_AUTHENTICATION false
setini service ENABLE_REVERSE_PROXY_AUTO_REGISTRATION false
setini session COOKIE_SECURE true
setini actions ENABLED true
setini actions LOG_RETENTION_DAYS 14
setini actions ARTIFACT_RETENTION_DAYS 7
