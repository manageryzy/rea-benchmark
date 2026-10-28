"""Validated selection and scheduling settings for the single coordinator."""
import json
from pathlib import Path

import records

DEFAULT = records.BENCH / 'config/benchmark.json'


def load(path=DEFAULT):
    value = json.loads(Path(path).read_text())
    allowed = {'schema_version', 'models', 'suites', 'experiments', 'cases', 'condition', 'execution', 'compiler_matrix', 'capabilities', 'evidence_access', 'exclude_cells', 'authorized_retries'}
    if value.get('schema_version') != 1 or set(value) - allowed:
        raise ValueError('Invalid benchmark configuration schema or unknown settings')
    primary = [e for e in records.registry()['experiments'] if e['role'] == 'primary']
    choices = {'models': set(records.models()), 'suites': {e['suite'] for e in primary},
               'experiments': {e['id'] for e in primary}}
    from capabilities.tasks import declared
    capability_cases = declared()
    choices['suites'].add('capabilities-v1')
    choices['experiments'].add('capabilities-v1')
    if type(value.setdefault('capabilities', True)) is not bool:
        raise ValueError('Capability selection must be a boolean')
    defaults = {'models': 'all', 'suites': 'all', 'experiments': 'primary', 'cases': 'all'}
    for name, default in defaults.items():
        selected = value.setdefault(name, default)
        if selected == default:
            continue
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
            raise ValueError(name + ' must be a nonempty unique list or ' + default)
        if any(not isinstance(item, str) or name in choices and item not in choices[name] for item in selected):
            raise ValueError('Unregistered selection in ' + name)
    known_cases = {c['id'] for e in primary for c in records.frozen_cases(e)}
    known_cases |= {e['id'] + '/' + c['id'] for e in primary for c in records.frozen_cases(e)}
    known_cases |= {row['task_id'] for row in capability_cases}
    known_cases |= {'capabilities-v1/' + row['task_id'] for row in capability_cases}
    if value['cases'] != 'all' and set(value['cases']) - known_cases:
        raise ValueError('Unregistered case selector')
    excluded = value.get('exclude_cells', [])
    if (not isinstance(excluded, list) or any(not isinstance(cell, list) or len(cell) != 3
            or any(not isinstance(part, str) for part in cell) for cell in excluded)):
        raise ValueError('exclude_cells must contain [model, experiment, case] triples')
    if len({tuple(cell) for cell in excluded}) != len(excluded):
        raise ValueError('Duplicate excluded benchmark cell')
    for model, experiment, case in excluded:
        if model not in choices['models'] or experiment not in choices['experiments'] or experiment + '/' + case not in known_cases:
            raise ValueError('Unregistered excluded benchmark cell')
    condition = value.setdefault('condition', 'active')
    if condition not in ['active', *[c['id'] for c in records.condition_catalog()['conditions']]]:
        raise ValueError('Unregistered condition')
    execution = value.setdefault('execution', {})
    if set(execution) - {'max_parallel_models', 'max_parallel_cases_per_model', 'report_every', 'budget_stop_scope', 'skip_saved_failures', 'startup_mode', 'transport_timeout_scope'}:
        raise ValueError('Unknown execution setting')
    if execution.setdefault('startup_mode', 'strict') not in ('strict', 'trusted_local'):
        raise ValueError('startup_mode must be strict or trusted_local')
    if execution.setdefault('budget_stop_scope', 'model') not in ('model', 'case'):
        raise ValueError('budget_stop_scope must be model or case')
    if execution.setdefault('transport_timeout_scope', 'model') not in ('model', 'case'):
        raise ValueError('transport_timeout_scope must be model or case')
    if type(execution.setdefault('skip_saved_failures', False)) is not bool:
        raise ValueError('skip_saved_failures must be a boolean')
    per_model = execution.setdefault('max_parallel_cases_per_model', 1)
    if type(per_model) is not int or per_model not in (1, 2):
        raise ValueError('max_parallel_cases_per_model must be 1 or 2')
    for name, default, minimum in [('max_parallel_models', 3, 1), ('report_every', 0, 0)]:
        number = execution.setdefault(name, default)
        if type(number) is not int or number < minimum:
            raise ValueError(name + ' must be an integer >= ' + str(minimum))
    matrix = value.get('compiler_matrix', {})
    if (set(matrix) - {'config', 'inference_enabled', 'note'}
            or type(matrix.get('inference_enabled', False)) is not bool):
        raise ValueError('Invalid compiler matrix selection')
    if matrix.get('inference_enabled', False):
        from native_case_registry import require_matrix
        require_matrix(matrix.get('config'))
    if 'evidence_access' in value:
        from capabilities.evidence_access import validate_selection
        validate_selection(value, capability_cases)
    retries = value.get('authorized_retries', [])
    if not isinstance(retries, list):
        raise ValueError('authorized_retries must be an explicit list')
    from capabilities.attempts import authorized_retry
    seen = set()
    for row in retries:
        checked = authorized_retry(row)
        model, experiment, case = checked['cell']
        key = (model, experiment, case)
        if (key in seen or model not in choices['models']
                or experiment + '/' + case not in known_cases
                or not accepts(value, {'model': model, 'suite': experiment,
                                      'experiment': experiment, 'case': case})):
            raise ValueError('Retry authorization is duplicate, unknown or excluded')
        seen.add(key)
    return value


def accepts(selection, entry):
    if [entry.get('model'), entry['experiment'], entry['case']] in selection.get('exclude_cells', []):
        return False
    if (entry.get('native_case_origin') == 'compiler_matrix'
            and not selection.get('compiler_matrix', {}).get('inference_enabled', False)):
        return False
    for plural, field in [('models', 'model'), ('suites', 'suite'), ('experiments', 'experiment')]:
        values = selection[plural]
        if isinstance(values, list) and entry[field] not in values:
            return False
    cases = selection['cases']
    return cases == 'all' or entry['case'] in cases or entry['experiment'] + '/' + entry['case'] in cases
