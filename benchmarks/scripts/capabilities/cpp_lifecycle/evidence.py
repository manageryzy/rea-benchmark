"""Separate source/runtime acceptance for the matched C++ lifecycle condition."""
from contextlib import contextmanager
from contextvars import ContextVar
import json

import action_cache
from grading.c_executor import runtime as executor
from .. import build, lifecycle
from . import runtime

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/cpp-lifecycle-validation.json'
_controls = ContextVar('cpp_lifecycle_validation_controls', default=False)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def fingerprints():
    # The logical old-header path is an explicit alias, not a physical source
    # dependency. Only the new header's actual bytes enter this control receipt.
    names = set(runtime.sources()) - {runtime.ALIAS}
    names.update(executor.sources('c_execute'))
    names.update(lifecycle.REFERENCE_SOURCES)
    names.update(build.SOURCES)
    names.update(('benchmarks/scripts/capabilities/lifecycle.py',
        'benchmarks/scripts/capabilities/lifecycle_build.py',
        runtime.PREFIX + 'evidence.py', 'benchmarks/scripts/check_cpp_lifecycle.py'))
    for path in (build.SUITE / 'controls/lifecycle_cpp').glob('*.cpp'):
        names.add(str(path.relative_to(ROOT)))
    for name in ('common.hpp', 'callback_policy.cpp', 'session_state.cpp'):
        names.add('benchmarks/suites/capabilities-v1/sources/lifecycle/' + name)
    return {name: runtime.base.sha((ROOT / name).read_bytes()) for name in sorted(names)}


def pins():
    return {'compiler': runtime.platform(), 'execution': executor.platform(True),
            'binutils': build.tooling()[0]}


def begin():
    executor.admit()
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0, 'model_api_calls': 0,
             'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('C++ lifecycle control inputs changed during validation')
    value = {**started, 'passed': result.wasSuccessful(), 'checks': result.testsRun,
             'failed': len(result.failures) + len(result.errors)}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


from startup_policy import offline_gate

@offline_gate
def require_validation():
    if _controls.get():
        return
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.exists() else {}
    if (value.get('schema_version') != 1 or value.get('passed') is not True or value.get('model_api_calls') != 0
            or value.get('failed') != 0 or type(value.get('checks')) is not int or value['checks'] < 12
            or value.get('inputs_sha256') != fingerprints() or value.get('pins') != pins()):
        raise RuntimeError('C++ lifecycle controls missing or stale; run make check-cpp-lifecycle')
