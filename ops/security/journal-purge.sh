#!/usr/bin/env bash
# Entfernt geleakte Secrets aus dem systemd-Journal.
# journald kann keine einzelnen Einträge löschen – nur ganze Journal-Dateien. Daher:
#   1) komplettes Journal exportieren, dabei alle bekannten Secrets durch [SECRET:name] ersetzen
#      -> /var/log/mag-audit/journal-archive/journal-<zeit>.json.zst (lesbar für mag-audit-readers)
#   2) Journal rotieren und ALLE archivierten Journal-Dateien löschen
#
#   sudo ops/security/journal-purge.sh            -> nur Export + Vorschau
#   sudo ops/security/journal-purge.sh --yes      -> Export + Löschen
#
# Erst NACH der Rotation der Secrets ausführen (dann sind die alten Werte ohnehin wertlos,
# und neu ausgegebene Werte werden durch LogFilterPatterns/Code-Fix nicht mehr geloggt).
set -euo pipefail
LIST=/etc/mag-audit/secrets.list
OUT=/var/log/mag-audit/journal-archive
[[ $EUID -eq 0 ]] || { echo "bitte als root"; exit 1; }
[[ -s $LIST ]] || { echo "$LIST fehlt/leer – ohne bekannte Secret-Werte kein sicherer Export."; exit 1; }
command -v zstd >/dev/null || apt-get install -y zstd

install -d -o mag-audit -g mag-audit-readers -m 2750 "$OUT"
f="$OUT/journal-$(date -u +%Y%m%dT%H%M%SZ).json.zst"
journalctl -o json --no-pager | python3 -c '
import sys
pairs=[]
for l in open(sys.argv[1]):
    l=l.rstrip("\n")
    if l and not l.startswith("#") and "=" in l:
        n,v=l.split("=",1)
        if len(v)>=6: pairs.append((v,n.strip()))
pairs.sort(key=lambda p:-len(p[0]))
for line in sys.stdin:
    for v,n in pairs:
        if v in line: line=line.replace(v,"[SECRET:"+n+"]")
    sys.stdout.write(line)
' "$LIST" | zstd -q -19 -T0 -o "$f"
chown mag-audit:mag-audit-readers "$f"; chmod 0640 "$f"
echo "Export: $f ($(du -h "$f" | cut -f1))"

# Kontrolle: kein bekannter Wert mehr im Export
left=$(zstdcat "$f" | python3 -c '
import sys
vals=[l.split("=",1)[1].rstrip("\n") for l in open(sys.argv[1]) if "=" in l and not l.startswith("#")]
data=sys.stdin.read(); print(sum(data.count(v) for v in vals if len(v)>=6))' "$LIST")
[[ "$left" == 0 ]] || { echo "ABBRUCH: Export enthält noch $left Secret-Vorkommen"; exit 1; }
echo "Export ist secret-frei."

if [[ "${1:-}" != "--yes" ]]; then
  echo "Vorschau: journalctl --disk-usage = $(journalctl --disk-usage | grep -o '[0-9.]*[KMGT]')"
  echo "Zum Löschen erneut mit --yes aufrufen."
  exit 0
fi
journalctl --rotate
journalctl --vacuum-time=1s
echo "Journal geleert. Neue Einträge laufen ab jetzt mit Filter (LogFilterPatterns)."
