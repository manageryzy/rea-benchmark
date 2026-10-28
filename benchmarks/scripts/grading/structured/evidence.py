"""Independent acceptance for closed artifact workers, separate from native grades."""
import hashlib
import json
from pathlib import Path

import action_cache
from . import runtime

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/structured-grading-validation.json'


def fingerprints():
    names = set(runtime.SOURCES) | {'benchmarks/config/structured-grading.json',
        'benchmarks/scripts/grading/structured/evidence.py', 'benchmarks/scripts/check_structured_grading.py'}
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def begin():
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0, 'model_api_calls': 0,
             'runtime_id': runtime.image.record()['id'], 'inputs_sha256': fingerprints()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if fingerprints() != started['inputs_sha256'] or runtime.image.record()['id'] != started['runtime_id']:
        raise RuntimeError('Structured acceptance inputs changed during controls')
    value = {**started, 'passed': result.wasSuccessful(), 'checks': result.testsRun,
             'failed': len(result.failures) + len(result.errors)}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


from startup_policy import offline_gate

@offline_gate
def require_validation():
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.exists() else {}
    if (value.get('schema_version') != 1 or value.get('passed') is not True
            or value.get('inputs_sha256') != fingerprints() or value.get('model_api_calls') != 0
            or value.get('runtime_id') != runtime.image.record()['id']):
        raise RuntimeError('Closed artifact controls missing or stale; run make check-structured-grading')
