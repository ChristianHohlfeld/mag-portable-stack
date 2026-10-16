#!/usr/bin/env bash
# Installiert git-memory (MCP-Server + CLI "gitmem") in eine Bot-VM.
# Aufruf auf mag:  sudo ops/mcp/git-memory/install-vm.sh magbot-agent1
# Ergebnis in der VM: /opt/magbot/mcp/git-memory/gitmem.py, /usr/local/bin/gitmem,
# MCP-Konfig fuer Nutzer bot: ~/.config/mcp/servers.d/git-memory.json (stdio).
# Idempotent. Rollback: rm -rf /opt/magbot/mcp/git-memory /usr/local/bin/gitmem
#   /home/bot/.config/mcp/servers.d/git-memory.json (Speicher ~/memory + Gitea-Repo bleiben).
set -euo pipefail
DOM="${1:?VM-Domain angeben, z. B. magbot-agent1}"
HERE="$(cd "$(dirname "$0")" && pwd)"
B64="$(base64 -w0 "$HERE/gitmem.py")"
SCRIPT=$(cat <<SH
set -e
install -d -m755 /opt/magbot/mcp/git-memory
echo '$B64' | base64 -d > /opt/magbot/mcp/git-memory/gitmem.py
chmod 755 /opt/magbot/mcp/git-memory/gitmem.py
ln -sfn /opt/magbot/mcp/git-memory/gitmem.py /usr/local/bin/gitmem
install -d -o bot -g bot -m700 /home/bot/.config /home/bot/.config/mcp /home/bot/.config/mcp/servers.d
cat > /home/bot/.config/mcp/servers.d/git-memory.json <<'J'
{"mcpServers":{"git-memory":{"command":"/usr/bin/python3","args":["/opt/magbot/mcp/git-memory/gitmem.py"],"transport":"stdio"}}}
J
chown bot:bot /home/bot/.config/mcp/servers.d/git-memory.json
echo installed
SH
)
CMD=$(python3 -c 'import json,sys;print(json.dumps({"execute":"guest-exec","arguments":{"path":"/bin/bash","arg":["-c",sys.argv[1]],"capture-output":True}}))' "$SCRIPT")
PID=$(virsh qemu-agent-command "$DOM" "$CMD" | python3 -c 'import json,sys;print(json.load(sys.stdin)["return"]["pid"])')
for _ in $(seq 1 30); do
  sleep 1
  R=$(virsh qemu-agent-command "$DOM" "{\"execute\":\"guest-exec-status\",\"arguments\":{\"pid\":$PID}}")
  if echo "$R" | python3 -c 'import json,sys;sys.exit(0 if json.load(sys.stdin)["return"]["exited"] else 1)'; then
    echo "$R" | python3 -c 'import json,sys,base64;r=json.load(sys.stdin)["return"];print(base64.b64decode(r.get("out-data","")).decode(),base64.b64decode(r.get("err-data","")).decode());sys.exit(r.get("exitcode",1))'
    exit $?
  fi
done
echo "Timeout" >&2; exit 1
