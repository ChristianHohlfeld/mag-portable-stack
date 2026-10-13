#!/usr/bin/env python3
"""Read-only MAGBOT observability; PM2 supervises this, libvirt owns VMs.

No spawn, despawn, reboot, domain start/stop or root credentials in PM2.
Only exact sudoers-whitelisted read-only commands are allowed.
"""
import datetime
import os
import signal
import subprocess
import time
from pathlib import Path

RUNNING = True
INTERVAL = 60
GIB = 1024 ** 3
RAM_RESERVE = 64
DISK_RESERVE = 100

def shutdown(_sig, _frame):
    global RUNNING
    RUNNING = False

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


def check():
    result = subprocess.run(
        ['/usr/bin/sudo', '-n', '/usr/local/bin/magbot', 'status'],
        capture_output=True, text=True, timeout=90, env={**os.environ, 'LC_ALL':'C'},
    )
    status = result.stdout.strip() if result.returncode == 0 else ('ERROR: ' + (result.stderr or result.stdout).strip())
    info = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            info['ram_gib'] = int(line.split()[1]) * 1024 / GIB
            break
    disk = os.statvfs('/var/lib/libvirt/images/magbot')
    info['disk_gib'] = disk.f_bavail * disk.f_frsize / GIB
    warnings = []
    if info.get('ram_gib', 0) < RAM_RESERVE:
        warnings.append('LOW HOST MEMORY')
    if info['disk_gib'] < DISK_RESERVE:
        warnings.append('LOW HOST DISK')
    if any(' timeout ' in (' ' + line + ' ') or ' failed ' in (' ' + line + ' ') for line in status.splitlines()):
        warnings.append('BOT BOOTSTRAP FAILURE')
    return status, info, warnings


def main():
    last = None
    next_heartbeat = 0
    while RUNNING:
        try:
            status, info, warnings = check()
            marker = (status, tuple(warnings))
            if marker != last or time.monotonic() >= next_heartbeat:
                print(f'{datetime.datetime.now(datetime.timezone.utc).isoformat()} MAGBOT '
                      f'MemAvailable={info.get("ram_gib", 0):.1f}GiB FreeDisk={info["disk_gib"]:.1f}GiB'
                      + (f' WARNING={",".join(warnings)}' if warnings else '')
                      + '\n' + status, flush=True)
                next_heartbeat = time.monotonic() + 600
                last = marker
        except Exception as exc:
            marker = f'WATCH ERROR: {type(exc).__name__}: {exc}'
            if marker != last:
                print(marker, flush=True)
                last = marker
        # Interruptible wait so PM2 can stop without a forced kill.
        for _ in range(INTERVAL):
            if not RUNNING:
                break
            time.sleep(1)

if __name__ == '__main__':
    main()
