"""Separate source/runtime gate for the original-language C++ buffer pairing."""
from contextlib import contextmanager
from contextvars import ContextVar
import json

import action_cache
from grading.c_executor import runtime as executor
from . import runtime

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/cpp-buffer-validation.json'
_controls = ContextVar('cpp_buffer_controls', default=False)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def fingerprints():
    names = set(runtime.sources()) | {str(runtime.POLICY.relative_to(ROOT)),
        'benchmarks/scripts/capabilities/cpp_buffer/evidence.py', 'benchmarks/scripts/check_cpp_buffer.py',
        'benchmarks/scripts/capabilities/buffers.py',
        'benchmarks/scripts/capabilities/tasks.py', 'benchmarks/scripts/capabilities/public.py',
        'benchmarks/scripts/capabilities/config.py', 'benchmarks/scripts/grading/artifacts.py',
        'benchmarks/suites/capabilities-v1/config/families.json',
        'benchmarks/suites/capabilities-v1/controls/buffer_solutions.py',
        'benchmarks/suites/capabilities-v1/controls/cpp_buffer_solutions.py'}
    names.update(executor.sources('c_execute'))
    return {name: runtime.sha((ROOT / name).read_bytes()) for name in sorted(names)}


def pins():
    return {'compile': runtime.platform(), 'execution': executor.platform(True)}


def begin():
    executor.admit()
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0, 'model_api_calls': 0,
             'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('C++ pairing sources/runtime changed during controls')
    value = {**started, 'passed': result.wasSuccessful(), 'checks': result.testsRun,
             'failed': len(result.failures) + len(result.errors)}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


from startup_policy import offline_gate

@offline_gate
def require_validation():
    if _controls.get():
        return
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.is_file() else {}
    if (value.get('schema_version') != 1 or value.get('passed') is not True or value.get('model_api_calls') != 0
            or value.get('failed') != 0 or type(value.get('checks')) is not int or value['checks'] < 16
            or value.get('inputs_sha256') != fingerprints() or value.get('pins') != pins()):
        raise RuntimeError('C++ buffer controls missing or stale; run make check-cpp-buffer')
