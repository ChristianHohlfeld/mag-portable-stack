#!/usr/bin/env bash
# Zentrales Logging auf mag installieren (Gateway :8080, Web-Proxy :8888, nftables). Idempotent.
#
#   sudo bash ops/audit/install-audit.sh            Trockenlauf: zeigt alles, ändert nichts
#   sudo bash ops/audit/install-audit.sh --apply    ausführen
#
# Aus dem Git-Repo (/home/chris/mag-stack) oder einem Release starten. Laufzeitdateien werden
# rollback-stabil kopiert: /opt/mag-audit (Gateway, Addon, mitmproxy-venv), /etc/mag-audit
# (Konfiguration, CA, VM-Paket), /etc/systemd/system (Units), /etc/nftables.d (Regeln).
# Optional: ALI_USER=<user> (sonst automatisch, wenn ein User "ali" existiert).
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
run() { echo "+ $*"; if [[ $APPLY -eq 1 ]]; then "$@"; fi; }
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
for f in ops/audit/mag_audit_gateway.py ops/audit/mitm_audit_addon.py ops/audit/mag-audit.nft \
         ops/audit/vm-proxy-setup.sh ops/audit/detect-vm-net.sh ops/audit/mag-audit-tail \
         ops/audit/systemd/mag-audit-gateway.service ops/audit/systemd/mag-audit-webproxy.service \
         ops/audit/systemd/mag-audit-nft.service ops/mag-120.d/20-deepseek-audit.conf; do
  [[ -f "$SRC/$f" ]] || { echo "fehlt: $SRC/$f"; exit 1; }
done
id magbot >/dev/null 2>&1 || { echo "User magbot fehlt"; exit 1; }

# Port 8080: frei, oder schon vom Gateway belegt (mag-llm/deepseek.service lauschen ab jetzt auf 8090)
owner=$(ss -Hltnp 'sport = :8080' 2>/dev/null | grep -oE 'users:\(\("[^"]+"' | head -1 | cut -d'"' -f2 || true)
if [[ -n "$owner" && "$owner" != python3* ]]; then
  echo "Port 8080 ist von '$owner' belegt (alter mag-llm oder deepseek.service?). Erst stoppen:"
  echo "  sudo systemctl disable --now deepseek.service   (mag-llm NIE stoppen: Modell würde entladen)"; exit 1
fi

READERS=(chris)
if [[ -n "${ALI_USER:-}" ]]; then READERS+=("$ALI_USER"); elif id ali >/dev/null 2>&1; then READERS+=(ali); fi
for u in "${READERS[@]}"; do id "$u" >/dev/null 2>&1 || { echo "User $u existiert nicht"; exit 1; }; done

echo "== 1. Benutzer/Gruppen (Leser: ${READERS[*]})"
getent group mag-audit-readers >/dev/null || run groupadd --system mag-audit-readers
id mag-audit >/dev/null 2>&1 || run useradd --system --home-dir /var/lib/mag-audit --shell /usr/sbin/nologin mag-audit
for u in "${READERS[@]}"; do
  grep -qx mag-audit-readers <<<"$(id -nG "$u" | tr ' ' '\n')" || run usermod -aG mag-audit-readers "$u"
done

echo "== 2. Verzeichnisse"
run install -d -o mag-audit -g mag-audit-readers -m 2750 /var/log/mag-audit
run install -d -o mag-audit -g mag-audit -m 0750 /var/lib/mag-audit /var/lib/mag-audit/mitmproxy
run install -d -o root -g mag-audit -m 0751 /etc/mag-audit
run install -d -o root -g root -m 0755 /opt/mag-audit /etc/mag-audit/vm
if [[ ! -f /etc/mag-audit/secrets.list ]]; then run install -o root -g mag-audit -m 0640 /dev/null /etc/mag-audit/secrets.list; fi

echo "== 3. Programme nach /opt/mag-audit"
run install -m 0644 "$SRC/ops/audit/mag_audit_gateway.py" "$SRC/ops/audit/mitm_audit_addon.py" /opt/mag-audit/
run install -m 0755 "$SRC/ops/audit/mag-audit-tail" /usr/local/bin/mag-audit-tail

echo "== 4. mitmproxy (eigene venv, auf mag nicht installiert)"
if [[ ! -x /opt/mag-audit/venv/bin/mitmdump ]]; then
  command -v python3 >/dev/null
  python3 -c 'import venv' 2>/dev/null || run apt-get install -y python3-venv
  run python3 -m venv /opt/mag-audit/venv
  run /opt/mag-audit/venv/bin/pip install --quiet 'mitmproxy>=10,<12'
fi
if [[ $APPLY -eq 1 && ! -f /var/lib/mag-audit/mitmproxy/mitmproxy-ca-cert.pem ]]; then
  echo "   CA erzeugen"
  runuser -u mag-audit -- timeout 15 /opt/mag-audit/venv/bin/mitmdump \
    --set confdir=/var/lib/mag-audit/mitmproxy --listen-host 127.0.0.1 -p 18888 >/dev/null 2>&1 || true
  [[ -f /var/lib/mag-audit/mitmproxy/mitmproxy-ca-cert.pem ]] || { echo "CA-Erzeugung fehlgeschlagen"; exit 1; }
fi

echo "== 5. VM-Netz (libvirt)"
VM_ENV=/etc/mag-audit/vmnet.env
if [[ $APPLY -eq 1 ]]; then
  bash "$SRC/ops/audit/detect-vm-net.sh"
else
  sed "s|^OUT=.*|OUT=$TMP/vmnet.env|; s|^install -d -m 0751 /etc/mag-audit|true|; s|^chgrp .*|true|" \
    "$SRC/ops/audit/detect-vm-net.sh" > "$TMP/detect.sh"
  bash "$TMP/detect.sh" || { echo "VM-Netz nicht erkennbar (siehe oben)"; exit 2; }
  VM_ENV=$TMP/vmnet.env
fi
# shellcheck disable=SC1090
source "$VM_ENV"
: "${VM_IFACES:?}" "${VM_SUBNET:?}" "${VM_HOST_IP:?}"

echo "== 6. Konfiguration, CA, VM-Paket"
if [[ $APPLY -eq 1 ]]; then
  printf 'MAG_AUDIT_CLIENT_MAP=%s\n' "$VM_CLIENT_MAP" > /etc/mag-audit/webproxy.env
  chown root:mag-audit /etc/mag-audit/webproxy.env; chmod 0640 /etc/mag-audit/webproxy.env
  install -o root -g mag-audit -m 0644 /var/lib/mag-audit/mitmproxy/mitmproxy-ca-cert.pem /etc/mag-audit/mitmproxy-ca-cert.pem
  cat /etc/ssl/certs/ca-certificates.crt /etc/mag-audit/mitmproxy-ca-cert.pem > /etc/mag-audit/ca-bundle.pem
  chmod 0644 /etc/mag-audit/ca-bundle.pem
  # Für `magbot audit-proxy` (vm.cpp): CA, Gast-Skript, Proxy-URL
  install -m 0644 /etc/mag-audit/mitmproxy-ca-cert.pem /etc/mag-audit/vm/mag-audit-ca.crt
  install -m 0644 "$SRC/ops/audit/vm-proxy-setup.sh" /etc/mag-audit/vm/vm-proxy-setup.sh
  printf 'MAG_PROXY=http://%s:8888\n' "$VM_HOST_IP" > /etc/mag-audit/vm/proxy.env
  chmod 0644 /etc/mag-audit/vm/proxy.env
else
  echo "+ webproxy.env, ca-bundle.pem, /etc/mag-audit/vm/{mag-audit-ca.crt,vm-proxy-setup.sh,proxy.env (http://$VM_HOST_IP:8888)}"
fi

echo "== 7. systemd-Units (Kopien)"
run install -m 0644 "$SRC/ops/audit/systemd/mag-audit-gateway.service" \
  "$SRC/ops/audit/systemd/mag-audit-webproxy.service" "$SRC/ops/audit/systemd/mag-audit-nft.service" /etc/systemd/system/
run install -d /etc/systemd/system/mag-120.service.d
run install -m 0644 "$SRC/ops/mag-120.d/20-deepseek-audit.conf" /etc/systemd/system/mag-120.service.d/20-deepseek-audit.conf
NOPROXY="127.0.0.1,localhost,::1,$VM_SUBNET,$VM_HOST_IP"
if [[ $APPLY -eq 1 ]]; then
  cat > /etc/systemd/system/mag-120.service.d/21-audit-net.conf <<EOF
# erzeugt von ops/audit/install-audit.sh – VM-Netz/Bridge direkt, alles andere über den Audit-Proxy
[Service]
Environment="NO_PROXY=$NOPROXY"
Environment="no_proxy=$NOPROXY"
EOF
else
  echo "+ 21-audit-net.conf: NO_PROXY=$NOPROXY"
fi

echo "== 8. nftables (eigene Tabelle inet mag_audit; mag_agents und /etc/nftables.conf bleiben unberührt)"
UID_AUDIT=$(id -u mag-audit 2>/dev/null || echo 65533)
UID_MAGBOT=$(id -u magbot)
sed -e "s/@UID_MAG_AUDIT@/$UID_AUDIT/g" -e "s/@UID_MAGBOT@/$UID_MAGBOT/g" \
    -e "s|@VM_IFACES@|$VM_IFACES|g" -e "s|@VM_SUBNET@|$VM_SUBNET|g" \
    "$SRC/ops/audit/mag-audit.nft" > "$TMP/mag-audit.nft"
nft -c -f "$TMP/mag-audit.nft" && echo "   Syntax OK"
run install -D -o root -g root -m 0640 "$TMP/mag-audit.nft" /etc/nftables.d/mag-audit.nft
if ! grep -q 8888 <<<"$(nft list table inet mag_agents 2>/dev/null || true)"; then
  echo "   HINWEIS: Tabelle mag_agents erlaubt VM->Host :8888 erst nach dem Deploy dieses Branches"
  echo "            (ops/mag-agents.nft). Bis dahin haben die VMs keinen Web-Zugriff."
fi

echo "== 9. Lesezugriff auf die Raum-Transkripte (/var/lib/magbot/room)"
command -v setfacl >/dev/null || run apt-get install -y acl
ROOM=/var/lib/magbot/room
if [[ -d $ROOM ]]; then
  run setfacl -m g:mag-audit-readers:x /var/lib/magbot "$ROOM"
  for f in room.json agents.json files.json; do
    [[ -e $ROOM/$f ]] && run setfacl -m g:mag-audit-readers:r "$ROOM/$f"
  done
  # magroom schreibt diese Dateien atomar neu -> Default-ACL, damit Neufassungen lesbar bleiben.
  # Bestehende Geheimnisse (token, browser-udp-keys/) bekommen KEINE Leserechte.
  run setfacl -d -m g:mag-audit-readers:r "$ROOM"
  [[ -d $ROOM/files ]] && run setfacl -R -m g:mag-audit-readers:rX "$ROOM/files" && run setfacl -d -m g:mag-audit-readers:rX "$ROOM/files"
fi

echo "== 10. Dienste starten (Gateway übernimmt :8080; LLM-Backend ist ab jetzt :8090)"
run systemctl daemon-reload
run systemctl enable --now mag-audit-nft.service mag-audit-gateway.service mag-audit-webproxy.service
if [[ $APPLY -eq 1 ]]; then
  sleep 2
  curl -fsS --max-time 5 http://127.0.0.1:8080/audit/v1/health >/dev/null && echo "   Gateway OK" \
    || { echo "   FEHLER: Gateway antwortet nicht (journalctl -u mag-audit-gateway)"; exit 1; }
  curl -fsS --max-time 15 -x http://127.0.0.1:8888 -o /dev/null --cacert /etc/mag-audit/ca-bundle.pem https://example.org \
    && echo "   Webproxy OK" || echo "   WARNUNG: Webproxy-Test fehlgeschlagen (journalctl -u mag-audit-webproxy)"
  nft list table inet mag_audit >/dev/null && echo "   nftables aktiv"
fi
echo
echo "Fertig. Gruppe mag-audit-readers wirkt nach neuem Login. Lesen: mag-audit-tail -f"
[[ $APPLY -eq 0 ]] && echo "(Trockenlauf – mit --apply ausführen)"
exit 0
