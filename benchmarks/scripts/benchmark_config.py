"""Validated selection and scheduling settings for the single coordinator."""
import json
from pathlib import Path

import records

DEFAULT = records.BENCH / 'config/benchmark.json'


def load(path=DEFAULT):
    value = json.loads(Path(path).read_text())
    allowed = {'schema_version', 'models', 'suites', 'experiments', 'cases', 'condition', 'execution', 'compiler_matrix'}
    if value.get('schema_version') != 1 or set(value) - allowed:
        raise ValueError('Invalid benchmark configuration schema or unknown settings')
    primary = [e for e in records.registry()['experiments'] if e['role'] == 'primary']
    choices = {'models': set(records.models()), 'suites': {e['suite'] for e in primary},
               'experiments': {e['id'] for e in primary}}
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
    if value['cases'] != 'all' and set(value['cases']) - known_cases:
        raise ValueError('Unregistered case selector')
    condition = value.setdefault('condition', 'active')
    if condition not in ['active', *[c['id'] for c in records.condition_catalog()['conditions']]]:
        raise ValueError('Unregistered condition')
    execution = value.setdefault('execution', {})
    if set(execution) - {'max_parallel_models', 'report_every'}:
        raise ValueError('Unknown execution setting')
    for name, default, minimum in [('max_parallel_models', 3, 1), ('report_every', 0, 0)]:
        number = execution.setdefault(name, default)
        if type(number) is not int or number < minimum:
            raise ValueError(name + ' must be an integer >= ' + str(minimum))
    matrix = value.get('compiler_matrix', {})
    if set(matrix) - {'config', 'inference_enabled', 'note'} or matrix.get('inference_enabled', False) is not False:
        raise ValueError('Compiler expansion is build-only until its cases are registered for inference')
    return value


def accepts(selection, entry):
    for plural, field in [('models', 'model'), ('suites', 'suite'), ('experiments', 'experiment')]:
        values = selection[plural]
        if isinstance(values, list) and entry[field] not in values:
            return False
    cases = selection['cases']
    return cases == 'all' or entry['case'] in cases or entry['experiment'] + '/' + entry['case'] in cases
