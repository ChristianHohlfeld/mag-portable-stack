#!/usr/bin/env python3
"""Fail when a test or a workflow job/step has no deadline.

    python3 ops/ci/check-timeouts.py          # CI guard
    python3 ops/ci/check-timeouts.py --stamp  # insert missing markers

Python tests call ops/ci/mag_deadline.py. Shell tests re-exec under
timeout(1). Native binaries call mag_test_deadline. Node contracts arm
mag_deadline.cjs. The Go UDP test refuses a missing or long -timeout.
Every job and step in .github/workflows and .gitea/workflows carries
timeout-minutes.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PY_SECONDS = {
    'mag-room/tests/test_speech_real.py': 600,
    'mag-room/tests/test_room_e2e.py': 240,
    'mag-room/tests/test_hang_invariants.py': 90,
    'ops/deepseek/preflight-tests.py': 3900,
    'mag-room/tests/test_native.py': 300,
}
SH_SECONDS = {
    'ops/tests/e2e-bootstrap.sh': 5400,
    'ops/pages/tests/test-pages-lifecycle.sh': 600,
    'ops/pages/tests/test-pages-sandbox.sh': 600,
    'ops/tests/test-hang-deadlines.sh': 60,
    'ops/ci/tests/test-affected.sh': 60,
    'ops/ci/tests/test-ci-reap.sh': 60,
}
JOB_MINUTES = {
    'native': 90,
    'room': 45,
    'speech': 60,
    'gitleaks': 15,
    'pages': 40,
    'secrets': 15,
    'nginx': 30,
    'recovery': 45,
    'chromium': 30,
    'e2e-bootstrap': 120,
    'promote-green': 120,
    'deploy-live': 5,
    'publish': 5,
    'checks': 1440,
}

_SKIP = ('snapshot-3.8', 'perf-results', '/build/')


def _rel(path):
    return path.relative_to(ROOT).as_posix()


def _skipped(rel):
    return any(part in rel for part in _SKIP) or rel.startswith('build/')


def python_tests():
    found = []
    for path in ROOT.rglob('*.py'):
        rel = _rel(path)
        if _skipped(rel) or '/.git/' in f'/{rel}':
            continue
        name = path.name
        if name in ('test_mag_deterministic_engine.py', 'preflight-tests.py') or name.startswith('test_') or name.startswith('test-') or name.endswith('_test.py'):
            found.append(path)
    return found


def shell_tests():
    found = []
    for path in ROOT.rglob('*.sh'):
        rel = _rel(path)
        if _skipped(rel):
            continue
        if rel.startswith('ops/tests/') or '/tests/' in rel or rel == 'ops/trust/test-trust.sh':
            found.append(path)
    return found


def cpp_tests():
    return sorted((ROOT / 'mag-room/tests').glob('test_*.cpp'))


def node_tests():
    return sorted((ROOT / 'mag-room/tests').glob('test_*.cjs'))


def go_tests():
    return sorted((ROOT / 'mag-room/webrtc').glob('*_test.go'))


def workflows():
    found = []
    for base in (ROOT / '.github/workflows', ROOT / '.gitea/workflows'):
        if base.is_dir():
            found.extend(sorted(base.glob('*.yml')))
            found.extend(sorted(base.glob('*.yaml')))
    return found


def _py_seconds(rel):
    return PY_SECONDS.get(rel, 180)


def _sh_seconds(rel):
    return SH_SECONDS.get(rel, 180)


def _py_snippet(seconds):
    return (
        f'# mag-test-timeout: {seconds}\n'
        'import pathlib as _mag_path\n'
        'import sys as _mag_sys\n'
        '_mag_root = _mag_path.Path(__file__).resolve()\n'
        'while _mag_root != _mag_root.parent and not (_mag_root / \'ops/ci/mag_deadline.py\').is_file():\n'
        '    _mag_root = _mag_root.parent\n'
        '_mag_sys.path.insert(0, str(_mag_root / \'ops/ci\'))\n'
        'import mag_deadline as _mag_deadline\n'
        f'_mag_deadline.arm({seconds})\n'
    )


def stamp_python(path):
    text = path.read_text(encoding='utf-8')
    if 'mag_deadline.arm(' in text and 'mag-test-timeout:' in text:
        return False
    seconds = _py_seconds(_rel(path))
    lines = text.splitlines(keepends=True)
    i = 0
    if i < len(lines) and lines[i].startswith('#!'):
        i += 1
    if i < len(lines) and re.match(r'#.*coding[:=]', lines[i]):
        i += 1
    while i < len(lines) and lines[i].startswith('from __future__'):
        i += 1
    if i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            if stripped.count(quote) >= 2:
                i += 1
            else:
                i += 1
                while i < len(lines) and quote not in lines[i]:
                    i += 1
                if i < len(lines):
                    i += 1
    path.write_text(''.join(lines[:i]) + _py_snippet(seconds) + ''.join(lines[i:]), encoding='utf-8')
    return True


def _sh_block(seconds):
    return (
        f'# mag-test-timeout: {seconds}\n'
        'if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then\n'
        '  export MAG_TEST_DEADLINE_ARMED=1\n'
        f'  exec timeout --kill-after=5s {seconds}s bash "$0" "$@"\n'
        'fi\n'
    )


def stamp_shell(path):
    text = path.read_text(encoding='utf-8')
    if 'MAG_TEST_DEADLINE_ARMED' in text and 'mag-test-timeout:' in text:
        return False
    seconds = _sh_seconds(_rel(path))
    lines = text.splitlines(keepends=True)
    block = _sh_block(seconds)
    if lines and lines[0].startswith('#!'):
        path.write_text(lines[0] + block + ''.join(lines[1:]), encoding='utf-8')
    else:
        path.write_text(block + text, encoding='utf-8')
    return True


def stamp_cpp(path):
    text = path.read_text(encoding='utf-8')
    if 'mag_test_deadline(' in text and 'mag-test-timeout:' in text:
        return False
    if 'mag_deadline.hpp' not in text:
        text = '#include "mag_deadline.hpp"\n' + text
    new, n = re.subn(
        r'(int\s+main\s*\([^)]*\)\s*\{)',
        r'\1\n  mag_test_deadline(60); // mag-test-timeout: 60',
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f'{path}: no int main() to arm')
    path.write_text(new, encoding='utf-8')
    return True


def stamp_node(path):
    text = path.read_text(encoding='utf-8')
    if 'mag_deadline.cjs' in text and 'mag-test-timeout:' in text:
        return False
    path.write_text('// mag-test-timeout: 60\nrequire(\'./mag_deadline.cjs\').arm(60);\n' + text, encoding='utf-8')
    return True


def stamp_go(path):
    text = path.read_text(encoding='utf-8')
    if 'func TestMain' in text and 'mag-test-timeout:' in text:
        return False
    text = text.replace(
        'import (\n "bytes"\n "testing"\n)',
        'import (\n "bytes"\n "flag"\n "fmt"\n "os"\n "testing"\n "time"\n)',
        1,
    )
    text += (
        '\n// mag-test-timeout: 60\n'
        'func TestMain(m *testing.M) {\n'
        '\tflag.Parse()\n'
        '\traw := flag.Lookup("test.timeout").Value.String()\n'
        '\td, err := time.ParseDuration(raw)\n'
        '\tif err != nil || d <= 0 || d > 2*time.Minute {\n'
        '\t\tfmt.Fprintf(os.Stderr, "go test -timeout must be set and at most 2m, got %q\\n", raw)\n'
        '\t\tos.Exit(1)\n'
        '\t}\n'
        '\tos.Exit(m.Run())\n'
        '}\n'
    )
    path.write_text(text, encoding='utf-8')
    return True


def _indent(line):
    return len(line) - len(line.lstrip(' '))


def _step_minutes(body):
    low = body.lower()
    if 'suite.sh' in low:
        return 120
    if 'e2e-bootstrap.sh' in low or 'blank ubuntu' in low:
        return 100
    if 'gitleaks' in low or 'actions/checkout' in low or 'actions/setup-' in low or 'upload-artifact' in low:
        return 10
    if 'apt-get' in low or 'cmake' in low or 'playwright install' in low or 'vendor-' in low:
        return 20
    if 'playwright' in low or 'native integration' in low or 'test_native.py' in low or 'test_room' in low or 'test_speech' in low:
        return 40
    return 15


def parse_workflow(text):
    """Return (jobs, steps) where each item is a dict."""
    lines = text.splitlines()
    jobs, steps = [], []
    in_jobs = False
    block_at = None
    job = None
    step = None
    item_indent = None

    def close_step(end):
        nonlocal step
        if step is not None:
            step['end'] = end
            step['body'] = '\n'.join(lines[step['header']:end])
            steps.append(step)
            step = None

    def close_job(end):
        nonlocal job
        close_step(end)
        if job is not None:
            job['end'] = end
            jobs.append(job)
            job = None

    for i, raw in enumerate(lines):
        line = raw.split('\t', 1)[0] if False else raw
        if block_at is not None:
            if line.strip() and not line.lstrip().startswith('#') and _indent(line) <= block_at:
                block_at = None
            else:
                continue
        if re.match(r'^jobs:\s*(#.*)?$', line):
            in_jobs = True
            continue
        if in_jobs and line.strip() and not line.lstrip().startswith('#') and _indent(line) == 0:
            close_job(i)
            in_jobs = False
        if not in_jobs:
            continue
        job_m = re.match(r'^  ([A-Za-z0-9_-]+):\s*(#.*)?$', line)
        if job_m:
            close_job(i)
            job = {'name': job_m.group(1), 'header': i, 'has': False, 'end': len(lines)}
            item_indent = None
            continue
        if job and re.match(r'^    timeout-minutes:\s*[1-9][0-9]*\s*(#.*)?$', line):
            job['has'] = True
        if job and re.match(r'^    steps:\s*(#.*)?$', line):
            item_indent = 6
            continue
        if item_indent is not None and re.match(r'^ {' + str(item_indent) + r'}- \S', line):
            close_step(i)
            step = {'header': i, 'has': False, 'item_indent': item_indent, 'end': len(lines), 'job': job['name'] if job else ''}
            continue
        if step and re.match(r'^ {' + str(step['item_indent'] + 2) + r'}timeout-minutes:\s*[1-9][0-9]*\s*(#.*)?$', line):
            step['has'] = True
        if re.match(r'^\s+\S[^#\n]*:\s*[|>][-+]?\s*(#.*)?$', line):
            block_at = _indent(line)
    close_job(len(lines))
    return jobs, steps


def workflow_gaps(text, name):
    jobs, steps = parse_workflow(text)
    gaps = []
    if 'jobs:' in text and not jobs:
        gaps.append(f'{name}: jobs: present but no job was recognized')
    for job in jobs:
        if not job['has']:
            gaps.append(f'{name}:{job["header"] + 1}: job {job["name"]} has no timeout-minutes')
    if 'steps:' in text and not steps:
        gaps.append(f'{name}: steps: present but no step was recognized')
    for step in steps:
        if not step['has']:
            gaps.append(f'{name}:{step["header"] + 1}: step under {step["job"]} has no timeout-minutes')
    return gaps


def stamp_workflow(path):
    text = path.read_text(encoding='utf-8')
    jobs, steps = parse_workflow(text)
    inserts = []
    for job in jobs:
        if not job['has']:
            minutes = JOB_MINUTES.get(job['name'], 45)
            inserts.append((job['header'], f'    timeout-minutes: {minutes}\n'))
    lines = text.splitlines(keepends=True)
    for step in steps:
        if step['has']:
            continue
        body = ''.join(lines[step['header']:step['end']])
        minutes = _step_minutes(body)
        pad = ' ' * (step['item_indent'] + 2)
        inserts.append((step['header'], f'{pad}timeout-minutes: {minutes}\n'))
    if not inserts:
        return False
    for index, extra in sorted(inserts, key=lambda item: item[0], reverse=True):
        lines.insert(index + 1, extra)
    path.write_text(''.join(lines), encoding='utf-8')
    return True


def _self_check():
    missing = workflow_gaps(
        'name: sample\njobs:\n  native:\n    runs-on: ubuntu-24.04\n    steps:\n      - name: no timeout\n        run: true\n',
        'sample.yml',
    )
    if len(missing) != 2:
        raise SystemExit(f'deadline guard self-check failed: {missing}')
    ok = (
        'name: sample\njobs:\n  native:\n    timeout-minutes: 5\n    runs-on: ubuntu-24.04\n'
        '    steps:\n      - name: has\n        timeout-minutes: 1\n        run: true\n'
    )
    extra = workflow_gaps(ok, 'ok.yml')
    if extra:
        raise SystemExit(f'deadline guard self-check rejected a complete workflow: {extra}')
    blocked = workflow_gaps(
        'name: sample\njobs:\n  native:\n    timeout-minutes: 5\n    steps:\n'
        '      - name: script\n        timeout-minutes: 2\n        run: |\n          - name: not a step\n',
        'block.yml',
    )
    if blocked:
        raise SystemExit(f'deadline guard treated a script line as a step: {blocked}')


def scan():
    problems = []
    py = python_tests()
    sh = shell_tests()
    cpp = cpp_tests()
    node = node_tests()
    go = go_tests()
    yml = workflows()
    if len(py) < 10 or len(sh) < 10 or len(cpp) < 5 or len(node) < 1 or len(go) < 1 or len(yml) < 5:
        problems.append(
            f'deadline scan is too narrow: py={len(py)} sh={len(sh)} cpp={len(cpp)} node={len(node)} go={len(go)} yml={len(yml)}'
        )
    for path in py:
        text = path.read_text(encoding='utf-8')
        rel = _rel(path)
        if 'mag-test-timeout:' not in text or 'mag_deadline.arm(' not in text:
            problems.append(f'{rel}: python test has no mag-test-timeout / mag_deadline.arm')
        else:
            want = _py_seconds(rel)
            if f'mag_deadline.arm({want})' not in text:
                problems.append(f'{rel}: expected mag_deadline.arm({want})')
    for path in sh:
        text = path.read_text(encoding='utf-8')
        rel = _rel(path)
        want = _sh_seconds(rel)
        if f'# mag-test-timeout: {want}' not in text or 'MAG_TEST_DEADLINE_ARMED' not in text:
            problems.append(f'{rel}: shell test has no mag-test-timeout re-exec')
        elif f'exec timeout --kill-after=5s {want}s bash' not in text:
            problems.append(f'{rel}: shell deadline does not match {want}s')
    for path in cpp:
        text = path.read_text(encoding='utf-8')
        if 'mag-test-timeout:' not in text or 'mag_test_deadline(' not in text:
            problems.append(f'{_rel(path)}: native test has no mag_test_deadline')
    for path in node:
        text = path.read_text(encoding='utf-8')
        if 'mag-test-timeout:' not in text or "mag_deadline.cjs').arm(" not in text:
            problems.append(f'{_rel(path)}: node contract has no mag_deadline.cjs arm')
    for path in go:
        text = path.read_text(encoding='utf-8')
        if 'mag-test-timeout:' not in text or 'func TestMain' not in text or 'test.timeout' not in text:
            problems.append(f'{_rel(path)}: go test has no TestMain -timeout guard')
    for path in yml:
        problems.extend(workflow_gaps(path.read_text(encoding='utf-8'), _rel(path)))
    return problems


def stamp_all():
    changed = 0
    for path in python_tests():
        changed += stamp_python(path)
    for path in shell_tests():
        changed += stamp_shell(path)
    for path in cpp_tests():
        changed += stamp_cpp(path)
    for path in node_tests():
        changed += stamp_node(path)
    for path in go_tests():
        changed += stamp_go(path)
    for path in workflows():
        changed += stamp_workflow(path)
    return changed


def main():
    _self_check()
    if '--stamp' in sys.argv:
        print(f'stamped {stamp_all()} files')
    problems = scan()
    if problems:
        sys.stderr.write('\n'.join(problems) + '\n')
        return 1
    print('deadlines ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
