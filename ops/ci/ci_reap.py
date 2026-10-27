#!/usr/bin/env python3
"""Keep local CI from leaving servers behind.

The runner stays in the process group it was started in, so an outer timeout,
GitHub Actions, or Ctrl-C can still signal it. Each command runs in its own
session. On exit, timeout, or abort every descendant is killed, including
processes that called setsid and would otherwise be reparented to init and
keep a 127.0.0.1 port.

  ci_reap.py --self bash script args...   become a subreaper, then exec
  ci_reap.py --supervise command args...  run one command and reap its tree
  ci_reap.py --kill ROOT [KEEP...]        kill ROOT's descendants, spare KEEP
"""
import os
import signal
import subprocess
import sys
import time

PR_SET_CHILD_SUBREAPER = 36


def prctl_subreaper():
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        sys.stderr.write('ci reaper: prctl(PR_SET_CHILD_SUBREAPER) failed (%d)\n' % err)
        raise SystemExit(1)


def _ppid_map():
    mapping = {}
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open('/proc/%d/status' % pid, encoding='ascii', errors='replace') as handle:
                for line in handle:
                    if line.startswith('PPid:'):
                        mapping[pid] = int(line.split()[1])
                        break
        except OSError:
            continue
    return mapping


def _spared(mapping, root, keep):
    spare = set(keep)
    spare.add(os.getpid())
    for pid in mapping:
        walk = pid
        for _ in range(64):
            if walk in keep:
                spare.add(pid)
                break
            if walk == root or walk <= 1:
                break
            parent = mapping.get(walk)
            if not parent:
                break
            walk = parent
    return spare


def _descendants(mapping, root):
    children = {}
    for pid, parent in mapping.items():
        children.setdefault(parent, []).append(pid)
    found = []
    stack = list(children.get(root, []))
    seen = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


def _signal(pids, root, sig):
    try:
        root_pgid = os.getpgid(root)
    except OSError:
        root_pgid = None
    groups = set()
    for pid in pids:
        try:
            pgid = os.getpgid(pid)
        except OSError:
            continue
        if pgid > 1 and pgid != root_pgid and pgid not in groups:
            groups.add(pgid)
            try:
                os.killpg(pgid, sig)
            except OSError:
                pass
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def reap_foreign(root, keep):
    """Kill descendants of root. keep and their descendants stay running."""
    for sig, pause in ((signal.SIGTERM, 0.2), (signal.SIGKILL, 0.1), (signal.SIGKILL, 0)):
        mapping = _ppid_map()
        spare = _spared(mapping, root, set(keep))
        victims = [pid for pid in _descendants(mapping, root) if pid not in spare]
        if not victims:
            return
        _signal(victims, root, sig)
        if pause:
            time.sleep(pause)


def supervise(cmd):
    prctl_subreaper()
    proc = subprocess.Popen(cmd, start_new_session=True)
    caught = {'sig': 0}

    def handle(signum, _frame):
        caught['sig'] = signum
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            try:
                proc.terminate()
            except OSError:
                pass

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGHUP, handle)
    rc = proc.wait()
    reap_foreign(os.getpid(), [])
    if caught['sig']:
        return 128 + caught['sig']
    if rc < 0:
        return 128 + (-rc)
    return rc


def main(argv):
    if len(argv) < 2:
        return 64
    mode = argv[1]
    if mode == '--self':
        if len(argv) < 3:
            return 64
        prctl_subreaper()
        os.execvp(argv[2], argv[2:])
    if mode == '--supervise':
        if len(argv) < 3:
            return 64
        return supervise(argv[2:])
    if mode == '--kill':
        if len(argv) < 3:
            return 64
        root = int(argv[2])
        keep = [int(item) for item in argv[3:]]
        reap_foreign(root, keep)
        return 0
    sys.stderr.write('usage: ci_reap.py --self|--supervise|--kill ...\n')
    return 64


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
