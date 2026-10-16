#!/usr/bin/env bash
# Findet, WO Secrets im Journal stehen und WELCHER Code sie ausgibt – ohne Werte anzuzeigen.
#   sudo ops/security/secrets-scan.sh [/etc/mag-audit/secrets.list]
# Mit secrets.list (name=wert) wird exakt nach den bekannten Werten gesucht,
# sonst nach typischen Mustern. Ausgabe: Unit, Anzahl Treffer, erste/letzte Zeit, Codestelle.
set -uo pipefail
LIST="${1:-/etc/mag-audit/secrets.list}"
STACK=/opt/mag-stack/current
[[ $EUID -eq 0 ]] || { echo "bitte als root"; exit 1; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
journalctl -o json --no-pager > "$tmp/j.json"

python3 - "$tmp/j.json" "$LIST" <<'EOF'
import json, re, sys, collections
jpath, lpath = sys.argv[1], sys.argv[2]
secrets = []
try:
    for l in open(lpath):
        l = l.rstrip("\n")
        if l and not l.startswith("#") and "=" in l:
            n, v = l.split("=", 1)
            if len(v) >= 6: secrets.append((n.strip(), v))
except FileNotFoundError:
    pass
patterns = [("token-artig", re.compile(r"(?i)(token|secret|private.?key|preshared|udp.?key|wg.?key)\W{0,3}[:=]\s*\S{12,}")),
            ("wireguard-key", re.compile(r"\b[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=")),
            ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"))]
hits = collections.defaultdict(lambda: [0, None, None])
for line in open(jpath, errors="replace"):
    try: e = json.loads(line)
    except ValueError: continue
    msg = e.get("MESSAGE")
    if not isinstance(msg, str): continue
    unit = e.get("_SYSTEMD_UNIT") or e.get("SYSLOG_IDENTIFIER") or "?"
    ts = e.get("__REALTIME_TIMESTAMP")
    found = [n for n, v in secrets if v in msg] or [n for n, p in patterns if p.search(msg)]
    for n in found:
        h = hits[(unit, n)]; h[0] += 1; h[1] = h[1] or ts; h[2] = ts
import datetime
fmt = lambda t: datetime.datetime.fromtimestamp(int(t)/1e6).isoformat(timespec="seconds") if t else "-"
if not hits: print("Keine Treffer im Journal."); sys.exit(0)
print(f"{'UNIT':40} {'SECRET/MUSTER':20} {'ANZAHL':>7}  ERSTE .. LETZTE")
for (u, n), (c, a, b) in sorted(hits.items(), key=lambda x: -x[1][0]):
    print(f"{u:40} {n:20} {c:7}  {fmt(a)} .. {fmt(b)}")
EOF

echo
echo "== Codestellen, die Tokens/Keys ausgeben könnten (in $STACK):"
grep -rnE --include='*.py' --include='*.sh' --include='*.js' --include='*.ts' --include='*.service' --include='*.conf' \
  '(print|echo|log(ger)?\.(info|debug|warning)|console\.log|logging\.).*(token|key|secret)' \
  "$STACK" 2>/dev/null | grep -viE 'redact|mask|\[SECRET' | head -50
echo
echo "== Unit-Dateien mit Secrets in ExecStart/Environment (landen in 'systemctl show' und ggf. im Journal):"
grep -rnE '(ExecStart|Environment)=.*(token|key|secret)' /etc/systemd/system "$STACK/ops" 2>/dev/null | sed -E 's/(=)[A-Za-z0-9+/=_-]{16,}/\1<…>/g'

echo
echo "== Weitere Kopien außerhalb des Journals (rsyslog, alte Logs):"
if [[ -s "$LIST" ]]; then
  python3 - "$LIST" <<'PY'
import sys, glob, os, gzip
vals=[(l.split("=",1)[0], l.split("=",1)[1].rstrip("\n")) for l in open(sys.argv[1]) if "=" in l and not l.startswith("#")]
for p in sorted(glob.glob("/var/log/**/*", recursive=True)):
    if not os.path.isfile(p) or p.startswith("/var/log/journal") or p.startswith("/var/log/mag-audit"): continue
    try:
        op = gzip.open if p.endswith(".gz") else open
        data = op(p, "rb").read().decode("utf-8", "replace")
    except Exception:
        continue
    found = [n for n, v in vals if len(v) >= 6 and v in data]
    if found: print(f"{p}: {', '.join(found)}")
PY
else
  echo "(ohne secrets.list übersprungen)"
fi
