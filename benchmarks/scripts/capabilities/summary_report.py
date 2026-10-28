"""Active preparation projection, including per-action coordinator receipts.

The legacy summary.py stays byte-identical because historical validation actions
bind it. Only this report module evolves; projection never runs validation checks.
"""
import hashlib
import json
from pathlib import Path

import action_cache

ROOT = action_cache.ROOT
PINS = ROOT / 'benchmarks/config/capability-task-pins.json'


def empty():
    return {'status': 'not_recorded', 'inference_enabled': False, 'families': 0, 'task_definitions': 0,
            'frozen_tasks': 0, 'frozen_inputs_current': False, 'model_api_calls': 0,
            'supported_capabilities': [], 'source_languages': [], 'controls': []}


def for_report(read_bytes):
    from . import config, buffer_evidence, lifecycle_evidence, evidence
    from grading.structured import evidence as structured
    from .abi import evidence as abi_evidence
    from .cpp_buffer import evidence as cpp_evidence
    from .cpp_lifecycle import evidence as cpp_lifecycle_evidence
    from .pointers import evidence as pointer_evidence
    from .variant_objects import evidence as variant_evidence
    from .indirect_calls import evidence as indirect_evidence
    from .optimized_locations import evidence as optimized_evidence
    from .native_abi import evidence as native_evidence
    from .baselines import report as baseline_report
    import check_capabilities
    settings = json.loads(read_bytes(config.DEFAULT))
    definitions = json.loads(read_bytes(ROOT / settings['families_file']))
    result = {**empty(), 'status': 'offline_preparation', 'inference_enabled': settings['inference_enabled'],
              'families': len(definitions['families']), 'task_definitions': sum(
                  len(profile['families']) * len(profile['conventions']) if profile['kind'] == 'abi_probe' else len(profile.get('families', definitions['families']))
                  for profile in settings['profiles']) + 1,
              'supported_capabilities': ['L1', 'L2', 'L3', 'L4', 'L5', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'T1', 'T2', 'T3', 'T4', 'T5', 'T6'], 'source_languages': ['cpp']}
    if any(p['kind'] == 'optimized_localization' for p in settings['profiles']):
        result['supported_capabilities'].insert(5, 'L6')
    if any(p['kind'] == 'native_abi' for p in settings['profiles']):
        result['supported_capabilities'].append('T7')
        result['source_languages'] += sorted({p['variant'] for p in settings['profiles'] if p['kind'] == 'native_abi'})
    if PINS.is_file():
        pins = json.loads(read_bytes(PINS))
        if pins.get('schema_version') != 1 or pins.get('model_api_calls') != 0:
            raise ValueError('Invalid capability preparation receipt')
        current = bool(pins.get('inputs_sha256'))
        for name, expected in pins.get('inputs_sha256', {}).items():
            if not name.startswith(('benchmarks/config/', 'benchmarks/scripts/capabilities/', 'benchmarks/scripts/grading/', 'benchmarks/suites/capabilities-v1/')):
                raise ValueError('Unreviewed capability preparation input')
            path = ROOT.joinpath(*action_cache.relative(name).parts)
            try:
                observed = hashlib.sha256(read_bytes(path)).hexdigest()
            except OSError:
                observed = None
            current = observed == expected and current
        current = current and len(pins['tasks']) == result['task_definitions']
        result.update(frozen_tasks=len(pins['tasks']), frozen_inputs_current=current)
    receipts = [
        ('localization', ROOT / 'benchmarks/config/capability-validation.json', check_capabilities.source_hashes),
        ('structured_runtime', structured.RECEIPT, structured.fingerprints),
        ('c_buffers', buffer_evidence.RECEIPT, buffer_evidence.fingerprints),
        ('c_lifecycle', lifecycle_evidence.RECEIPT, lifecycle_evidence.fingerprints),
        ('cpp_buffers', cpp_evidence.RECEIPT, cpp_evidence.fingerprints),
        ('cpp_lifecycle', cpp_lifecycle_evidence.RECEIPT, cpp_lifecycle_evidence.fingerprints),
        ('abi_probes', abi_evidence.RECEIPT, abi_evidence.fingerprints),
        ('pointer_probes', pointer_evidence.RECEIPT, pointer_evidence.fingerprints),
        ('variant_objects', variant_evidence.RECEIPT, variant_evidence.fingerprints),
        ('indirect_calls', indirect_evidence.RECEIPT, indirect_evidence.fingerprints),
        ('optimized_locations', optimized_evidence.RECEIPT, optimized_evidence.fingerprints),
        ('native_abi', native_evidence.RECEIPT, native_evidence.fingerprints),
        ('coordinator', evidence.RECEIPT, evidence.fingerprints)]
    for name, path, fingerprints in receipts:
        result['controls'].append(control_row(name, path, fingerprints, read_bytes))
    import incremental_validation
    if (ROOT / incremental_validation.RECEIPT).is_file():
        row = {'id': 'coordinator_incremental', 'passed': False, 'current': False, 'checks': 0, 'failed': 0}
        value = json.loads(read_bytes(ROOT / incremental_validation.RECEIPT))
        row['checks'] = value.get('focused_checks', 0) + value.get('coordinator_checks', 0)
        try:
            plan = coordinator_plan()
            for name, raw in {**plan.inputs, **plan.projections}.items():
                if read_bytes(ROOT / name) != raw:
                    raise ValueError('Incremental report inputs changed')
            policy = json.loads(read_bytes(ROOT / incremental_validation.POLICY))
            for name in policy['additional_inputs']:
                read_bytes(ROOT / name)
            admitted = incremental_validation.require(plan,
                ReportCache(ROOT / '.cache/benchmark-actions/v3', read_bytes))
            row.update(passed=True, current=True,
                checks=admitted['focused_checks'] + admitted['coordinator_checks'])
        except (RuntimeError, ValueError, OSError, KeyError, TypeError):
            row['failed'] = 1
        result['controls'].append(row)
    result['controls'].extend(baseline_report.for_report(read_bytes))
    return result


def coordinator_plan():
    """Build current action identities; this does not execute or publish checks."""
    import unittest
    import validation_cache
    from check_capability_coordinator import TypedCoordinatorControls
    from . import evidence
    names = unittest.defaultTestLoader.getTestCaseNames(TypedCoordinatorControls)
    return validation_cache.Plan(ROOT, evidence.SOURCES, names)


class ReportCache(action_cache.Cache):
    """Keep the normal AC/CAS admission and bind every consumed proof to the report."""
    def __init__(self, root, read_bytes):
        super().__init__(root)
        self.read_bytes = read_bytes

    def get(self, value):
        action_cache.valid_digest(value)
        before = self.read_bytes(self._path('cas', value['hash']))
        result = super().get(value)
        if result != before:
            raise ValueError('Validation proof changed during report projection')
        return result

    def lookup(self, action):
        path = self._path('ac', action.key)
        if not path.exists():
            return None
        before = self.read_bytes(path)
        result = super().lookup(action)
        if path.read_bytes() != before:
            raise ValueError('Validation action changed during report projection')
        return result


def coordinator_current(value, read_bytes):
    import validation_cache
    try:
        plan = coordinator_plan()
        # Report action identity includes current validation inputs, projected
        # source modules and cache proofs; the validation action is unchanged.
        for name, raw in {**plan.inputs, **plan.projections}.items():
            if read_bytes(ROOT / name) != raw:
                raise ValueError('Validation input changed during report projection')
        validation_cache.require(plan, value,
            cache=ReportCache(ROOT / '.cache/benchmark-actions/v3', read_bytes))
    except (RuntimeError, ValueError, OSError, KeyError, TypeError):
        # Historical passes remain visible. Missing/stale/corrupt current AC/CAS
        # evidence cannot be promoted to current controls by this renderer.
        return False
    return True


def control_row(name, path, fingerprints, read_bytes):
    row = {'id': name, 'passed': False, 'current': False, 'checks': 0, 'failed': 0}
    if not path.is_file():
        return row
    value = json.loads(read_bytes(path))
    if not isinstance(value, dict):
        raise ValueError('Invalid capability control receipt')
    version = value.get('schema_version')
    if (type(version) is not int or version not in (1, 2)
            or version == 2 and name != 'coordinator'
            or type(value.get('model_api_calls')) is not int or value['model_api_calls'] != 0
            or 'provider_launches' in value and (type(value['provider_launches']) is not int
                or value['provider_launches'] != 0)):
        raise ValueError('Capability controls were not confirmed offline')
    checks, failed = value.get('checks', 0), value.get('failed', 0)
    if (type(value.get('passed')) is not bool
            or any(type(number) is not int or not 0 <= number <= 10000 for number in (checks, failed))
            or failed > checks):
        raise ValueError('Invalid capability control counts')
    if version == 2:
        import re
        import validation_cache
        actions = value.get('actions')
        if (value.get('kind') != validation_cache.VERSION
                or value.get('provider_launches') != 0 or not isinstance(actions, dict)
                or len(actions) != checks or any(not isinstance(key, str) or not key.startswith('test_')
                    or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                    for key, digest in actions.items())):
            raise ValueError('Invalid cached coordinator receipt shape')
        current = coordinator_current(value, read_bytes)
    else:
        inputs = value.get('inputs_sha256', {})
        if not isinstance(inputs, dict):
            raise ValueError('Invalid capability control input map')
        try:
            expected_names = set(fingerprints())
            current = set(inputs) == expected_names
            for item in sorted(expected_names):
                raw = read_bytes(ROOT.joinpath(*action_cache.relative(item).parts))
                current = hashlib.sha256(raw).hexdigest() == inputs.get(item) and current
        except OSError:
            current = False
    row.update(passed=value['passed'] and failed == 0 and checks > 0,
               current=current, checks=checks, failed=failed)
    return row
