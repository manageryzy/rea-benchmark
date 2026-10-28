"""Fail-closed source/runtime gate for pending T3 probes; no model activation."""
from contextlib import contextmanager
from contextvars import ContextVar
import json

import action_cache
from . import runtime, contracts

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/pointer-probe-validation.json'
_controls = ContextVar('pointer_probe_controls', default=False)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def fingerprints():
    names = {str(runtime.POLICY.relative_to(ROOT)), runtime.MODULE + 'runtime.py',
             runtime.MODULE + 'contracts.py', runtime.MODULE + 'evidence.py',
             'benchmarks/scripts/capabilities/elf.py', 'benchmarks/scripts/check_pointer_probes.py'}
    for mode in ('probe', 'native'):
        names.update(runtime.sources(mode))
    for variant in contracts.VARIANTS:
        names.update(runtime.sources('original', variant))
    names.update(runtime.executor.sources('c_execute'))
    return {name: runtime.sha((ROOT / name).read_bytes()) for name in sorted(names)}


def pins():
    return {'original': runtime.platform('original'), 'probe': runtime.platform('probe'),
            'execution': runtime.executor.platform(True)}


def begin():
    runtime.executor.admit()
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0, 'model_api_calls': 0,
             'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('Pointer probe sources or runtime changed during controls')
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
    if (value.get('schema_version') != 1 or value.get('passed') is not True or value.get('failed') != 0
            or type(value.get('checks')) is not int or value['checks'] < 27 or value.get('model_api_calls') != 0
            or value.get('inputs_sha256') != fingerprints() or value.get('pins') != pins()):
        raise RuntimeError('Pointer probe controls missing or stale; T3 remains disabled')
