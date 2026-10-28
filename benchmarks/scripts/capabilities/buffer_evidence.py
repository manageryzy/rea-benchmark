"""Source-bound buffer-condition controls; separate from the C VM prototype."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

import action_cache
from grading.c_executor import runtime as c_runtime
from grading import image_manifest
from . import buffers, build

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/buffer-capability-validation.json'
_controls = ContextVar('buffer_capability_validation_controls', default=False)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def fingerprints():
    names = set(buffers.REFERENCE_SOURCES) | set(build.SOURCES) | {
        'benchmarks/scripts/capabilities/__init__.py', 'benchmarks/scripts/capabilities/buffers.py',
        'benchmarks/scripts/capabilities/build.py', 'benchmarks/scripts/capabilities/buffer_evidence.py',
        'benchmarks/scripts/check_buffer_capabilities.py',
        'benchmarks/suites/capabilities-v1/config/families.json',
        'benchmarks/suites/capabilities-v1/controls/buffer_solutions.py'}
    names.update(str(path.relative_to(ROOT)) for path in (build.SUITE / 'sources').iterdir() if path.is_file())
    for stage in ('c_compile', 'c_execute'):
        names.update(c_runtime.sources(stage))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def pins():
    return {'candidate_runtime': c_runtime.platform(True), 'original_cpp_image': image_manifest.load('cpp')['id'],
            'original_binutils': build.tooling()[0]}


def begin():
    c_runtime.admit()
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0,
             'model_api_calls': 0, 'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('Buffer capability inputs changed during controls')
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
    if (value.get('passed') is not True or value.get('schema_version') != 1
            or value.get('inputs_sha256') != fingerprints() or value.get('pins') != pins()
            or value.get('model_api_calls') != 0):
        raise RuntimeError('Buffer capability controls missing or stale; run make check-buffer-capabilities')
