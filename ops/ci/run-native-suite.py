#!/usr/bin/env python3
"""Run one unittest module with a hard per-test alarm, or emit parallel shards.

Each worker process loads the module and lets unittest call setUpClass once,
so a class shares one fixture server across its tests. Classes with at least
16 tests are split into contiguous slices across workers (one server per
shard, never one server per test). The per-test cap is 30s. The process cap on a shard is
higher because the shard runs many of those tests; that cap is printed as @N
for the parallel runner.
"""
import argparse
import importlib.util
import os
import signal
import sys
import time
import unittest

# Per-test cap. A longer entry needs a one-line reason in this dict.
LONGER = {
    # Two concurrently delayed 60-second fake GLM streams must finish, not time out.
    "GlmLayoutStartup.test_two_glm_turns_complete_after_60_seconds_without_cancel": 90,
}
DEFAULT_PER_TEST = 30


def _load(path):
    path = path.resolve()
    sys.path.insert(0, str(path.parent))
    name = path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _walk(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _walk(item)
        else:
            yield item


def _selector(test):
    # test.id() is module.Class.method
    return test.id().split('.', 1)[1]


def _shard_count(count, jobs):
    if count < 16:
        return 1
    # Keep at least four tests on a worker so process startup is not the shard.
    return max(1, min(jobs, count // 4))


def _outer_cap(count):
    # Process cap, not the per-test alarm. 8s of budget per test, floored so
    # startup fits, capped so one shard cannot run for many minutes.
    return min(120, max(45, 8 * count))


def emit_shards(path, jobs):
    module = _load(path)
    by_class = {}
    order = []
    for test in _walk(unittest.defaultTestLoader.loadTestsFromModule(module)):
        cls = type(test).__name__
        if cls not in by_class:
            order.append(cls)
            by_class[cls] = []
        by_class[cls].append(_selector(test))
    rel = os.path.relpath(path)
    rows = []
    for cls in order:
        names = by_class[cls]
        pieces = _shard_count(len(names), jobs)
        # Contiguous slices keep a test next to the earlier test it builds on.
        # A fresh server starts each slice, so a test must not need state from
        # a test that landed in an earlier slice.
        size = (len(names) + pieces - 1) // pieces
        shards = [names[i:i + size] for i in range(0, len(names), size)]
        for n, shard in enumerate(shards):
            if not shard:
                continue
            label = f'native {cls}' if pieces == 1 else f'native {cls}#{n}'
            args = ' '.join(f'--test {name}' for name in shard)
            # Long integration tests need an enclosing shard cap above their
            # explicit per-test deadline plus fixture startup/teardown overhead.
            cap = max(_outer_cap(len(shard)),
                      max([LONGER.get(name, DEFAULT_PER_TEST) for name in shard]) + 30)
            rows.append((len(shard), label, cap, rel, args))
    # Long shards first so the cores spend the wall clock on them.
    rows.sort(key=lambda row: (-row[0], row[1]))
    for _count, label, cap, rel, args in rows:
            print(
            f'@{cap} {label} :: python3 ops/ci/run-native-suite.py'
            f' --file {rel} --per-test {DEFAULT_PER_TEST} {args}',
            flush=True,
        )


class _Alarm(BaseException):
    pass


def _make_result(per_test, stream):
    class TimedResult(unittest.TextTestResult):
        def startTest(self, test):
            super().startTest(test)
            self._t0 = time.perf_counter()
            limit = LONGER.get(test.id().split('.', 1)[1], per_test)

            def _on_alarm(signum, frame):
                raise _Alarm(f'per-test timeout after {limit}s')

            signal.signal(signal.SIGALRM, _on_alarm)
            signal.alarm(limit)

        def stopTest(self, test):
            signal.alarm(0)
            dt = time.perf_counter() - getattr(self, '_t0', time.perf_counter())
            print(f'TEST {dt:.2f}s {test.id()}', file=stream, flush=True)
            super().stopTest(test)

        def addError(self, test, err):
            if err[0] is _Alarm:
                err = (AssertionError, AssertionError(str(err[1])), err[2])
            super().addError(test, err)

    return TimedResult


def run_tests(path, selected, per_test):
    os.environ.setdefault('PYTHONUNBUFFERED', '1')
    module = _load(path)
    loader = unittest.defaultTestLoader
    if selected:
        suite = loader.loadTestsFromNames(selected, module)
    else:
        suite = loader.loadTestsFromModule(module)
    # mag_deadline.arm() only raises its bound. A per-test attribute still
    # shortens that module's 180s/300s default to the gate cap.
    for test in _walk(suite):
        test.mag_timeout_s = LONGER.get(_selector(test), per_test)
    result_class = _make_result(per_test, sys.stdout)
    runner = unittest.TextTestRunner(
        verbosity=0, failfast=True, resultclass=result_class, stream=sys.stderr
    )
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', required=True, type=os.path.abspath)
    parser.add_argument('--emit-shards', action='store_true')
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--test', action='append', default=[])
    parser.add_argument('--per-test', type=int, default=DEFAULT_PER_TEST)
    args = parser.parse_args(argv)
    path = __import__('pathlib').Path(args.file)
    if not path.is_file():
        print(f'missing test file {path}', file=sys.stderr)
        return 2
    if args.emit_shards:
        if args.jobs < 1:
            print('bad --jobs', file=sys.stderr)
            return 2
        emit_shards(path, args.jobs)
        return 0
    if args.per_test < 1:
        print('bad --per-test', file=sys.stderr)
        return 2
    return run_tests(path, args.test, args.per_test)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
