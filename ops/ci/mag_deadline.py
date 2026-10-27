"""Per-test deadline for unittest modules.

Call arm(seconds) once at import. Each test, setUpClass and tearDownClass
is interrupted when it exceeds that bound. Subprocesses start in their own
session and are killed on timeout, SIGTERM, or process exit.
"""
import atexit
import os
import signal
import subprocess
import threading
import unittest

_ARMED = False
_DEFAULT = 180
_LIMIT = 180
_CHILDREN = []
_LOCK = threading.Lock()
_ORIG_POPEN = subprocess.Popen
_ORIG_RUN = unittest.TestCase.run
_ORIG_CLASS_SETUP = unittest.suite.TestSuite._handleClassSetUp
_ORIG_CLASS_TEARDOWN = unittest.suite.TestSuite._tearDownPreviousClass


def _descendants(pid):
    kids = []
    try:
        with open(f'/proc/{pid}/task/{pid}/children', encoding='ascii') as handle:
            raw = handle.read().split()
    except OSError:
        return kids
    for item in raw:
        try:
            child = int(item)
        except ValueError:
            continue
        kids.append(child)
        kids.extend(_descendants(child))
    return kids


def _kill_pid(pid):
    for child in _descendants(pid):
        try:
            os.kill(child, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _worktree_magroom():
    env = os.environ.get('MAG_CI_MAGROOM')
    if env:
        return os.path.realpath(env)
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.realpath(os.path.join(here, 'build', 'magroom'))


def _kill_worktree_magroom():
    """Kill this checkout's magroom only. Production binaries stay running."""
    magroom = _worktree_magroom()
    if not magroom or not os.path.exists(magroom):
        return
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            exe = os.path.realpath('/proc/%s/exe' % entry)
        except OSError:
            continue
        if exe == magroom:
            try:
                os.kill(int(entry), signal.SIGKILL)
            except OSError:
                pass


def _kill_tracked():
    with _LOCK:
        procs = list(_CHILDREN)
    for proc in procs:
        if proc.poll() is None:
            _kill_pid(proc.pid)
    _kill_worktree_magroom()


def _track_popen(*args, **kwargs):
    kwargs.setdefault('start_new_session', True)
    proc = _ORIG_POPEN(*args, **kwargs)
    with _LOCK:
        _CHILDREN.append(proc)
    return proc


def _on_signal(signum, _frame):
    _kill_tracked()
    if signum == signal.SIGALRM:
        raise TimeoutError(f'mag-test-timeout: exceeded {_LIMIT}s')
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _bounded(seconds, fn, *args, **kwargs):
    global _LIMIT
    _LIMIT = seconds
    signal.signal(signal.SIGALRM, _on_signal)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(*args, **kwargs)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _run(self, result=None):
    seconds = getattr(self, 'mag_timeout_s', None) or _DEFAULT
    return _bounded(seconds, _ORIG_RUN, self, result)


def _class_setup(self, *args, **kwargs):
    return _bounded(_DEFAULT, _ORIG_CLASS_SETUP, self, *args, **kwargs)


def _class_teardown(self, *args, **kwargs):
    return _bounded(_DEFAULT, _ORIG_CLASS_TEARDOWN, self, *args, **kwargs)


def arm(seconds=180):
    """Install the per-test deadline. Safe to call more than once.

    The first call also arms a process deadline, so a script that never
    enters unittest still cannot wait forever. A later call can only raise
    that bound. Each test replaces the timer with its own limit and does
    not lower a longer one already installed by the importing module.
    """
    global _ARMED, _DEFAULT, _LIMIT
    seconds = int(seconds)
    if seconds < 1:
        raise ValueError('mag-test-timeout must be at least 1s')
    if _ARMED and seconds <= _DEFAULT:
        return
    _DEFAULT = seconds
    _LIMIT = seconds
    if not _ARMED:
        subprocess.Popen = _track_popen
        unittest.TestCase.run = _run
        unittest.suite.TestSuite._handleClassSetUp = _class_setup
        unittest.suite.TestSuite._tearDownPreviousClass = _class_teardown
        signal.signal(signal.SIGTERM, _on_signal)
        atexit.register(_kill_tracked)
        _ARMED = True
    signal.signal(signal.SIGALRM, _on_signal)
    signal.setitimer(signal.ITIMER_REAL, seconds)
