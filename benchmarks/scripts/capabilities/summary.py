"""Bounded preparation status for global reports; no cache or tool installations."""
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
        row = {'id': name, 'passed': False, 'current': False, 'checks': 0, 'failed': 0}
        if path.is_file():
            value = json.loads(read_bytes(path))
            if value.get('schema_version') != 1 or value.get('model_api_calls') != 0:
                raise ValueError('Capability controls were not confirmed offline')
            expected_names = set(fingerprints())
            current = set(value.get('inputs_sha256', {})) == expected_names
            for item in sorted(expected_names):
                raw = read_bytes(ROOT.joinpath(*action_cache.relative(item).parts))
                current = hashlib.sha256(raw).hexdigest() == value.get('inputs_sha256', {}).get(item) and current
            checks, failed = value.get('checks', 0), value.get('failed', 0)
            if any(type(number) is not int or not 0 <= number <= 10000 for number in (checks, failed)):
                raise ValueError('Invalid capability control counts')
            row.update(passed=value.get('passed') is True and failed == 0 and checks > 0,
                       current=current, checks=checks, failed=failed)
        result['controls'].append(row)
    result['controls'].extend(baseline_report.for_report(read_bytes))
    return result
