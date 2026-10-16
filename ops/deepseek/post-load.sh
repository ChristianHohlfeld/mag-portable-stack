#!/usr/bin/env bash
# Läuft nach jedem Start von mag-llm als eigene transiente Unit (mag-llm-postload, siehe ExecStartPost):
# wartet, bis llama bereit ist, und entfernt dann die GGUF-Dateien aus dem Page-Cache.
# Hintergrund: Ohne mmap kopiert llama die Gewichte in seinen eigenen Speicher; die beim Lesen
# entstandenen Cache-Seiten wären eine zweite, nutzlose Kopie (~144 GB).
# Übersprungen, falls llama die Dateien doch per mmap nutzt (dann SIND die Cache-Seiten das Modell).
set -uo pipefail
DIR=/home/chris/models/deepseek-v4
FILES=("$DIR"/DeepSeek-V4-Flash-0731-UD-Q4_K_XL-0000{1..5}-of-00005.gguf)
for _ in $(seq 1 1080); do          # bis 3 h
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health || true)
  [[ "$code" == 200 ]] && break
  systemctl is-active --quiet mag-llm.service || { echo "mag-llm nicht aktiv – Abbruch"; exit 0; }
  sleep 10
done
[[ "${code:-}" == 200 ]] || { echo "mag-llm nach 3 h nicht bereit – nichts geändert"; exit 0; }
systemctl is-active --quiet mag-model-pin && { echo "mag-model-pin aktiv – Page-Cache bleibt"; exit 0; }
pid=$(systemctl show -p MainPID --value mag-llm.service)
if [[ -n "$pid" && "$pid" != 0 ]] && grep -q 'DeepSeek-V4-Flash-0731.*\.gguf' "/proc/$pid/maps" 2>/dev/null; then
  echo "llama nutzt die Dateien per mmap – Page-Cache bleibt"; exit 0
fi
python3 -I - "${FILES[@]}" <<'PY'
import os, sys
total = 0
for f in sys.argv[1:]:
    try:
        fd = os.open(f, os.O_RDONLY)
        total += os.fstat(fd).st_size
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        os.close(fd)
    except OSError as e:
        print(f"{f}: {e}")
print(f"Page-Cache der Modelldateien freigegeben ({total/2**30:.0f} GiB Dateien)")
PY
