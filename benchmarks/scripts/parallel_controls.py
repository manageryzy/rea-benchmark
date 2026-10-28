"""Run controls in independently prepared workspaces; never override their cache."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from grading.metadata_admission import metadata_validation_cache


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def relative(value):
    if (not isinstance(value, str) or not value or '\\' in value
            or value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Invalid workspace-relative control path')
    return value


def fingerprints(root, names):
    root = Path(root).resolve()
    result = {}
    for name in names:
        path = root / relative(name)
        if (any(part.is_symlink() for part in (path, *path.parents) if part != root and part.is_relative_to(root))
                or not path.resolve().is_relative_to(root)):
            raise ValueError('Control source escapes its workspace')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(canonical(value))
        temporary = Path(stream.name)
    temporary.replace(path)


def forbidden(*args, **kwargs):
    raise AssertionError('Network/model access forbidden during offline controls')




class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = {}
        self.started = {}

    def startTest(self, test):
        name = test._testMethodName
        if name in self.rows:
            raise RuntimeError('A control executed more than once')
        self.rows[name] = {'name': name, 'status': 'running'}
        self.started[name] = time.monotonic()
        super().startTest(test)

    def addSuccess(self, test):
        self.rows[test._testMethodName]['status'] = 'passed'
        super().addSuccess(test)

    def addFailure(self, test, error):
        self.rows[test._testMethodName]['status'] = 'failed'
        super().addFailure(test, error)

    def addError(self, test, error):
        if hasattr(test, '_testMethodName'):
            self.rows[test._testMethodName]['status'] = 'error'
        super().addError(test, error)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.rows[test._testMethodName]['status'] = 'failed'
        super().addSubTest(test, subtest, error)

    def addSkip(self, test, reason):
        self.rows[test._testMethodName]['status'] = 'skipped'
        super().addSkip(test, reason)

    def stopTest(self, test):
        name = test._testMethodName
        self.rows[name]['seconds'] = time.monotonic() - self.started[name]
        super().stopTest(test)


def worker(plan_path, output):
    plan = json.loads(Path(plan_path).read_bytes())
    root = Path(plan['root']).resolve()
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != plan['runner_sha256']:
        raise ValueError('Parallel runner changed before controls')
    if fingerprints(root, plan['sources']) != plan['sources']:
        raise ValueError('Worker source closure differs before controls')
    os.chdir(root)
    sys.path.insert(0, str(root / 'benchmarks/scripts'))
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=forbidden))
        module = importlib.import_module(plan['module'])
        if hasattr(module, 'run') and hasattr(module.run, 'launch_model'):
            stack.enter_context(patch.object(module.run, 'launch_model', side_effect=forbidden))
        if hasattr(module, 'evidence'):
            stack.enter_context(module.evidence.validation_controls())
        if module.__name__ == 'check_capability_coordinator':
            stack.enter_context(metadata_validation_cache())
        test_class = getattr(module, plan['class'])
        suite = unittest.TestSuite(test_class(name) for name in plan['tests'])
        result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    if fingerprints(root, plan['sources']) != plan['sources']:
        raise ValueError('Worker source closure changed during controls')
    rows = list(result.rows.values())
    passed = (result.wasSuccessful() and not result.skipped and not result.expectedFailures
              and len(rows) == len(plan['tests']) and all(row['status'] == 'passed' for row in rows))
    write_json(output, {'schema_version': 1, 'sources': plan['sources'], 'tests': rows,
        'runner_sha256': plan['runner_sha256'], 'worker_pid': os.getpid(),
        'checks': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'passed': passed, 'provider_launches': 0, 'model_api_calls': 0})
    return 0 if passed else 1


def collect(results, expected, sources, runner_sha256):
    seen = []
    worker_pids = []
    rows = []
    failures = errors = 0
    for value in results:
        if (value.get('schema_version') != 1 or value.get('sources') != sources
                or value.get('runner_sha256') != runner_sha256
                or type(value.get('worker_pid')) is not int or value['worker_pid'] <= 0
                or value.get('provider_launches') != 0 or value.get('model_api_calls') != 0
                or value.get('skipped') != 0 or type(value.get('checks')) is not int
                or value['checks'] != len(value['tests']) or type(value.get('passed')) is not bool):
            raise ValueError('Incomplete or inconsistent parallel control receipt')
        worker_pids.append(value['worker_pid'])
        for row in value['tests']:
            if row['status'] not in ('passed', 'failed', 'error'):
                raise ValueError('A control did not finish')
            seen.append(row['name'])
            rows.append(row)
        if value['passed'] != all(row['status'] == 'passed' for row in value['tests']):
            raise ValueError('Worker pass flag differs from its controls')
        for name in ('failures', 'errors'):
            if type(value.get(name)) is not int or value[name] < 0:
                raise ValueError('Invalid control failure count')
        failures += value['failures']
        errors += value['errors']
    if len(worker_pids) != len(set(worker_pids)):
        raise ValueError('Parallel workers did not use independent processes')
    if len(seen) != len(set(seen)) or set(seen) != set(expected) or len(seen) != len(expected):
        raise ValueError('Parallel controls are missing, duplicated or unexpected')
    passed = all(row['status'] == 'passed' for row in rows)
    if passed != (failures == errors == 0):
        raise ValueError('Aggregate failure counts differ from control outcomes')
    return {'schema_version': 1, 'sources': sources, 'tests': sorted(rows, key=lambda row: row['name']),
        'runner_sha256': runner_sha256, 'worker_pids': [value['worker_pid'] for value in results],
        'checks': len(rows), 'failures': failures, 'errors': errors, 'passed': passed,
        'provider_launches': 0, 'model_api_calls': 0}


def run_prepared(workspaces, tests, source_names, output, *, module, test_class, timeout=7200):
    """The caller must supply distinct, complete workspaces with private mutable files."""
    roots = [Path(root).resolve() for root in workspaces]
    if (not roots or len(roots) > 16 or len(set(roots)) != len(roots)
            or any(a != b and a.is_relative_to(b) for a in roots for b in roots)):
        raise ValueError('Parallel workers require distinct nonnested workspaces')
    if not tests or len(set(tests)) != len(tests) or not all(re.fullmatch(r'test_[a-zA-Z0-9_]+', n) for n in tests):
        raise ValueError('Invalid parallel control selection')
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Invalid control timeout')
    runner_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    sources = fingerprints(roots[0], source_names)
    if not sources or any(fingerprints(root, source_names) != sources for root in roots):
        raise ValueError('Parallel workers have different source closures')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    groups = [tests[i::len(roots)] for i in range(len(roots))]

    def execute(index):
        assigned = groups[index]
        if not assigned:
            return None
        directory = output / ('worker-' + str(index))
        directory.mkdir()  # Never consume an older result as a new run.
        plan = {'root': str(roots[index]), 'module': module, 'class': test_class,
                'tests': assigned, 'sources': sources, 'runner_sha256': runner_sha256}
        write_json(directory / 'plan.json', plan)
        environment = {key: value for key, value in os.environ.items()
                       if not any(word in key.upper() for word in ('TOKEN', 'SECRET', 'API_KEY'))}
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        environment.pop('REA_BENCHMARK_OFFLINE', None)
        script = Path(__file__).resolve()
        with (directory / 'output.log').open('wb') as log:
            child = subprocess.run([sys.executable, str(script), 'worker',
                '--plan', str((directory / 'plan.json').resolve()),
                '--output', str((directory / 'result.json').resolve())],
                cwd=roots[index], env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        path = directory / 'result.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError('Parallel worker did not produce a bounded result')
        value = json.loads(path.read_bytes())
        if {row['name'] for row in value['tests']} != set(assigned):
            raise ValueError('Parallel worker changed its assigned controls')
        if child.returncode != (0 if value.get('passed') is True else 1):
            raise ValueError('Parallel worker exit status differs from its result')
        return value

    with ThreadPoolExecutor(max_workers=len(roots)) as pool:
        results = list(pool.map(execute, range(len(roots))))
    if any(fingerprints(root, source_names) != sources for root in roots):
        raise ValueError('Parallel source closure changed before aggregation')
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != runner_sha256:
        raise ValueError('Parallel runner changed during controls')
    aggregate = collect([result for result in results if result is not None], tests, sources, runner_sha256)
    write_json(output / 'result.json', aggregate)
    return aggregate


def run_isolated(root, tests, source_names, output, *, module, test_class, jobs, timeout=7200):
    """Snapshot once, then give every process independent mutable files and caches."""
    from control_workspace import snapshot
    import shutil
    if type(jobs) is not int or not 1 <= jobs <= 16:
        raise ValueError('Control worker count must be between 1 and 16')
    required = {'benchmarks/scripts/parallel_controls.py', 'benchmarks/scripts/control_workspace.py'}
    if not required.issubset(source_names):
        raise ValueError('The control source closure must include runner and workspace setup')
    expected = fingerprints(root, source_names)
    with tempfile.TemporaryDirectory(prefix='rea-control-workers-') as temporary:
        temporary = Path(temporary)
        seed = temporary / 'seed'
        snapshot(root, seed)
        if fingerprints(seed, source_names) != expected:
            raise ValueError('Control source closure changed while preparing its snapshot')
        roots = [seed]
        for index in range(1, min(jobs, len(tests))):
            worker_root = temporary / ('workspace-' + str(index))
            shutil.copytree(seed, worker_root, symlinks=True,
                            ignore=shutil.ignore_patterns('__pycache__'))
            roots.append(worker_root)
        result = run_prepared(roots, tests, source_names, output,
                              module=module, test_class=test_class, timeout=timeout)
        if fingerprints(root, source_names) != expected:
            raise ValueError('Repository control sources changed during isolated acceptance')
        return result


class AggregateResult:
    """Only validated worker aggregates can be adapted into an acceptance receipt."""
    def __init__(self, value, directory):
        expected = [row['name'] for row in value['tests']]
        # Recheck structural consistency rather than trusting an aggregate pass flag.
        worker = {**value, 'worker_pid': 1, 'skipped': 0}
        checked = collect([worker], expected, value['sources'], value['runner_sha256'])
        self.testsRun = checked['checks']
        self.failures = [None] * checked['failures']
        self.errors = [None] * checked['errors']
        self.skipped = []
        self.expectedFailures = []
        self._passed = checked['passed']
        self.execution = {'mode': 'isolated_processes', 'worker_count': len(value['worker_pids']),
            'test_ids': expected, 'runner_sha256': value['runner_sha256'],
            'results_directory': str(directory)}

    def wasSuccessful(self):
        return self._passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['worker'])
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(worker(args.plan, args.output))
