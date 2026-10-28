"""Source-bound offline acceptance for explicit C5/C6 conditions."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

import action_cache
from grading import image_manifest
from grading.c_executor import runtime as executor
from . import build, lifecycle

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/lifecycle-capability-validation.json'
_controls = ContextVar('lifecycle_validation_controls', default=False)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def fingerprints():
    names = set(build.SOURCES) | set(lifecycle.REFERENCE_SOURCES) | {
        'benchmarks/scripts/capabilities/build.py', 'benchmarks/scripts/capabilities/elf.py',
        'benchmarks/scripts/capabilities/lifecycle.py', 'benchmarks/scripts/capabilities/lifecycle_evidence.py',
        'benchmarks/scripts/capabilities/lifecycle_build.py',
        'benchmarks/scripts/check_lifecycle_capabilities.py'}
    for directory in ('sources/lifecycle', 'controls/lifecycle'):
        names.update(str(path.relative_to(ROOT)) for path in (build.SUITE / directory).iterdir() if path.is_file())
    for stage in ('c_compile', 'c_execute'):
        names.update(executor.sources(stage))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def pins():
    return {'candidate': executor.platform(True), 'original_cpp': image_manifest.load('cpp')['id'],
            'original_binutils': build.tooling()[0]}


def begin():
    executor.admit()
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0, 'model_api_calls': 0,
             'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('Lifecycle acceptance inputs changed during controls')
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
    if (value.get('schema_version') != 1 or value.get('passed') is not True
            or value.get('model_api_calls') != 0 or value.get('inputs_sha256') != fingerprints()
            or value.get('pins') != pins() or value.get('failed') != 0
            or type(value.get('checks')) is not int or value['checks'] < 22):
        raise RuntimeError('Lifecycle controls missing or stale; run make check-lifecycle-capabilities')
