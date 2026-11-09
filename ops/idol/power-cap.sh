#!/usr/bin/env bash
# Root, run before the Idol worker starts. LOWER-ONLY clamp of the MI50 power
# cap: if the card (PCI 0000:05:00.0) reports more than 72 W, e.g. after a
# reboot reset it to the driver default, set it back to 72 W. This script
# never writes a value above the current one and never above 72 W.
set -uo pipefail
LIMIT=72000000
for cap in /sys/bus/pci/devices/0000:05:00.0/hwmon/hwmon*/power1_cap; do
  [[ -w $cap ]] || continue
  now=$(cat "$cap" 2>/dev/null) || continue
  [[ $now =~ ^[0-9]+$ ]] || continue
  if (( now > LIMIT )); then
    echo "$LIMIT" > "$cap" && echo "MI50 power cap lowered from $now to $LIMIT uW"
  fi
done
exit 0
