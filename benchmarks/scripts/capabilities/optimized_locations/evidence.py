"""Offline preparation receipt; never a capability release or model result."""
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

import action_cache
from grading import image_manifest
from grading.c_executor.context import host
from capabilities import build as family_build
from . import build, tooling

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/optimized-location-validation.json'
MINIMUM_CHECKS = 42
_controls = ContextVar('optimized_location_controls', default=False)
NAMES = (
    'benchmarks/scripts/check_optimized_locations.py',
    'benchmarks/scripts/action_cache.py',
    'benchmarks/scripts/capabilities/__init__.py', 'benchmarks/scripts/capabilities/elf.py',
    'benchmarks/scripts/capabilities/locations.py', 'benchmarks/scripts/capabilities/build.py',
    'benchmarks/scripts/capabilities/config.py',
    'benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/offline.py',
    'benchmarks/scripts/grading/process.py', 'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py',
    'benchmarks/scripts/grading/image_manifest.py', 'benchmarks/scripts/grading/toolchains.py',
    'benchmarks/scripts/grading/artifacts.py',
    'benchmarks/scripts/grading/c_executor/context.py',
)


def fingerprints():
    from . import runtime
    names = set(NAMES) | {build.MODULE + name for name in (
        '__init__.py', 'build.py', 'build_worker.py', 'tooling.py', 'native.py', 'native_worker.py',
        'reference.py', 'dwarf.py', 'review.py', 'evidence.py', 'artifact.py', 'scoring.py', 'tasks.py', 'runtime.py', 'grade_worker.py')}
    names.update(runtime.SOURCES)
    names.update(('benchmarks/scripts/grading/structured/runtime.py', 'benchmarks/config/structured-grading.json'))
    names.update(build.PREFIX + name for name in build.FILES)
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def pins():
    return {'compiler_image': image_manifest.load('cpp')['id'],
            'binutils_image': family_build.tooling()[0], 'lto_image_id': tooling.record()['id'], **host()}


def begin():
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0,
             'model_api_calls': 0, 'scope': 'optimized_location_preparation_only',
             'release_accepted': False, 'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('Optimized-location controls changed while running')
    value = {**started, 'passed': result.wasSuccessful() and result.testsRun >= MINIMUM_CHECKS,
             'checks': result.testsRun, 'failed': len(result.failures) + len(result.errors)}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def current():
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.is_file() else {}
    return (type(value.get('schema_version')) is int and value['schema_version'] == 1 and value.get('passed') is True
            and type(value.get('checks')) is int and value['checks'] >= MINIMUM_CHECKS
            and value.get('failed') == 0 and value.get('model_api_calls') == 0
            and value.get('release_accepted') is False
            and value.get('scope') == 'optimized_location_preparation_only'
            and value.get('inputs_sha256') == fingerprints() and value.get('pins') == pins())


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


from startup_policy import offline_gate

@offline_gate
def require_validation():
    if not _controls.get() and not current():
        raise RuntimeError('Optimized controls missing or stale; run make check-optimized-locations')
