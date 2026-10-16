#!/usr/bin/env bash
# Ermittelt Netz der Agent-VMs und schreibt /etc/mag-audit/vmnet.env:
#   VM_IFACES='"virbr0"'           Host-Interface(s) (nft-Syntax), über die VM-Verkehr kommt
#   VM_SUBNET='192.168.122.0/24'   Netz der VMs
#   VM_HOST_IP='192.168.122.1'     Host-Adresse im VM-Netz (Proxy-Adresse für die VMs)
#   VM_CLIENT_MAP='…=agent1,…'      Client-IP oder CIDR -> Agent-Name (für das Web-Audit-Log)
#   VM_DOMAINS='magbot-agent1'      erkannte libvirt-Domains
#
#   sudo ops/audit/detect-vm-net.sh
#   sudo VM_NAMES="magbot-agent1" ops/audit/detect-vm-net.sh
# Funktioniert auch bei ausgeschalteten VMs (Werte aus der libvirt-Netzdefinition).
# Bei Mehrdeutigkeit: Abbruch mit Kandidaten; dann VM_IFACES/VM_SUBNET/VM_HOST_IP/VM_CLIENT_MAP setzen.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "bitte als root"; exit 1; }
OUT=/etc/mag-audit/vmnet.env
V="virsh -q -c qemu:///system"

ifaces=(); map=(); subnets=(); hostips=(); domains=()

if [[ -z "${VM_IFACES:-}" ]] && command -v virsh >/dev/null; then
  if [[ -n "${VM_NAMES:-}" ]]; then
    read -r -a names <<<"$VM_NAMES"
  else
    # Agent-Domains automatisch: magbot-agent1, magbot-agent2, … (auch ausgeschaltete)
    mapfile -t names < <($V list --all --name 2>/dev/null | grep -E '(^|-)agent[0-9]*$' || true)
  fi
  [[ ${#names[@]} -gt 0 ]] || { echo "Keine Agent-Domains gefunden ($V list --all):"; $V list --all; exit 2; }
  for vm in "${names[@]}"; do
    $V dominfo "$vm" >/dev/null 2>&1 || { echo "libvirt: Domain $vm nicht gefunden"; continue; }
    domains+=("$vm")
    label=$(sed -E 's/^magbot-//' <<<"$vm")          # magbot-agent1 -> agent1
    state=$($V domstate "$vm" 2>/dev/null || echo "?")
    echo "Domain $vm ($state) -> Label $label"
    # domiflist: Interface Type Source Model MAC  (Interface ist "-" bei ausgeschalteter VM)
    while read -r _dev type source _model mac; do
      [[ -z "$mac" ]] && continue
      br="" ; net_subnet="" ; net_host=""
      if [[ "$type" == "network" ]]; then
        xml=$($V net-dumpxml "$source" 2>/dev/null || true)
        br=$(grep -oP "<bridge name='\K[^']+" <<<"$xml" | head -1 || true)
        net_host=$(grep -oP "<ip address='\K[0-9.]+" <<<"$xml" | head -1 || true)
        pfx=$(grep -oP "<ip address='[0-9.]+' (prefix='\K[0-9]+|netmask='\K[0-9.]+)" <<<"$xml" | head -1 || true)
        [[ -n "$net_host" && -n "$pfx" ]] && net_subnet=$(python3 -c "import ipaddress,sys;print(ipaddress.ip_interface(sys.argv[1]+'/'+sys.argv[2]).network)" "$net_host" "$pfx")
        static_ip=$(grep -oP "<host mac='(?i:$mac)'[^>]*ip='\K[0-9.]+" <<<"$xml" | head -1 || true)
      else
        br="$source"; static_ip=""
      fi
      [[ -n "$br" ]] && ifaces+=("$br")
      if [[ -z "$net_subnet" && -n "$br" ]]; then
        cidr=$(ip -4 -o addr show dev "$br" 2>/dev/null | awk '{print $4}' | head -1 || true)
        if [[ -n "$cidr" ]]; then
          net_host=${cidr%/*}
          net_subnet=$(python3 -c "import ipaddress,sys;print(ipaddress.ip_interface(sys.argv[1]).network)" "$cidr")
        fi
      fi
      [[ -n "$net_subnet" ]] && subnets+=("$net_subnet")
      [[ -n "$net_host" ]] && hostips+=("$net_host")
      ip="$static_ip"
      if [[ -z "$ip" && "$state" == "running" ]]; then
        ip=$($V domifaddr "$vm" --source lease 2>/dev/null | awk -v m="$mac" 'tolower($2)==tolower(m){split($4,a,"/");print a[1]}' | head -1)
        [[ -z "$ip" ]] && ip=$($V domifaddr "$vm" --source arp 2>/dev/null | awk -v m="$mac" 'tolower($2)==tolower(m){split($4,a,"/");print a[1]}' | head -1)
      fi
      if [[ -n "$ip" ]]; then map+=("$ip=$label"); echo "   $mac -> $ip ($br)"
      else echo "   $mac -> keine feste IP ($br, VM $state) – Zuordnung über das Subnetz"; fi
    done < <($V domiflist "$vm" 2>/dev/null)
  done
fi

if [[ -z "${VM_IFACES:-}" ]]; then
  mapfile -t ifaces < <(printf '%s\n' "${ifaces[@]}" | grep -v '^$' | sort -u || true)
  mapfile -t subnets < <(printf '%s\n' "${subnets[@]}" | grep -v '^$' | sort -u || true)
  mapfile -t hostips < <(printf '%s\n' "${hostips[@]}" | grep -v '^$' | sort -u || true)
  if [[ ${#ifaces[@]} -eq 0 || ${#subnets[@]} -eq 0 ]]; then
    echo "VM-Netz nicht automatisch erkannt. Kandidaten:"
    ip -br addr | awk '$1 ~ /^(virbr|br|vnet|tap|wg|incusbr|lxdbr)/'
    echo "-> VM_IFACES/VM_SUBNET/VM_HOST_IP/VM_CLIENT_MAP setzen (siehe Kopf dieser Datei)."
    exit 2
  fi
  if [[ ${#subnets[@]} -gt 1 ]]; then
    echo "Mehrere VM-Netze: ${subnets[*]} – bitte VM_SUBNET/VM_HOST_IP explizit setzen."; exit 2
  fi
  VM_IFACES=$(printf '"%s", ' "${ifaces[@]}"); VM_IFACES=${VM_IFACES%, }
  VM_SUBNET=${VM_SUBNET:-${subnets[0]}}
  VM_HOST_IP=${VM_HOST_IP:-${hostips[0]:-}}
  if [[ -z "${VM_CLIENT_MAP:-}" ]]; then
    if [[ ${#map[@]} -gt 0 ]]; then
      VM_CLIENT_MAP=$(IFS=,; echo "${map[*]}")
    elif [[ ${#domains[@]} -eq 1 ]]; then
      # genau eine Agent-VM: das ganze VM-Netz ihr zuordnen
      VM_CLIENT_MAP="$VM_SUBNET=$(sed -E 's/^magbot-//' <<<"${domains[0]}")"
    else
      VM_CLIENT_MAP=""
      echo "WARNUNG: keine IP-Zuordnung möglich (VMs aus, keine festen DHCP-Einträge) – Log zeigt 'unknown'."
    fi
  fi
else
  if [[ "$VM_IFACES" != *\"* ]]; then
    VM_IFACES=$(tr ',' '\n' <<<"$VM_IFACES" | sed 's/^ *//;s/ *$//;s/.*/"&"/' | paste -sd, - | sed 's/,/, /g')
  fi
  : "${VM_SUBNET:?VM_SUBNET fehlt}" "${VM_HOST_IP:?VM_HOST_IP fehlt}" "${VM_CLIENT_MAP:=}"
fi
: "${VM_HOST_IP:?Host-IP im VM-Netz nicht ermittelbar – VM_HOST_IP setzen}"

python3 - "$VM_SUBNET" "$VM_HOST_IP" <<'EOF' || exit 2
import ipaddress, sys
net = ipaddress.ip_network(sys.argv[1], strict=False); host = ipaddress.ip_address(sys.argv[2])
assert host in net, f"VM_HOST_IP {host} liegt nicht in {net}"
EOF
case ",$VM_CLIENT_MAP," in *",127.0.0.1=host,"*) ;; *) VM_CLIENT_MAP="${VM_CLIENT_MAP:+$VM_CLIENT_MAP,}127.0.0.1=host" ;; esac

install -d -m 0751 /etc/mag-audit
umask 027
cat > "$OUT" <<EOF
# erzeugt von detect-vm-net.sh am $(date -Is)
VM_IFACES='$VM_IFACES'
VM_SUBNET='$VM_SUBNET'
VM_HOST_IP='$VM_HOST_IP'
VM_CLIENT_MAP='$VM_CLIENT_MAP'
VM_DOMAINS='${domains[*]:-${VM_NAMES:-}}'
EOF
chgrp mag-audit "$OUT" 2>/dev/null || true
echo "geschrieben: $OUT"; cat "$OUT"
