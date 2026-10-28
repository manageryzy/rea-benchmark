"""Source-bound gate for original endpoint probes; inference stays disabled."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

import action_cache
from grading.c_executor import runtime as executor
from . import runtime
from . import reference

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/abi-probe-validation.json'
_controls = ContextVar('abi_probe_controls', default=False)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def fingerprints():
    names = set(runtime.sources('probe')) | {
        'benchmarks/config/abi-probes.json', 'benchmarks/scripts/capabilities/abi/runtime.py',
        'benchmarks/scripts/capabilities/abi/evidence.py', 'benchmarks/scripts/check_abi_probes.py',
        'benchmarks/scripts/capabilities/abi/variants.py', 'benchmarks/scripts/capabilities/abi/tasks.py',
        'benchmarks/scripts/capabilities/config.py', 'benchmarks/scripts/capabilities/tasks.py',
        'benchmarks/suites/capabilities-v1/config/abi-variants.json'}
    names.update(str(path.relative_to(ROOT)) for path in (ROOT / runtime.PREFIX).rglob('*') if path.is_file())
    for family in runtime.contracts.FAMILIES: names.update(runtime.sources('original', family))
    names.update(reference.SOURCES)
    names.update(executor.sources('c_execute'))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def pins():
    return {'original': runtime.platform('original'), 'probe': runtime.platform('probe'), 'execution': executor.platform(True)}


def begin():
    executor.admit()
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0, 'model_api_calls': 0,
             'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('ABI probe sources or runtime changed during controls')
    value = {**started, 'passed': result.wasSuccessful(), 'checks': result.testsRun, 'failed': len(result.failures) + len(result.errors)}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


from startup_policy import offline_gate

@offline_gate
def require_validation():
    if _controls.get():
        return
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.is_file() else {}
    if (value.get('passed') is not True or value.get('schema_version') != 1 or value.get('model_api_calls') != 0
            or type(value.get('checks')) is not int or value['checks'] < 35 or value.get('failed') != 0
            or value.get('inputs_sha256') != fingerprints() or value.get('pins') != pins()):
        raise RuntimeError('ABI probe controls missing or stale; run make check-abi-probes')
