#!/usr/bin/env bash
# Root, run before the Idol worker starts. LOWER-ONLY clamp of the MI50 power
# cap: if a card (found by its PCI id 1002:66a1; 0000:05:00.0 on mag) reports
# more than 85 W, e.g. after a reboot reset it to the driver default, set it
# back to 85 W. This script never writes a value above the current one and
# never above 85 W.
set -uo pipefail
LIMIT=85000000
for dev in /sys/bus/pci/devices/*; do
  [[ $(cat "$dev/vendor" 2>/dev/null) == 0x1002 && $(cat "$dev/device" 2>/dev/null) == 0x66a1 ]] || continue
  for cap in "$dev"/hwmon/hwmon*/power1_cap; do
    [[ -w $cap ]] || continue
    now=$(cat "$cap" 2>/dev/null) || continue
    [[ $now =~ ^[0-9]+$ ]] || continue
    if (( now > LIMIT )); then
      echo "$LIMIT" > "$cap" && echo "MI50 power cap lowered from $now to $LIMIT uW"
    fi
  done
done
exit 0
