#!/usr/bin/env bash
# Called by an operator with the public interface IP. Changes only Gitea.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
here=$(cd -- "$(dirname -- "$0")" && pwd)
: "${MAG_GITEA_PUBLIC_IP:?set public interface IPv4}"
[[ $MAG_GITEA_PUBLIC_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
# Fail before modifying config if the existing owner is absent; no duplicate
# admin or replacement password is silently generated.
runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini admin user list --admin |
  awk '$2 == "chris" {found=1} END {exit !found}'
install -d -m 0755 /var/lib/mag-gitea-acme
install -d -m 0700 /etc/mag-gitea-acme
# Initial ACME listener only, before a certificate exists.
if [[ ! -s /etc/mag-gitea-acme/certificates/git.mag.stardenburg.de.crt ]]; then
  sed -n '1,/^}/p' "$here/public.nginx.conf" | sed "s/@PUBLIC_IP@/$MAG_GITEA_PUBLIC_IP/g" > /etc/nginx/sites-available/mag-gitea.conf
  ln -sfn /etc/nginx/sites-available/mag-gitea.conf /etc/nginx/sites-enabled/mag-gitea.conf
  nginx -t; systemctl reload nginx
fi
bash "$here/../../private-access/install-lego.sh"
/usr/local/bin/lego run --accept-tos --email "${MAG_ACME_EMAIL:?set ACME email}" \
 --path /etc/mag-gitea-acme --domains git.mag.stardenburg.de \
 --http --http.webroot /var/lib/mag-gitea-acme
# Replace only named keys; retain all unrelated VM/SSH configuration.
set_ini() {
 local section=$1 key=$2 value=$3 next
 next=$(mktemp /etc/gitea/.mag-ci.XXXXXXXX)
 awk -v section="$section" -v key="$key" -v value="$value" '
 function missing() {if (inside && !seen) print key " = " value}
 /^[ \t]*\[/ {missing(); inside=($0=="[" section "]"); if(inside) found=1; print; next}
 inside && $0 ~ "^[ \t]*" key "[ \t]*=" {if(!seen) print key " = " value; seen=1; next}
 {print}
 END {missing();if(!found) print "\n[" section "]\n" key " = " value}
 ' /etc/gitea/app.ini > "$next"
 chown --reference=/etc/gitea/app.ini "$next"
 chmod --reference=/etc/gitea/app.ini "$next"
 mv -f "$next" /etc/gitea/app.ini
}
cp -p /etc/gitea/app.ini /etc/gitea/app.ini.before-mag-ci
set_ini server DOMAIN git.mag.stardenburg.de
set_ini server ROOT_URL https://git.mag.stardenburg.de/
set_ini security TWO_FACTOR_AUTH enforced
set_ini service DISABLE_REGISTRATION true
set_ini service REQUIRE_SIGNIN_VIEW true
set_ini actions ENABLED true
set_ini actions DEFAULT_ACTIONS_URL github
sed "s/@PUBLIC_IP@/$MAG_GITEA_PUBLIC_IP/g" "$here/public.nginx.conf" > /etc/nginx/sites-available/mag-gitea.conf
nginx -t
systemctl restart gitea.service
systemctl reload nginx

install -d -m 0755 /etc/mag-ci
printf 'MAG_ACME_EMAIL=%s\n' "$MAG_ACME_EMAIL" > /etc/mag-ci/acme.env
for f in mag-gitea-acme.service mag-gitea-acme.timer; do
 install -m 0644 "$here/$f" "/etc/systemd/system/$f"
done
systemctl daemon-reload
systemctl enable --now mag-gitea-acme.timer
