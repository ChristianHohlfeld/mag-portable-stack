#!/usr/bin/env bash
# Install pinned Gitea 28.1.0. Secrets are generated on the host only.
# Does not print them. Does not restart a matching running binary unless --restart.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
restart=0
[[ "${1:-}" == --restart ]] && restart=1
[[ "${1:-}" == "" || "${1:-}" == --restart ]] || { echo 'Usage: install.sh [--restart]' >&2; exit 2; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
ver=28.1.0
sha=ea93cfa4a300564ac457d3903469fea49ec5a4da37d0cebaa435f8e329ea64d0
artifact="gitea-${ver}-linux-amd64"
url="https://github.com/go-gitea/gitea/releases/download/v${ver}/${artifact}"
have=0
if [[ -x /usr/local/bin/gitea ]] && /usr/local/bin/gitea --version 2>/dev/null | grep -Fq "$ver"; then
  have=1
  echo "Gitea ${ver} already installed"
fi
if [[ $have == 0 ]]; then
  for cmd in curl sha256sum install; do command -v "$cmd" >/dev/null; done
  tmp=$(mktemp -d /var/tmp/mag-gitea.XXXXXXXX)
  trap 'rm -rf -- "$tmp"' EXIT
  curl --proto '=https' --tlsv1.2 -fSL --retry 3 --max-time 180 "$url" -o "$tmp/$artifact"
  printf '%s  %s\n' "$sha" "$tmp/$artifact" | sha256sum -c -
  install -m 0755 -o root -g root "$tmp/$artifact" /usr/local/bin/.gitea-next
  mv -f /usr/local/bin/.gitea-next /usr/local/bin/gitea
  /usr/local/bin/gitea --version | grep -Fq "$ver"
  rm -rf -- "$tmp"
  trap - EXIT
fi
if ! id git >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/gitea --shell /bin/bash --comment 'Gitea' git
fi
install -d -o git -g git -m 0750 /var/lib/gitea /var/lib/gitea/data /var/lib/gitea/data/lfs /etc/gitea
install -d -o git -g git -m 0750 /var/log/gitea
if [[ ! -e /etc/gitea/app.ini ]]; then
  install -m 0640 -o root -g git "$here/app.ini.example" /etc/gitea/app.ini
  sk=$(openssl rand -hex 32)
  it=$(openssl rand -hex 32)
  jwt=$(openssl rand -hex 32)
  python3 - "$sk" "$it" "$jwt" <<'PY'
import pathlib, sys
path = pathlib.Path("/etc/gitea/app.ini")
text = path.read_text()
repls = {
    "SECRET_KEY = CHANGE_ME": "SECRET_KEY = " + sys.argv[1],
    "INTERNAL_TOKEN = CHANGE_ME": "INTERNAL_TOKEN = " + sys.argv[2],
    "JWT_SECRET = CHANGE_ME": "JWT_SECRET = " + sys.argv[3],
}
for old, new in repls.items():
    if old not in text:
        raise SystemExit("app.ini placeholder missing")
    text = text.replace(old, new, 1)
if "CHANGE_ME" in text:
    raise SystemExit("placeholder left in app.ini")
path.write_text(text)
PY
  unset sk it jwt
  chown root:git /etc/gitea/app.ini
  chmod 0640 /etc/gitea/app.ini
  echo 'Wrote /etc/gitea/app.ini with generated secrets (not printed)'
else
  echo 'Left existing /etc/gitea/app.ini unchanged'
fi
install -m 0644 "$here/gitea.service" /etc/systemd/system/gitea.service
systemctl daemon-reload
systemctl enable gitea.service
if systemctl is-active --quiet gitea.service; then
  if [[ $restart == 1 ]]; then
    systemctl restart gitea.service
    echo 'Restarted gitea because --restart was passed'
  else
    echo 'gitea is already running; binary on disk updates on the next restart'
  fi
else
  systemctl start gitea.service
  echo 'Started gitea'
fi
