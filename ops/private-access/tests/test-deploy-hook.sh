#!/usr/bin/env bash
# Exercise both known nginx TLS destinations and transactional failure recovery.
set -euo pipefail
tmp=$(mktemp -d)
trap 'sudo rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/src" "$tmp/old" "$tmp/new" "$tmp/bin"
cat > "$tmp/lego.env" <<'ENV'
MAG_ACME_DOMAIN=mag.stardenburg.de
ENV
openssl req -x509 -newkey rsa:2048 -nodes -days 3 \
  -subj '/CN=mag.stardenburg.de' \
  -keyout "$tmp/src/mag.stardenburg.de.key" \
  -out "$tmp/src/mag.stardenburg.de.crt" >/dev/null 2>&1
sed -e "s|/etc/mag-acme/lego.env|$tmp/lego.env|g" \
    -e "s|/etc/mag-acme/lego/certificates|$tmp/src|g" \
    -e "s|/etc/nginx/mag-tls|$tmp/old|g" \
    -e "s|/etc/mag-nginx/tls|$tmp/new|g" \
    -e "s|/etc/mag-acme/.tls-snapshot.XXXXXXXX|$tmp/.tls-snapshot.XXXXXXXX|g" \
    -e "s|/run/lock/mag-acme-deploy.lock|$tmp/lock|g" \
    ops/private-access/deploy-hook.sh > "$tmp/hook.sh"
chmod 0755 "$tmp/hook.sh"
cat > "$tmp/bin/nginx" <<EOF
#!/usr/bin/env bash
[[ ! -e "$tmp/fail-nginx" ]]
EOF
cat > "$tmp/bin/systemctl" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$tmp/reloads.log"
EOF
chmod +x "$tmp/bin/"*
for d in old new; do
  printf 'previous cert\n' | sudo tee "$tmp/$d/cert.pem" >/dev/null
  printf 'previous key\n' | sudo tee "$tmp/$d/key.pem" >/dev/null
done
# Before the cutover, renewing must update the live old AND staged new path.
sudo env PATH="$tmp/bin:$PATH" bash "$tmp/hook.sh" --no-reload
for d in old new; do
  sudo cmp "$tmp/src/mag.stardenburg.de.crt" "$tmp/$d/cert.pem"
  sudo cmp "$tmp/src/mag.stardenburg.de.key" "$tmp/$d/key.pem"
done
[[ ! -e $tmp/reloads.log ]]
# A reload failure must restore both previous pairs, not leave a partial pair.
for d in old new; do
  printf 'previous cert\n' | sudo tee "$tmp/$d/cert.pem" >/dev/null
  printf 'previous key\n' | sudo tee "$tmp/$d/key.pem" >/dev/null
done
touch "$tmp/fail-nginx"
if sudo env PATH="$tmp/bin:$PATH" bash "$tmp/hook.sh"; then
  echo "Expected failing nginx -t" >&2
  exit 1
fi
for d in old new; do
  sudo grep -Fx 'previous cert' "$tmp/$d/cert.pem"
  sudo grep -Fx 'previous key' "$tmp/$d/key.pem"
done
[[ ! -e $tmp/reloads.log ]]
rm -f "$tmp/fail-nginx"
sudo env PATH="$tmp/bin:$PATH" bash "$tmp/hook.sh"
for d in old new; do
  sudo cmp "$tmp/src/mag.stardenburg.de.crt" "$tmp/$d/cert.pem"
  sudo cmp "$tmp/src/mag.stardenburg.de.key" "$tmp/$d/key.pem"
done
grep -Fx 'reload nginx.service' "$tmp/reloads.log"
echo "Both nginx TLS destinations updated; bad config rolled back atomically"
