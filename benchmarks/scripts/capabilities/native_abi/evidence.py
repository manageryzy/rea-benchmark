"""Source/runtime-bound native ABI preparation, separate from model release."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

import action_cache
from grading.c_executor import runtime as executor
from . import build, execute, probe

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/native-abi-validation.json'
MINIMUM_CHECKS = 40
_controls = ContextVar('native_abi_controls', default=False)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def fingerprints():
    names = set(build.SOURCES) | set(execute.SOURCES) | set(probe.SOURCES)
    names.update(build.MODULE + name for name in (
        'build.py', 'native.py', 'native_worker.py', 'reference.py', 'contracts.py', 'evidence.py', 'runtime.py', 'tasks.py'))
    names.update(build.PREFIX + language + '/' + name for language, files in build.FILES.items() for name in files)
    names.update(('benchmarks/scripts/check_native_abi.py', 'benchmarks/scripts/capabilities/build.py',
                  'benchmarks/scripts/action_cache.py', 'benchmarks/config/c-executor.json',
                  'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py',
                  'benchmarks/scripts/grading/c_executor/runtime.py', 'benchmarks/scripts/grading/c_executor/image.py'))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def pins():
    return {'originals': {language: build.action(language)[3] for language in build.FILES},
            'probe': executor.platform(), 'execution': executor.platform(True)}


def begin():
    executor.admit()
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0,
             'model_api_calls': 0, 'release_accepted': False, 'scope': 'native_abi_preparation_only',
             'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('Native ABI sources/runtime changed during controls')
    value = {**started, 'passed': result.wasSuccessful() and result.testsRun >= MINIMUM_CHECKS,
             'checks': result.testsRun, 'failed': len(result.failures) + len(result.errors)}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


from startup_policy import offline_gate

@offline_gate
def require_validation():
    if _controls.get():
        return
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.is_file() else {}
    if (type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('passed') is not True or value.get('failed') != 0
            or value.get('model_api_calls') != 0 or value.get('release_accepted') is not False
            or value.get('scope') != 'native_abi_preparation_only'
            or type(value.get('checks')) is not int or value['checks'] < MINIMUM_CHECKS
            or value.get('inputs_sha256') != fingerprints() or value.get('pins') != pins()):
        raise RuntimeError('Native ABI controls missing or stale; run make check-native-abi')
