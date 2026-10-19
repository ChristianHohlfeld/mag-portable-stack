#!/usr/bin/env bash
# Läuft IN einer Agent-VM als root. Wird automatisch von `magbot audit-proxy <id>`
# (vm.cpp, per qemu-guest-agent) ausgeführt: bei jedem neuen Bot im Spawn und für bestehende
# Bots beim nächsten Browser-Start durch magroom, und nach einem Deploy für jede laufende VM.
# Manuell nur zur Diagnose:
#
#   vm-proxy-setup.sh http://192.168.122.1:8888 /pfad/mag-audit-ca.crt [/pfad/isrg-roots.pem]
#
# Richtet ein, dass jeder Web-Zugriff der VM (Shell-Werkzeuge, apt/pip/git, Chromium) über den
# protokollierenden Proxy auf dem Host läuft und dessen CA vertraut wird. Direkter Web-Zugriff
# der VMs ist auf dem Host per nftables gesperrt (mag-audit.nft). Die öffentlichen ISRG-Wurzeln
# liegen nur auf dieser Overlay-Platte. Despawn löscht die Platte; das Golden-Image bleibt
# unangetastet. Idempotent.
set -euo pipefail
PROXY="${1:?Proxy-URL, z.B. http://192.168.122.1:8888}"
CA="${2:?Pfad zur CA}"
ROOTS="${3:-}"
[[ $EUID -eq 0 ]] || { echo "bitte als root"; exit 1; }
[[ -s "$CA" ]] || { echo "CA-Datei fehlt: $CA"; exit 1; }
hostport=${PROXY#*://}; hostport=${hostport%/}
host=${hostport%:*}
# Direkt (ohne Proxy): lokal + VM-Gateway (Raum-Bridge :8767, Gitea :3001, DNS)
NOPROXY="localhost,127.0.0.1,::1,$host,192.168.122.1"

echo "== System-Trust-Store"
install -D -m 0644 "$CA" /usr/local/share/ca-certificates/mag-audit-ca.crt
# Replace the whole ISRG set. A smaller or empty bundle must not leave a
# previous mag-isrg-NN.crt on this overlay.
rm -f /usr/local/share/ca-certificates/mag-isrg-*.crt
if [[ -s "$ROOTS" ]]; then
  echo "== ISRG-Wurzeln (Let's Encrypt, auch Generation Y)"
  install -d -m 0755 /usr/local/share/ca-certificates
  n=0
  while IFS= read -r line; do
    if [[ $line == '-----BEGIN CERTIFICATE-----' ]]; then
      n=$((n + 1))
      out=$(printf '/usr/local/share/ca-certificates/mag-isrg-%02d.crt' "$n")
      : > "$out"
    fi
    [[ $n -gt 0 ]] || continue
    printf '%s\n' "$line" >> "$out"
    if [[ $line == '-----END CERTIFICATE-----' ]]; then chmod 0644 "$out"; fi
  done < "$ROOTS"
fi
update-ca-certificates >/dev/null
# certutil laeuft als jeweiliger Nutzer: die CA aus dem (root-only) Temp-Verzeichnis ist fuer ihn
# nicht lesbar -> ab hier die weltlesbare installierte Kopie verwenden.
CA=/usr/local/share/ca-certificates/mag-audit-ca.crt

echo "== Proxy-Variablen (System, Login-Shells, apt)"
for v in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY; do
  sed -i "/^$v=/d" /etc/environment
done
{
  for v in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; do echo "$v=$PROXY"; done
  echo "no_proxy=$NOPROXY"; echo "NO_PROXY=$NOPROXY"
} >> /etc/environment
cat > /etc/profile.d/mag-audit-proxy.sh <<EOF
# mag-audit: aller Web-Verkehr über den protokollierenden Proxy des Hosts
export http_proxy=$PROXY https_proxy=$PROXY HTTP_PROXY=$PROXY HTTPS_PROXY=$PROXY
export no_proxy=$NOPROXY NO_PROXY=$NOPROXY
export NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/mag-audit-ca.crt
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
export PIP_CERT=/etc/ssl/certs/ca-certificates.crt
EOF
chmod 0644 /etc/profile.d/mag-audit-proxy.sh
if [[ -d /etc/apt/apt.conf.d ]]; then
  printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' "$PROXY" "$PROXY" \
    > /etc/apt/apt.conf.d/90mag-audit-proxy
fi

echo "== Chromium/Chrome-Policy (falls eine Policy-fähige Installation vorhanden ist)"
POLICY=$(cat <<EOF
{
  "ProxySettings": {"ProxyMode": "fixed_servers", "ProxyServer": "${host}:8889",
                    "ProxyBypassList": "<-loopback>;$host;192.168.122.1"},
  "QuicAllowed": false,
  "DnsOverHttpsMode": "off",
  "WebRtcIPHandling": "disable_non_proxied_udp"
}
EOF
)
for d in /etc/chromium/policies/managed /etc/opt/chrome/policies/managed; do
  install -d -m 0755 "$d"; echo "$POLICY" > "$d/mag-audit.json"; chmod 0644 "$d/mag-audit.json"
done

echo "== CA in die NSS-Datenbanken (Chromium unter Linux nutzt ~/.pki/nssdb)"
if ! command -v certutil >/dev/null; then
  # apt läuft bereits über den Proxy (http-Spiegel); kurze Timeouts, kein Abbruch bei Fehler
  timeout 300 apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=30 update -qq >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive timeout 300 apt-get install -y -qq libnss3-tools >/dev/null 2>&1 || true
fi
if command -v certutil >/dev/null; then
  while IFS=: read -r user _ uid _ _ home _; do
    if [[ "$user" != bot && ( $uid -lt 1000 || $uid -ge 65534 ) ]]; then continue; fi
    [[ -d "$home" ]] || continue
    db="$home/.pki/nssdb"
    install -d -o "$user" -m 0700 "$home/.pki" "$db"
    [[ -f "$db/cert9.db" ]] || runuser -u "$user" -- certutil -d "sql:$db" -N --empty-password
    runuser -u "$user" -- certutil -d "sql:$db" -D -n mag-audit-ca >/dev/null 2>&1 || true
    runuser -u "$user" -- certutil -d "sql:$db" -A -t "C,," -n mag-audit-ca -i "$CA"
    # Drop nicknames from a previous larger bundle, then trust the current set.
    for i in $(seq -w 1 16); do
      runuser -u "$user" -- certutil -d "sql:$db" -D -n "mag-isrg-$i" >/dev/null 2>&1 || true
    done
    shopt -s nullglob
    for crt in /usr/local/share/ca-certificates/mag-isrg-*.crt; do
      nick=$(basename "$crt" .crt)
      runuser -u "$user" -- certutil -d "sql:$db" -A -t "C,," -n "$nick" -i "$crt"
    done
    shopt -u nullglob
    echo "   NSS: $user"
  done < /etc/passwd
else
  echo "WARNUNG: certutil (libnss3-tools) nicht verfügbar – Chromium vertraut der Audit-CA noch nicht." >&2
  exit 3
fi

echo "== Kurztest (Probe, max. 15 s)"
if command -v curl >/dev/null; then
  if curl -fsS --connect-timeout 5 --max-time 15 -o /dev/null -x "$PROXY" https://example.org; then
    echo "   HTTPS über Proxy OK"
  else
    echo "   HINWEIS: HTTPS über Proxy nicht erreichbar (Proxy auf dem Host aktiv?)"
  fi
fi
echo "Fertig. Laufendes Chromium übernimmt Proxy/CA erst nach einem Neustart der VM."
