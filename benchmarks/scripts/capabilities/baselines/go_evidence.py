"""Source-bound offline receipt for the development Go ABIInternal buffer path."""
import hashlib
import json

import action_cache
from grading.c_executor import runtime as executor
from . import evidence, go_runtime, go_execute

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/go-buffer-validation.json'
MINIMUM_CHECKS = 18


def fingerprints():
    names = set(evidence.fingerprints()) | set(go_runtime.SOURCES) | set(go_execute.SOURCES) | {
        go_runtime.MODULE + name for name in ('go_grade.py', 'go_controls.py', 'go_evidence.py')}
    names.update(('benchmarks/config/go-buffer.json', 'benchmarks/scripts/check_go_buffer.py'))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def pins():
    return {'originals': evidence.pins(), 'go_compiler': go_runtime.platform(),
            'execution': executor.platform(True)}


def begin():
    executor.admit()
    value = {'schema_version': 1, 'scope': 'restricted_go_abiinternal_buffer_controls_only',
             'passed': False, 'checks': 0, 'failed': 0, 'model_api_calls': 0,
             'release_accepted': False, 'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('Restricted Go buffer sources/toolchains changed during controls')
    value = {**started, 'passed': result.wasSuccessful() and result.testsRun >= MINIMUM_CHECKS,
             'checks': result.testsRun, 'failed': len(result.failures) + len(result.errors)}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def current():
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.is_file() else {}
    return (value.get('schema_version') == 1 and value.get('passed') is True
            and type(value.get('checks')) is int and value['checks'] >= MINIMUM_CHECKS
            and value.get('failed') == value.get('model_api_calls') == 0
            and value.get('scope') == 'restricted_go_abiinternal_buffer_controls_only'
            and value.get('release_accepted') is False
            and value.get('inputs_sha256') == fingerprints() and value.get('pins') == pins())
