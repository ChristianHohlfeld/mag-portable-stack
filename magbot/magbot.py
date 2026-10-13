#!/usr/bin/env python3
"""MAG bot VM lifecycle. Isolated libvirt VMs with bounded admission and teardown."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path('/var/lib/libvirt/images/magbot')
CONFIG = Path('/etc/magbot/config.json')
LOCK = Path('/run/lock/magbot.lock')
DOMAIN_PREFIX = 'magbot-'
VALID_NAME = re.compile(r'^[a-z][a-z0-9-]{0,31}$')
GIB = 1024 ** 3
DEFAULTS = {
    'max_bots': 4,
    'max_total_vcpus': 8,
    'max_total_ram_gib': 24,
    'min_host_available_ram_gib': 64,
    'min_host_free_disk_gib': 100,
    'max_host_load_per_cpu': 0.75,
    'default_vcpus': 2,
    'default_ram_gib': 4,
    'default_disk_gib': 20,
    'max_bot_vcpus': 4,
    'max_bot_ram_gib': 8,
    'max_bot_disk_gib': 32,
    'disk_bytes_sec': 40000000,
    'disk_iops_sec': 400,
    'network': 'default',
    'base_url': 'https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img',
}

class MagbotError(Exception):
    pass


def run(args, *, check=True, text=True, input=None, timeout=120):
    # virsh domstate/net-info are localized; lifecycle logic needs stable English values.
    env = dict(os.environ, LC_ALL='C')
    r = subprocess.run(args, check=False, text=text, input=input, capture_output=True, timeout=timeout, env=env)
    if check and r.returncode:
        raise MagbotError(f"{' '.join(map(str, args))}: {(r.stderr or r.stdout).strip()}")
    return r


def config():
    result = DEFAULTS.copy()
    if CONFIG.exists():
        loaded = json.loads(CONFIG.read_text())
        unknown = set(loaded) - set(DEFAULTS)
        if unknown:
            raise MagbotError(f'Unknown config properties: {sorted(unknown)}')
        result.update(loaded)
    for key in DEFAULTS:
        if key not in ('network', 'base_url'):
            if not isinstance(result[key], (int, float)) or isinstance(result[key], bool) or result[key] <= 0:
                raise MagbotError(f'Invalid config {key}')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', result['network']):
        raise MagbotError('Invalid libvirt network')
    return result


def require_root():
    if os.geteuid() != 0:
        raise MagbotError('Run with sudo: sudo magbot ...')


class locked:
    def __enter__(self):
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        self.fd = LOCK.open('w')
        fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX)
        return self
    def __exit__(self, *_):
        self.fd.close()


def valid_id(name):
    if not VALID_NAME.fullmatch(name) or name.endswith('-'):
        raise MagbotError('Bot ID: 1-32 lower-case a-z, 0-9, hyphen; first char a letter')
    return name


def bot_dir(name):
    valid_id(name)
    return ROOT / 'bots' / name


def domain(name):
    return DOMAIN_PREFIX + valid_id(name)


def write_registry(path, record):
    """Publish registry atomically; status must never see truncated JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.metadata.{os.getpid()}.tmp')
    try:
        with tmp.open('x') as stream:
            stream.write(json.dumps(record, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def definitions():
    result = []
    for p in sorted((ROOT / 'bots').glob('*/metadata.json')):
        if p.parent.is_symlink() or p.is_symlink():
            raise MagbotError(f'Unsafe symlink in bot registry: {p}')
        obj = json.loads(p.read_text())
        if obj.get('id') != p.parent.name or obj.get('domain') != domain(p.parent.name):
            raise MagbotError(f'Invalid bot registry: {p}')
        result.append(obj)
    return result


def vm_exists(dom):
    return run(['virsh', '-c', 'qemu:///system', 'dominfo', dom], check=False).returncode == 0


def vm_state(dom):
    r = run(['virsh', '-c', 'qemu:///system', 'domstate', dom], check=False)
    return r.stdout.strip() if r.returncode == 0 else 'undefined'


def bytes_available():
    stats = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        parts = line.split()
        if parts[0] == 'MemAvailable:':
            stats['ram'] = int(parts[1]) * 1024
            break
    stats['disk'] = shutil.disk_usage(ROOT).free
    return stats


def host_admission(cfg, existing, vcpus, ram, disk):
    if len(existing) >= cfg['max_bots']:
        raise MagbotError(f"Max bot count reached ({cfg['max_bots']})")
    if sum(i['vcpus'] for i in existing) + vcpus > cfg['max_total_vcpus']:
        raise MagbotError('Total vCPU budget exceeded')
    if sum(i['ram_gib'] for i in existing) + ram > cfg['max_total_ram_gib']:
        raise MagbotError('Total VM RAM budget exceeded')
    if vcpus > cfg['max_bot_vcpus'] or ram > cfg['max_bot_ram_gib'] or disk > cfg['max_bot_disk_gib']:
        raise MagbotError('Per-VM resource limit exceeded')
    if ram < 2 or disk < 12 or vcpus < 1:
        raise MagbotError('Minimum VM size: 1 vCPU, 2 GiB RAM, 12 GiB disk')
    stats = bytes_available()
    if stats['ram'] < (ram + cfg['min_host_available_ram_gib']) * GIB:
        raise MagbotError('Insufficient available RAM after required host reserve')
    # Conservative disk accounting: reserve each bot's entire *virtual* disk even if sparse.
    remaining_existing = sum(i['disk_gib'] for i in existing) * GIB
    required_disk = cfg['min_host_free_disk_gib'] * GIB + remaining_existing + disk * GIB
    if stats['disk'] < required_disk:
        raise MagbotError('Insufficient disk capacity including all virtual disk reservations')
    cpus = os.cpu_count() or 1
    if os.getloadavg()[0] > cfg['max_host_load_per_cpu'] * cpus:
        raise MagbotError('Host load too high; denying spawn')


def preflight():
    if not Path('/dev/kvm').exists():
        raise MagbotError('/dev/kvm missing: KVM unavailable; check VT-x and kvm_intel')
    for cmd in ('virsh', 'qemu-img', 'cloud-localds', 'virt-install'):
        if not shutil.which(cmd):
            raise MagbotError(f'{cmd} missing; run install-magbot.sh')
    if run(['virsh', '-c', 'qemu:///system', 'net-info', config()['network']], check=False).returncode != 0:
        raise MagbotError('Configured libvirt NAT network missing')


def ensure_network(cfg):
    net = cfg['network']
    info = run(['virsh', '-c', 'qemu:///system', 'net-info', net], check=False)
    if info.returncode:
        raise MagbotError(f'libvirt network {net} missing')
    if 'Active:         yes' not in info.stdout and not re.search(r'Active:\s+yes', info.stdout):
        run(['virsh', '-c', 'qemu:///system', 'net-start', net])
    run(['virsh', '-c', 'qemu:///system', 'net-autostart', net])


def base_image():
    # Prefer a prepared golden image (browser preinstalled) for fast bot spawn.
    golden = ROOT / 'base' / 'golden.qcow2'
    if golden.is_file() and golden.stat().st_size > 100 * 1024 * 1024:
        return golden
    base = ROOT / 'base' / 'ubuntu-noble.qcow2'
    if not base.is_file() or base.stat().st_size < 100 * 1024 * 1024:
        raise MagbotError('Base image missing; run: sudo magbot image-update')
    return base


def guest_cloudinit(name):
    # The isolated guest gets a dedicated unprivileged account and no host mounts.
    # Browser is placed in a shared (inside guest only) /opt directory.
    bootstrap = r'''#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PLAYWRIGHT_BROWSERS_PATH=/opt/magbot/browsers
mkdir -p /opt/magbot /var/lib/magbot
if [ ! -x /opt/magbot/venv/bin/python ] || [ ! -d /opt/magbot/browsers ]; then
  python3 -m venv /opt/magbot/venv
  /opt/magbot/venv/bin/python -m pip install --no-input --disable-pip-version-check 'playwright==1.55.0'
  /opt/magbot/venv/bin/python -m playwright install --with-deps chromium
  touch /opt/magbot/.browser-installed
fi
chmod -R a+rX /opt/magbot
cat > /opt/magbot/health.py <<'EOPY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('data:text/html,<title>MAGBOT_READY</title>')
    assert page.title() == 'MAGBOT_READY'
    browser.close()
EOPY
chown -R bot:bot /home/bot
runuser -u bot -- env PLAYWRIGHT_BROWSERS_PATH=/opt/magbot/browsers /opt/magbot/venv/bin/python /opt/magbot/health.py
touch /var/lib/magbot/ready
'''
    user_data = '''#cloud-config
users:
  - default
  - name: bot
    shell: /bin/bash
    lock_passwd: true
    sudo: false
package_update: true
packages:
  - qemu-guest-agent
  - python3-venv
  - python3-pip
  - curl
write_files:
  - path: /usr/local/sbin/magbot-bootstrap
    owner: root:root
    permissions: '0700'
    content: |
''' + ''.join('      '+line+'\n' for line in bootstrap.splitlines()) + '''runcmd:
  - [systemctl, enable, --now, qemu-guest-agent]
  - [bash, -c, '/usr/local/sbin/magbot-bootstrap > /var/log/magbot-bootstrap.log 2>&1 || { touch /var/lib/magbot/failed; exit 1; }']
final_message: 'MAGBOT cloud-init finished'
'''
    meta_data = f'instance-id: magbot-{name}\nlocal-hostname: magbot-{name}\n'
    return user_data, meta_data


def provision(name, vcpus, ram, disk, cfg):
    target = bot_dir(name)
    if target.exists() or vm_exists(domain(name)):
        raise MagbotError('Bot ID already exists or has an orphaned VM')
    existing = definitions()
    # Fail closed for orphaned libvirt domains not in our registry.
    existing_domains = run(['virsh', '-c', 'qemu:///system', 'list', '--all', '--name']).stdout.splitlines()
    recorded = {domain(x['id']) for x in existing}
    orphans = [d for d in existing_domains if d.startswith(DOMAIN_PREFIX) and d not in recorded]
    if orphans:
        raise MagbotError(f'Orphaned libvirt domains require attention: {orphans}')
    host_admission(cfg, existing, vcpus, ram, disk)
    ensure_network(cfg)
    base = base_image()
    target.mkdir(mode=0o711, parents=True)
    record = {'id': name, 'domain': domain(name), 'vcpus': vcpus,
              'ram_gib': ram, 'disk_gib': disk, 'created_at': int(time.time()),
              'state': 'provisioning'}
    write_registry(target / 'metadata.json', record)
    try:
        run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(base), str(target / 'root.qcow2'), f'{disk}G'])
        (target / 'user-data').write_text(guest_cloudinit(name)[0])
        (target / 'meta-data').write_text(guest_cloudinit(name)[1])
        run(['cloud-localds', str(target / 'seed.iso'), str(target / 'user-data'), str(target / 'meta-data')])
        # libvirt runs QEMU as libvirt-qemu; directory traversable but never writeable.
        target.chmod(0o711)
        shutil.chown(target / 'root.qcow2', group='kvm')
        (target / 'root.qcow2').chmod(0o660)
        (target / 'seed.iso').chmod(0o644)
        run(['virt-install', '--connect', 'qemu:///system', '--virt-type', 'kvm', '--name', domain(name),
             '--memory', str(ram * 1024), '--vcpus', str(vcpus),
             '--cpu', 'host-passthrough', '--import', '--noautoconsole',
             '--osinfo', 'detect=off,require=off', '--graphics', 'none',
             '--network', f"network={cfg['network']},model=virtio",
             '--disk', f'path={target}/root.qcow2,format=qcow2,bus=virtio',
             '--disk', f'path={target}/seed.iso,device=cdrom,readonly=on',
             '--channel', 'unix,target.type=virtio,target.name=org.qemu.guest_agent.0',
             '--boot', 'hd', '--check', 'all=on'], timeout=180)
        # CPU quota is set on both live and persistent VM. Fail closed if unsupported.
        # 2 vCPUs @ 100000us period = at most 2 physical CPU-seconds per real second.
        r = run(['virsh', '-c', 'qemu:///system', 'schedinfo', domain(name),
                 '--set', 'vcpu_period=100000', '--set', f'vcpu_quota={vcpus*100000}', '--live', '--config'], check=False)
        if r.returncode:
            raise MagbotError('Could not enforce CPU quota: ' + r.stderr.strip())
        # Throttle guest disk IO, fail closed if unsupported; avoid saturating storage.
        r = run(['virsh', '-c', 'qemu:///system', 'blkdeviotune', domain(name), 'vda',
                 '--total-bytes-sec', str(cfg['disk_bytes_sec']),
                 '--total-iops-sec', str(cfg['disk_iops_sec']),
                 '--live', '--config'], check=False)
        if r.returncode:
            raise MagbotError('Could not enforce disk IO quota: ' + r.stderr.strip())
        record['state'] = 'booting'
        write_registry(target / 'metadata.json', record)
    except BaseException:
        teardown(name, force=True)
        raise
    return record


def teardown(name, force=False):
    target = bot_dir(name)
    marker = target / 'metadata.json'
    if not marker.is_file() or marker.is_symlink() or target.is_symlink():
        if target.exists():
            raise MagbotError('Missing or invalid ownership marker; refusing deletion')
        if vm_exists(domain(name)):
            raise MagbotError('Orphaned VM without ownership marker; refusing deletion')
        return False
    record = json.loads(marker.read_text())
    if record.get('id') != name or record.get('domain') != domain(name):
        raise MagbotError('VM ownership marker mismatch; refusing deletion')
    dom = domain(name)
    if vm_exists(dom):
        state = vm_state(dom)
        if 'running' in state or 'paused' in state or 'idle' in state:
            run(['virsh', '-c', 'qemu:///system', 'destroy', dom])
        r = run(['virsh', '-c', 'qemu:///system', 'undefine', dom, '--nvram'], check=False)
        if r.returncode and vm_exists(dom):
            r = run(['virsh', '-c', 'qemu:///system', 'undefine', dom], check=False)
            if r.returncode:
                raise MagbotError('Unable to undefine VM; refusing to delete disk')
        if vm_exists(dom):
            raise MagbotError('VM still defined; refusing deletion')
    # No symlink traversal; only guarded path inside this bot's dedicated directory.
    shutil.rmtree(target)
    return True


def qga(dom, cmd, timeout=10):
    return json.loads(run(['virsh', '-c', 'qemu:///system', 'qemu-agent-command', dom,
                           json.dumps(cmd), '--timeout', str(timeout)], timeout=timeout+5).stdout)


def guest_exec(dom, argv, timeout=60):
    response = qga(dom, {'execute': 'guest-exec', 'arguments': {'path': argv[0], 'arg': argv[1:], 'capture-output': True}})
    pid = response['return']['pid']
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = qga(dom, {'execute':'guest-exec-status', 'arguments':{'pid': pid}})['return']
        if result.get('exited'):
            out = base64.b64decode(result.get('out-data', '')).decode('utf-8', errors='replace')
            err = base64.b64decode(result.get('err-data', '')).decode('utf-8', errors='replace')
            return result.get('exitcode', 1), out, err
        time.sleep(0.4)
    raise MagbotError(f'Guest command timed out after {timeout}s')


def ready(name):
    dom = domain(name)
    if vm_state(dom) != 'running':
        return 'stopped'
    try:
        code, _, _ = guest_exec(dom, ['/usr/bin/test', '-f', '/var/lib/magbot/ready'], timeout=8)
        if code == 0:
            return 'ready'
        code, _, _ = guest_exec(dom, ['/usr/bin/test', '-f', '/var/lib/magbot/failed'], timeout=8)
        return 'failed' if code == 0 else 'installing'
    except (MagbotError, KeyError, ValueError, subprocess.TimeoutExpired):
        return 'booting'


def inspect_bot(name):
    """Read-only diagnostics: never invent a bot or purge an orphan."""
    name = valid_id(name)
    target = bot_dir(name)
    dom = domain(name)
    marker = target / 'metadata.json'
    record = None
    error = None
    if marker.is_file() and not marker.is_symlink() and not target.is_symlink():
        try:
            record = json.loads(marker.read_text())
        except (ValueError, OSError) as exc:
            error = str(exc)
    elif marker.exists() or target.is_symlink():
        error = 'Invalid marker or symlink'
    info = {
        'id': name,
        'domain': dom,
        'registry_directory_exists': target.exists(),
        'registry_marker_exists': marker.is_file(),
        'registry_valid': bool(record and record.get('id') == name and record.get('domain') == dom),
        'vm_defined': vm_exists(dom),
        'vm_state': vm_state(dom),
        'guest_disk_exists': (target / 'root.qcow2').is_file(),
        'cloudinit_seed_exists': (target / 'seed.iso').is_file(),
    }
    if error:
        info['registry_error'] = error
    print(json.dumps(info, indent=2, sort_keys=True))
    return 0 if info['registry_valid'] and info['vm_defined'] else 1


def print_status(name=None):
    rows = definitions()
    if name:
        rows = [r for r in rows if r['id'] == valid_id(name)]
        if not rows:
            raise MagbotError(f'Unknown bot: {name}')
    for r in rows:
        print(f"{r['id']:<33} {ready(r['id']):<12} {r['vcpus']} vCPU  {r['ram_gib']} GiB RAM  {r['disk_gib']} GiB disk")
    if not rows:
        print('No bots')


def image_update(cfg):
    base_dir = ROOT / 'base'
    base_dir.mkdir(parents=True, exist_ok=True)
    base = base_dir / 'ubuntu-noble.qcow2'
    if base.exists():
        print(f'Base image exists: {base}; keeping immutable (no updates while overlays exist)')
        return
    if not cfg['base_url'].startswith('https://cloud-images.ubuntu.com/'):
        raise MagbotError('Base image URL must be an official HTTPS Ubuntu cloud image')
    stats = bytes_available()
    if stats['disk'] < (cfg['min_host_free_disk_gib'] + 5) * GIB:
        raise MagbotError('Insufficient disk for base image')
    temp = base.with_suffix('.download')
    try:
        run(['curl', '-fL', '--retry', '3', '--connect-timeout', '20',
             '--max-time', '1200', '-o', str(temp), cfg['base_url']], timeout=1230)
        checksums = run(['curl', '-fsSL', '--connect-timeout', '20', '--max-time', '120',
                         cfg['base_url'].rsplit('/', 1)[0] + '/SHA256SUMS'], timeout=150).stdout
        image_name = cfg['base_url'].rsplit('/', 1)[1]
        hashes = [line.split()[0] for line in checksums.splitlines()
                  if len(line.split()) >= 2 and line.split()[1].lstrip('*') == image_name]
        if len(hashes) != 1 or not re.fullmatch(r'[a-fA-F0-9]{64}', hashes[0]):
            raise MagbotError('Official image SHA256 not found; refusing unverified base image')
        h = hashlib.sha256()
        with temp.open('rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                h.update(block)
        if h.hexdigest().lower() != hashes[0].lower():
            raise MagbotError('Ubuntu image SHA256 mismatch')
        run(['qemu-img', 'info', '-f', 'qcow2', str(temp)])
        temp.chmod(0o644)
        os.replace(temp, base)
        print(f'Base image downloaded to {base}')
    finally:
        temp.unlink(missing_ok=True)


def main(argv=None):
    p = argparse.ArgumentParser(prog='magbot', description='MAG KVM sandbox lifecycle')
    sub = p.add_subparsers(dest='command', required=True)
    spawn = sub.add_parser('spawn', help='Create isolated VM + browser')
    spawn.add_argument('name')
    spawn.add_argument('--vcpus', type=int)
    spawn.add_argument('--ram', type=int, help='RAM GiB')
    spawn.add_argument('--disk', type=int, help='Disk GiB')
    sub.add_parser('image-update', help='Download base cloud image once')
    sub.add_parser('doctor', help='Verify virtualization and stack dependencies')
    inspect = sub.add_parser('inspect', help='Read-only registry/VM diagnostics')
    inspect.add_argument('name')
    status = sub.add_parser('status', help='VM + guest readiness')
    status.add_argument('name', nargs='?')
    despawn = sub.add_parser('despawn', help='Permanently remove VM and all its data')
    despawn.add_argument('name')
    execp = sub.add_parser('exec', help='Execute command as unprivileged bot user in VM')
    execp.add_argument('name')
    execp.add_argument('guest_args', nargs=argparse.REMAINDER)
    logs = sub.add_parser('logs', help='Cloud-init/bootstrap logs (when guest agent ready)')
    logs.add_argument('name')
    invoked = Path(sys.argv[0]).name
    if argv is None and invoked in ('spawnbot', 'despawnbot'):
        argv = [invoked.removesuffix('bot')] + sys.argv[1:]
    args = p.parse_args(argv)
    require_root()
    cfg = config()
    ROOT.mkdir(mode=0o711, parents=True, exist_ok=True)
    (ROOT / 'bots').mkdir(mode=0o711, parents=True, exist_ok=True)
    ROOT.chmod(0o711)
    (ROOT / 'bots').chmod(0o711)
    if args.command == 'doctor':
        preflight()
        print('/dev/kvm and KVM utilities found')
        print('Available:', {k: round(v/GIB, 1) for k,v in bytes_available().items()}, 'GiB')
        print('Registered bots:', len(definitions()))
    elif args.command == 'image-update':
        preflight()
        with locked():
            image_update(cfg)
    elif args.command == 'spawn':
        preflight()
        with locked():
            r = provision(valid_id(args.name), args.vcpus or cfg['default_vcpus'],
                          args.ram or cfg['default_ram_gib'], args.disk or cfg['default_disk_gib'], cfg)
        print(f"Created {r['domain']}; guest provisioning continues asynchronously.")
        print(f"Check: sudo magbot status {r['id']}")
    elif args.command == 'despawn':
        with locked():
            found = teardown(valid_id(args.name))
        print('Removed bot VM, disk, ISO, and metadata' if found else 'Bot not found (already absent)')
    elif args.command == 'inspect':
        return inspect_bot(args.name)
    elif args.command == 'status':
        print_status(args.name)
    elif args.command == 'exec':
        valid_id(args.name)
        if args.name not in [r['id'] for r in definitions()]:
            raise MagbotError('Unknown bot')
        cmd = args.guest_args
        if cmd and cmd[0] == '--':
            cmd = cmd[1:]
        if not cmd:
            raise MagbotError('Usage: magbot exec ID -- command [args]')
        if ready(args.name) != 'ready':
            raise MagbotError('Guest not ready; check magbot status')
        script = 'cd /home/bot && export PLAYWRIGHT_BROWSERS_PATH=/opt/magbot/browsers && ' + shlex.join(cmd)
        code, out, err = guest_exec(domain(args.name), ['/usr/sbin/runuser', '-u', 'bot', '--', '/bin/bash', '-lc', script], timeout=120)
        print(out, end='')
        print(err, file=sys.stderr, end='')
        return code
    elif args.command == 'logs':
        valid_id(args.name)
        if args.name not in [r['id'] for r in definitions()]:
            raise MagbotError('Unknown bot')
        code, out, err = guest_exec(domain(args.name), ['/usr/bin/tail', '-n', '80', '/var/log/magbot-bootstrap.log'], timeout=15)
        print(out or err, end='')
        return code
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (MagbotError, OSError, subprocess.TimeoutExpired, ValueError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
