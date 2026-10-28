"""Typed admission and scoring, excluding append-only storage and reporting."""
from grading import artifacts
from grading.artifact_capture import extract
from grading.structured import runtime as structured
from . import buffers


def location_totals(grade):
    results = []
    for key, score in grade.get('capabilities', {}).items():
        if key == 'L1':
            results.append(bool(score['top1']))
        elif key in ('L2', 'L3', 'L4') and score['f1'] is not None:
            results.append(score['f1'] == 1)
        elif key == 'L5':
            results.append(score['root_correct'] and score['nodes']['f1'] == score['edges']['f1'] == 1)
    return sum(results), len(results)


def evaluate(answer, task, value):
    try:
        files = extract(answer, task['artifact_schema'])
    except (ValueError, UnicodeError):
        kind = value['kind']
        total = (2 * len(value['nonces']) if kind in ('abi_probe', 'pointer_probe', 'variant_object', 'indirect_call', 'native_abi') else
                 len(value['truth']['realizations']) if kind == 'optimized_localization' else
                 sum(len(row['steps']) for row in value['scenarios']) if kind in ('c_lifecycle', 'cpp_lifecycle') else
                 len(value['cases']) if kind in ('c_buffer', 'cpp_buffer', 'matched_buffer') else len(task['capability_ids']))
        return {'status': 'invalid_artifact_set', 'artifact_valid': False, 'passed': 0,
                'total': total, 'capabilities': {}, 'task_success': False, 'compiled': False,
                'return_provenance': 'not_executed'}, {}
    if value['kind'] in ('optimized_localization', 'native_abi'):
        optimized = value['kind'] == 'optimized_localization'
        name, maximum = ('instances.json', 131072) if optimized else ('native-abi.json', 8192)
        # A well-framed but oversized solver artifact is an ordinary rejected
        # submission, not a broken worker transport or an infrastructure score.
        if len(files[name]) > maximum:
            return {'status': 'invalid_artifact', 'artifact_valid': False, 'compiled': False,
                    'passed': 0, 'total': len(value['truth']['realizations']) if optimized else 2 * len(value['nonces']),
                    'capabilities': {}, 'task_success': False, 'return_provenance': 'not_executed'}, files
    if value['kind'] == 'matched_buffer':
        from .baselines import task_runtime as matched_runtime
        result = matched_runtime.grade(files, value)
    elif value['kind'] == 'localization':
        result = structured.grade(files, value['truth'])
        result['capabilities'] = {key: score for key, score in result.get('capabilities', {}).items()
                                  if key in task['capability_ids']}
        passed, total = location_totals(result)
        if not result.get('artifact_valid'):
            passed, total = 0, len(task['capability_ids'])
        result.update(passed=passed, total=total)
    elif value['kind'] == 'optimized_localization':
        from .optimized_locations import runtime as optimized_runtime
        result = optimized_runtime.grade(files['instances.json'], value['mode'], target=value['target'])
    elif value['kind'] == 'native_abi':
        from .native_abi import runtime as native_runtime
        result = native_runtime.grade(value['language'], files['native-abi.json'], nonces=value['nonces'], target=value['target'])
    elif value['kind'] == 'indirect_call':
        from .indirect_calls import runtime as indirect_runtime
        result = indirect_runtime.grade(files['calls.json'], value['target'], nonces=value['nonces'])
    elif value['kind'] == 'variant_object':
        from .variant_objects import runtime as variant_runtime
        result = variant_runtime.grade(files['types.json'], value['target'], nonces=value['nonces'])
    elif value['kind'] == 'pointer_probe':
        from .pointers import runtime as pointer_runtime
        result = pointer_runtime.grade(files['types.json'], value['target'], nonces=value['nonces'])
    elif value['kind'] == 'abi_probe':
        from .abi import runtime as abi_runtime
        name = next(iter(task['artifact_schema']))
        result = abi_runtime.grade(files[name], value['target'], value['condition'], nonces=value['nonces'])
    else:
        lifecycle_condition = value['kind'] in ('c_lifecycle', 'cpp_lifecycle')
        expected_total = sum(len(row['steps']) for row in value['scenarios']) if lifecycle_condition else len(value['cases'])
        try:
            admitted = artifacts.admit(files, task['artifact_schema'])
        except ValueError:
            return {'status': 'invalid_artifact', 'artifact_valid': False, 'passed': 0,
                    'total': expected_total, 'capabilities': {}, 'task_success': False}, files
        cpp = value['kind'] in ('cpp_buffer', 'cpp_lifecycle')
        if lifecycle_condition:
            from . import lifecycle
            result = lifecycle.grade(admitted['solution.cpp' if cpp else 'solution.c']['value'], value['family_id'],
                                     value['scenarios'], value['oracle'], language='cpp' if cpp else 'c')
            result['capabilities'] = {key: score for key, score in result.get('capabilities', {}).items()
                                      if key in task['capability_ids']}
        else:
            result = buffers.grade(admitted['solution.cpp' if cpp else 'solution.c']['value'], value['cases'], value['oracle'],
                                   language='cpp' if cpp else 'c')
        result['artifact_valid'] = True
    if result.get('return_provenance') == 'host_emulator_return_v1' and not result.get('checks'):
        result['return_provenance'] = 'not_executed'
    result.pop('cache_hit', None)
    result['evaluation_id'] = result.pop('grading_id', None)
    result['task_success'] = bool(result.get('artifact_valid') and result['total'] and result['passed'] == result['total'])
    return result, files
