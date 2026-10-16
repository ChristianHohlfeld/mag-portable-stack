#!/usr/bin/env bash
# DeepSeek + zentrales Logging auf mag ausrollen. Als root, aus dem Repo (/home/chris/mag-stack).
# Ohne --apply wird nur angezeigt; es wird nichts verändert (kein chmod, keine Zustandsdatei).
#
#   sudo bash ops/deepseek/rollout.sh status
#   sudo bash ops/deepseek/rollout.sh audit [--apply]   Gateway :8080, Web-Proxy :8888, nftables, mag-120-Drop-ins
#   sudo bash ops/deepseek/rollout.sh llm   [--apply]   mag-llm (DeepSeek, :8090) + Modell-Gate (kein Modellstart)
#   sudo bash ops/deepseek/rollout.sh room  [--apply]   mag-120 neu starten (übernimmt Drop-ins), Status zeigen
#   sudo bash ops/deepseek/rollout.sh vms   [--apply]   Audit-Proxy in laufende Agent-VMs (sonst automatisch beim nächsten Browser-Start)
#   sudo bash ops/deepseek/rollout.sh test              Preflight als chris (wenn das Modell bereit ist)
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
step=${1:-status}; APPLY=${2:-}

model_json() { curl -fsS --max-time 5 http://127.0.0.1:8766/api/model-status 2>/dev/null || echo '{}'; }

case "$step" in
  status)
    echo "Repo:    $SRC ($(runuser -u chris -- git -C "$SRC" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?'))"
    echo "Release: $(readlink -f /opt/mag-stack/current) ($(cat /opt/mag-stack/current/SOURCE_COMMIT 2>/dev/null | cut -c1-12))"
    for u in mag-audit-nft mag-audit-gateway mag-audit-webproxy mag-model-gate.timer mag-llm mag-120 mag-browser-webrtc deepseek; do
      printf '  %-24s %-10s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)" "$(systemctl is-enabled "$u" 2>/dev/null)"
    done
    echo "Modell-Gate:"; /usr/local/lib/mag-deepseek/model-gate.sh status 2>/dev/null | sed 's/^/  /' || echo "  (noch nicht installiert)"
    echo "Raum sieht: $(model_json)"
    ;;
  audit) bash "$SRC/ops/audit/install-audit.sh" $APPLY ;;
  llm)   bash "$SRC/ops/deepseek/install-deepseek.sh" $APPLY ;;
  room)
    echo "+ systemctl restart mag-120.service  (übernimmt 20-deepseek-audit.conf / 21-audit-net.conf)"
    if [[ "$APPLY" == --apply ]]; then
      systemctl daemon-reload
      systemctl restart mag-120.service
      for i in $(seq 1 30); do curl -fsS --max-time 2 http://127.0.0.1:8766/readyz >/dev/null && break; sleep 1; done
      curl -fsS --max-time 5 http://127.0.0.1:8766/readyz; echo
      systemctl show mag-120.service -p Environment | tr ' ' '\n' | grep -E 'MAG_ROOM_(MODEL|BACKEND|LLM_TIMEOUT|GUEST_TIMEOUT|MAX_TOKENS|THINKING)='
    fi
    ;;
  vms)
    while read -r id state _; do
      [[ -n "$id" ]] || continue
      if [[ "$state" == ready ]]; then
        echo "+ magbot audit-proxy $id"
        if [[ "$APPLY" == --apply ]]; then /usr/local/bin/magbot audit-proxy "$id" || echo "  FEHLER bei $id"; fi
      else
        echo "  $id ist '$state' – Audit-Proxy wird beim nächsten Browser-Start automatisch eingerichtet"
      fi
    done < <(/usr/local/bin/magbot status 2>/dev/null)
    echo "Hinweis: Ein bereits laufendes Chromium übernimmt Proxy/CA erst nach Neustart der VM (virsh reboot magbot-<id>)."
    ;;
  test)
    grep -q '"ready": *true' <<<"$(model_json)" || { echo "Modell noch nicht bereit: $(model_json)"; exit 1; }
    runuser -u chris -- sg mag-audit-readers -c "python3 -I '$SRC/ops/deepseek/preflight-tests.py'"
    ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
