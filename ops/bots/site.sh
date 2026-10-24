#!/usr/bin/env bash
# Per-VM tailnet site. DNS and the wildcard certificate stay shared.
set -euo pipefail
root="${MAG_BOTS_ROOT:-/var/lib/mag-bots}"
zone="${MAG_BOTS_ZONE:-bot.stardenburg.de}"
publish_url="${MAG_BOTS_PUBLISH_URL:-http://192.168.122.1:8091/publish}"
magbot="${MAG_BOTS_MAGBOT:-/usr/local/bin/magbot}"

die() { echo "$*" >&2; exit 1; }

valid_id() { [[ ${1:-} =~ ^agent[0-9]+$ ]]; }

ensure_dirs() {
  [[ $root != *$'\n'* && $root == /* && $root != / ]] || die "Unsafe bot site root"
  install -d -m 0755 "$root" "$root/www"
  install -d -m 0700 "$root/tokens"
  [[ ! -L $root && ! -L $root/www && ! -L $root/tokens ]] || die "Unsafe bot site symlink"
}

www_dir() {
  local id=$1 dest
  valid_id "$id" || die "Invalid bot id"
  ensure_dirs
  dest="$root/www/$id"
  [[ ! -L $dest ]] || die "Refusing symlink site"
  [[ $(basename "$dest") == "$id" && $dest == "$root/www/$id" ]] || die "Invalid site path"
  printf '%s\n' "$dest"
}

token_file() {
  local id=$1 dest
  valid_id "$id" || die "Invalid bot id"
  ensure_dirs
  dest="$root/tokens/$id"
  [[ ! -L $dest ]] || die "Refusing symlink token"
  printf '%s\n' "$dest"
}

default_index() {
  local id=$1
  cat <<EOF
<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><title>${id}.${zone}</title></head>
<body>
<h1>${id}.${zone}</h1>
<p>Tailnet-Website dieses Bots. Nur im Tailnet erreichbar.</p>
</body>
</html>
EOF
}

site_note() {
  local id=$1
  cat <<EOF
Deine Website: https://${id}.${zone}
Nur im Tailnet erreichbar, nicht im oeffentlichen Internet.
Lege Dateien in /home/bot/www ab. index.html ist die Startseite.
Veroeffentlichen: bash /home/bot/publish-site
EOF
}

publish_script() {
  cat <<EOF
#!/bin/bash
set -euo pipefail
token=\$(tr -d '\\n' < /home/bot/.site-token)
id=\$(tr -d '\\n' < /home/bot/.site-id)
[[ \$id =~ ^agent[0-9]+\$ ]] || { echo 'bad site id' >&2; exit 1; }
mkdir -p /home/bot/www
[[ -f /home/bot/www/index.html ]] || { echo 'index.html missing in /home/bot/www' >&2; exit 1; }
tar --format=ustar -C /home/bot/www -cf - . | curl -fsS --max-time 60 \\
  -H "Authorization: Bearer \$token" \\
  -H "X-Mag-Bot: \$id" \\
  -H "Content-Type: application/x-tar" \\
  --data-binary @- \\
  ${publish_url}
EOF
}

ensure_token() {
  local id=$1 file
  file=$(token_file "$id")
  if [[ ! -s $file ]]; then
    (umask 077; openssl rand -hex 32 > "$file")
    chmod 0600 "$file"
  fi
  grep -Eq '^[0-9a-f]{64}$' "$file" || die "Bad site token"
}

guest_push() {
  local id=$1 fresh=$2 token
  [[ -x $magbot ]] || return 0
  token=$(cat "$(token_file "$id")")
  printf '%s\n' "$id" | "$magbot" file-write "$id" /home/bot/.site-id --write \
    || { echo "WARN: guest site id not written for $id" >&2; return 0; }
  printf '%s\n' "$token" | "$magbot" file-write "$id" /home/bot/.site-token --write \
    || { echo "WARN: guest site token not written for $id" >&2; return 0; }
  site_note "$id" | "$magbot" file-write "$id" /home/bot/SITE.md --write \
    || echo "WARN: SITE.md not written for $id" >&2
  publish_script | "$magbot" file-write "$id" /home/bot/publish-site --write \
    || echo "WARN: publish-site not written for $id" >&2
  # A later deploy must not wipe a page the bot already edited.
  if [[ $fresh == 1 ]]; then
    if ! "$magbot" exec "$id" -- test -f /home/bot/www/index.html; then
      default_index "$id" | "$magbot" file-write "$id" /home/bot/www/index.html --write \
        || echo "WARN: guest index not written for $id" >&2
    fi
  fi
}

assign_site() {
  local id=$1 dest fresh=0
  dest=$(www_dir "$id")
  install -d -m 0755 "$dest"
  if [[ ! -f $dest/index.html ]]; then
    fresh=1
    default_index "$id" > "$dest/index.html"
    chmod 0644 "$dest/index.html"
  fi
  ensure_token "$id"
  guest_push "$id" "$fresh"
  echo "site https://${id}.${zone}"
}

remove_site() {
  local id=$1 dest token real
  dest=$(www_dir "$id")
  token=$(token_file "$id")
  if [[ -e $dest ]]; then
    real=$(realpath "$dest")
    [[ $real == "$dest" ]] || die "Refusing to remove $dest"
    # Delete nothing: unpublish by moving the site out of www into a
    # root-only archive (never purged automatically). Re-assign starts fresh.
    install -d -m 0700 "$root/archive"
    [[ ! -L $root/archive ]] || die "Unsafe bot site symlink"
    local keep
    keep="$root/archive/$id-$(date -u +%Y%m%dT%H%M%SZ)"
    while [[ -e $keep ]]; do keep="$keep-1"; done
    mv -T -- "$dest" "$keep"
    echo "archived ${id} site to $keep"
  fi
  if [[ -e $token ]]; then
    real=$(realpath "$token")
    [[ $real == "$token" ]] || die "Refusing to remove $token"
    rm -f -- "$token"
  fi
  echo "removed ${id}.${zone}"
}

backfill() {
  local ids id
  [[ -x $magbot ]] || { echo "bot site backfill skipped (magbot missing)"; return 0; }
  if ! ids=$("$magbot" status); then
    echo "WARN: magbot status failed; bot sites unchanged" >&2
    return 0
  fi
  while read -r id _; do
    [[ $id =~ ^agent[0-9]+$ ]] || continue
    assign_site "$id" || echo "WARN: site assign failed for $id" >&2
  done <<<"$ids"
}

case "${1:-}" in
  assign) [[ $# == 2 ]] || die "Usage: site.sh assign ID"; assign_site "$2" ;;
  remove) [[ $# == 2 ]] || die "Usage: site.sh remove ID"; remove_site "$2" ;;
  backfill) [[ $# == 1 ]] || die "Usage: site.sh backfill"; backfill ;;
  *) die "Usage: site.sh assign|remove|backfill" ;;
esac
