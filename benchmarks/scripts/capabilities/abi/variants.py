"""Private counterfactual selection; compiler actions consume only selected bytes.

These reviewed facts supply gold controls, never the submitted-description probe.
The original endpoints remain authored C++ and are executed independently.
"""
import json

import action_cache
from grading.artifacts import identifier, integer, object_fields
from . import artifacts, contracts

ROOT = action_cache.ROOT
CATALOG = ROOT / 'benchmarks/suites/capabilities-v1/config/abi-variants.json'
PREFIX = 'benchmarks/suites/capabilities-v1/sources/abi/variants/'


def catalog():
    value = json.loads(CATALOG.read_bytes())
    object_fields(value, ('schema_version', 'variants'))
    integer(value['schema_version'], 1, 1)
    if not isinstance(value['variants'], list) or not value['variants']:
        raise ValueError('Counterfactual variants must be declared')
    result = {}
    for row in value['variants']:
        object_fields(row, ('id', 'condition', 'families'))
        identifier(row['id'])
        if row['id'] in result or row['condition'] not in ('scalar_recover', 'layout_recover'):
            raise ValueError('Unknown or duplicate counterfactual condition')
        if not isinstance(row['families'], dict) or set(row['families']) != set(contracts.FAMILIES):
            raise ValueError('Counterfactuals must cover all declared record families')
        for family, selected in row['families'].items():
            object_fields(selected, ('header', 'scalars', 'offsets'))
            path = str(action_cache.relative(selected['header']))
            if not path.startswith(PREFIX) or not path.endswith('.hpp'):
                raise ValueError('Counterfactual source escapes its reviewed header root')
            if not isinstance(selected['scalars'], dict) or not isinstance(selected['offsets'], dict):
                raise ValueError('Counterfactual facts must be bounded maps')
            for name, scalar in selected['scalars'].items():
                identifier(name)
                object_fields(scalar, ('id', 'bits', 'signed'))
                identifier(scalar['id'])
                if scalar['bits'] not in (8, 16, 32, 64) or type(scalar['bits']) is not int or type(scalar['signed']) is not bool:
                    raise ValueError('Unsupported counterfactual integer')
            for name, fields in selected['offsets'].items():
                identifier(name)
                if not isinstance(fields, dict) or not fields or len(fields) > 16:
                    raise ValueError('Invalid counterfactual field map')
                for role, offset in fields.items():
                    identifier(role)
                    integer(offset, 0, 127)
            if row['condition'] == 'scalar_recover' and (not selected['scalars'] or selected['offsets']):
                raise ValueError('Scalar counterfactual cannot change layout premises')
            if row['condition'] == 'layout_recover' and (selected['scalars'] or not selected['offsets']):
                raise ValueError('Layout counterfactual cannot change scalar premises')
        result[row['id']] = row
    return result


def select(family, variant):
    identifier(variant)
    values = catalog()
    if variant not in values or family not in values[variant]['families']:
        raise ValueError('Unregistered original-endpoint counterfactual')
    return values[variant]['families'][family]


def condition(variant):
    identifier(variant)
    values = catalog()
    if variant not in values:
        raise ValueError('Unregistered counterfactual condition')
    return values[variant]['condition']


def gold(family, binary_sha256, variant):
    selected = select(family, variant)
    value = contracts.gold(family, binary_sha256)
    nodes = {node['id']: node for node in value['types']}
    renamed = {}
    for name, facts in selected['scalars'].items():
        if name not in nodes or nodes[name]['kind'] != 'integer':
            raise ValueError('Counterfactual scalar does not exist')
        renamed[name] = facts['id']
        nodes[name].update(facts)
    for node in value['types']:
        if node['kind'] == 'record':
            for field in node['fields']:
                field['type'] = renamed.get(field['type'], field['type'])
        elif node['kind'] == 'array':
            node['element'] = renamed.get(node['element'], node['element'])
    for name, offsets in selected['offsets'].items():
        if name not in nodes or nodes[name]['kind'] != 'record' or set(offsets) != {f['role'] for f in nodes[name]['fields']}:
            raise ValueError('Counterfactual layout does not describe its whole record')
        for field in nodes[name]['fields']:
            field['offset'] = offsets[field['role']]
    return artifacts.types(value)
