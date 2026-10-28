"""Acceptance gate for typed task preparation, capture, selection and cache reuse."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

import action_cache

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/capability-coordinator-validation.json'
_controls = ContextVar('capability_coordinator_controls', default=False)
SOURCES = ('benchmarks/scripts/capabilities/config.py', 'benchmarks/scripts/capabilities/tasks.py',
    'benchmarks/scripts/capabilities/public.py', 'benchmarks/scripts/capabilities/coordinator.py',
    'benchmarks/scripts/capabilities/store.py', 'benchmarks/scripts/capabilities/submission.py',
    'benchmarks/scripts/capabilities/evidence.py', 'benchmarks/scripts/grading/artifact_capture.py',
    'benchmarks/scripts/grading/artifacts.py', 'benchmarks/scripts/check_capability_coordinator.py',
    'benchmarks/scripts/run.py', 'benchmarks/scripts/cache.py', 'benchmarks/scripts/benchmark_config.py')
SOURCES += ('benchmarks/scripts/capabilities/abi/tasks.py', 'benchmarks/scripts/capabilities/abi/variants.py',
            'benchmarks/suites/capabilities-v1/config/abi-variants.json',
            'benchmarks/scripts/capabilities/preparation.py')
SOURCES += ('benchmarks/scripts/capabilities/lifecycle_tasks.py',)
SOURCES += ('benchmarks/scripts/capabilities/pointers/tasks.py',)
SOURCES += ('benchmarks/scripts/capabilities/variant_objects/tasks.py',)
SOURCES += ('benchmarks/scripts/capabilities/indirect_calls/tasks.py',)
SOURCES += ('benchmarks/scripts/capabilities/optimized_locations/tasks.py',
            'benchmarks/scripts/capabilities/native_abi/tasks.py')
SOURCES += ('benchmarks/scripts/capabilities/baselines/tasks.py',
            'benchmarks/scripts/capabilities/baselines/task_runtime.py',
            'benchmarks/scripts/capabilities/baselines/coordinator_checks.py')

SOURCES += ('benchmarks/scripts/parallel_controls.py', 'benchmarks/scripts/control_workspace.py',
            'benchmarks/scripts/check_parallel_controls.py')
SOURCES += ('benchmarks/scripts/capabilities/evidence_access.py', 'benchmarks/scripts/capabilities/analysis_context.py',)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def begin():
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0,
             'provider_launches': 0, 'model_api_calls': 0, 'inputs_sha256': fingerprints()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints():
        raise RuntimeError('Typed coordinator inputs changed during controls')
    value = {**started, 'passed': (result.wasSuccessful() and not getattr(result, 'skipped', [])
                        and not getattr(result, 'expectedFailures', [])), 'checks': result.testsRun,
             'failed': len(result.failures) + len(result.errors)}
    if hasattr(result, 'execution'):
        value['execution'] = result.execution
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


from startup_policy import offline_gate

@offline_gate
def require_validation():
    if _controls.get():
        return
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.exists() else {}
    if value.get('schema_version') == 2:
        import unittest
        import validation_cache
        from check_capability_coordinator import TypedCoordinatorControls
        names = unittest.defaultTestLoader.getTestCaseNames(TypedCoordinatorControls)
        plan = validation_cache.Plan(action_cache.ROOT, SOURCES, names)
        try:
            validation_cache.require(plan, value)
        except RuntimeError as error:
            if not str(error).startswith('Coordinator check needs validation:'):
                raise
            import incremental_validation
            if not (ROOT / incremental_validation.POLICY).is_file():
                raise
            incremental_validation.require(plan)
        return
    if (value.get('schema_version') != 1 or value.get('passed') is not True
            or value.get('inputs_sha256') != fingerprints() or value.get('provider_launches') != 0
            or value.get('model_api_calls') != 0 or value.get('failed') != 0
            or type(value.get('checks')) is not int or value['checks'] < 99):
        raise RuntimeError('Typed coordinator controls missing or stale; run make check-capability-coordinator')

SOURCES += ('benchmarks/scripts/grading/metadata_admission.py',)

SOURCES += ('benchmarks/scripts/capabilities/artifact_format.py',)
SOURCES += ('benchmarks/scripts/capabilities/attempts.py', 'benchmarks/scripts/check_attempt_routing.py')
SOURCES += ('benchmarks/scripts/validation_cache.py',)
SOURCES += ('benchmarks/scripts/preparation_session.py', 'benchmarks/scripts/read_session.py',
            'benchmarks/scripts/check_preparation_session.py')
