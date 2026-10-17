#!/usr/bin/env bash
# Isolated simulation of a failed cutover: metadata + host files + retry state.
set -euo pipefail
tmp=$(mktemp -d)
trap 'sudo rm -rf -- "$tmp"' EXIT
root=$tmp/host
mkdir -p "$root/etc/nginx/sites-available" "$root/etc/nginx/sites-enabled" \
  "$root/etc/systemd/system" "$root/usr/local/lib/mag-private" \
  "$root/usr/local/lib/mag-acme" "$root/var/lib/mag-private-cutover/host" "$tmp/bin"
site=$root/etc/nginx/sites-available/mag-room.conf
state=$root/var/lib/mag-private-cutover
printf 'new config\n' > "$site"
chmod 0600 "$site"
printf 'original config\n' > "$site.pre-whois"
chmod 0644 "$site.pre-whois"
ln -s ../sites-available/mag-room.conf "$root/etc/nginx/sites-enabled/mag-room.conf"
printf '../sites-available/mag-room.conf\n' > "$state/enabled.before"
touch "$state/pending" "$state/host.ready"
paths=(
 "$root/usr/local/lib/mag-private/rollback.sh"
 "$root/usr/local/lib/mag-private/confirm.sh"
 "$root/usr/local/lib/mag-acme/issue-cert.sh"
 "$root/usr/local/lib/mag-acme/deploy-hook.sh"
 "$root/etc/systemd/system/mag-acme-renew.service"
 "$root/etc/systemd/system/mag-acme-renew.timer"
)
for i in "${!paths[@]}"; do
  printf 'before %s\n' "$i" > "$state/host/$i"
  printf 'after %s\n' "$i" > "${paths[$i]}"
done
# One new unit did not exist before installation.
rm "$state/host/5"
touch "$state/host/5.absent"
cat > "$tmp/bin/nginx" <<EOF
#!/usr/bin/env bash
[[ ! -e "$tmp/fail-nginx" ]]
EOF
cat > "$tmp/bin/systemctl" <<EOF
#!/usr/bin/env bash
echo "$*" >> "$tmp/systemctl.log"
EOF
chmod +x "$tmp/bin/"*
sed -e "s|/etc/nginx/sites-available/mag-room.conf|$root/etc/nginx/sites-available/mag-room.conf|g" \
    -e "s|/etc/nginx/sites-enabled/mag-room.conf|$root/etc/nginx/sites-enabled/mag-room.conf|g" \
    -e "s|/var/lib/mag-private-cutover|$state|g" \
    -e "s|/usr/local/lib/mag-private/|$root/usr/local/lib/mag-private/|g" \
    -e "s|/usr/local/lib/mag-acme/|$root/usr/local/lib/mag-acme/|g" \
    -e "s|/etc/systemd/system/mag-acme-renew.|$root/etc/systemd/system/mag-acme-renew.|g" \
    ops/private-access/rollback.sh > "$tmp/rollback.sh"
# Failure after restoring files must NOT remove recovery snapshots.
touch "$tmp/fail-nginx"
if sudo env PATH="$tmp/bin:$PATH" bash "$tmp/rollback.sh"; then
  echo "Rollback should fail while nginx -t fails" >&2; exit 1
fi
test -s "$site.pre-whois"
test -s "$state/enabled.before"
test -e "$state/host.ready"
rm "$tmp/fail-nginx"
sudo env PATH="$tmp/bin:$PATH" bash "$tmp/rollback.sh"
grep -Fx 'original config' "$site"
[[ $(stat -c %a "$site") == 644 ]]
for i in "${!paths[@]}"; do
  if [[ $i == 5 ]]; then
    [[ ! -e ${paths[$i]} ]]
  else
    grep -Fx "before $i" "${paths[$i]}"
  fi
done
[[ ! -e $site.pre-whois && ! -e $state/enabled.before &&
   ! -e $state/host.ready && ! -e $state/pending ]]
[[ -L $root/etc/nginx/sites-enabled/mag-room.conf ]]
grep -Fx 'reload nginx.service' "$tmp/systemctl.log"
echo "Rollback restores 0644 mode and host files; leaves no retry blockers"
