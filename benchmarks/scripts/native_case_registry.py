"""Registered native build variants, using frozen inputs and existing graders.

Registration is data, not an instruction to rebuild or rerun prior attempts.
Payload identity is verified when a selected task resolves its public directory.
"""
import json
from pathlib import Path
import re

import records


def declared_path(name):
    if not isinstance(name, str):
        raise ValueError('Native registration path must be a string')
    path = Path(name)
    if path.is_absolute() or not path.parts or any(part in ('.', '..') for part in path.parts):
        raise ValueError('Native registration path escapes workspace')
    return records.ROOT / path


def relative(name):
    result = declared_path(name)
    if not result.resolve().is_relative_to(records.ROOT.resolve()):
        raise ValueError('Native registration path escapes workspace')
    return result


def configuration():
    name = records.registry().get('native_case_extensions')
    if name is None:
        return {'schema_version': 1, 'compiler_matrix_config': None, 'cases': []}
    value = records.read(relative(name))
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'compiler_matrix_config', 'cases'}
            or value['schema_version'] != 1 or not isinstance(value['cases'], list)):
        raise ValueError('Invalid native case extension registry')
    declared_path(value['compiler_matrix_config'])
    seen = set()
    for row in value['cases']:
        required = {'experiment', 'suite', 'id', 'base_case', 'public_directory', 'inputs_sha256',
                    'build_receipt', 'build_receipt_sha256', 'target_build'}
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError('Invalid native case extension')
        for key in ('experiment', 'suite', 'id', 'base_case'):
            if not isinstance(row[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', row[key]):
                raise ValueError('Invalid native case identity')
        identity = (row['experiment'], row['id'])
        if identity in seen or row['id'] == row['base_case']:
            raise ValueError('Duplicate native case registration')
        seen.add(identity)
        # Enumeration validates syntax only. Selected payloads/receipts are
        # confined and hash-checked by public_directory() before use. Resolving
        # every unselected path here repeats thousands of WSL metadata calls.
        declared_path(row['public_directory'])
        declared_path(row['build_receipt'])
        if not isinstance(row['inputs_sha256'], dict) or set(row['inputs_sha256']) != {'target', 'prompt.txt', 'manifest.json'}:
            raise ValueError('Native registration must freeze all three public payloads')
        if any(not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
               for digest in [row['build_receipt_sha256'], *row['inputs_sha256'].values()]):
            raise ValueError('Invalid native registration digest')
        build = row['target_build']
        fields = {'compiler_id', 'compiler_family', 'compiler_version', 'optimization', 'standard',
                  'string_abi', 'standard_library', 'strip_mode'}
        if (not isinstance(build, dict) or set(build) != fields
                or build['optimization'] not in ('O0', 'O2', 'O3')
                or type(build['string_abi']) is not int or build['string_abi'] not in (0, 1)
                or any(not isinstance(build[key], str) or not build[key] for key in fields - {'string_abi'})):
            raise ValueError('Invalid registered target build metadata')
        if row['id'] != row['base_case'] + '--' + build['compiler_id'] + '--' + build['optimization']:
            raise ValueError('Native build point ID does not match its metadata')
    return value


def require_matrix(config):
    registry = configuration()
    if not registry['cases'] or config != registry['compiler_matrix_config']:
        raise ValueError('Compiler inference requires a registered native case matrix')


def extensions(experiment, base_cases):
    if experiment['role'] != 'primary':
        return []
    original = {case['id']: case for case in base_cases}
    result = []
    for item in configuration()['cases']:
        if item['experiment'] != experiment['id']:
            continue
        if item['suite'] != experiment['suite'] or item['base_case'] not in original or item['id'] in original:
            raise ValueError('Native extension does not belong to an existing base case')
        base = original[item['base_case']]
        if base['language'] != 'cpp':
            raise ValueError('Compiler matrix extension requires an existing C++ case')
        result.append({**base, 'id': item['id'], 'label': item['id'],
            'standard': item['target_build']['standard'], 'string_abi': item['target_build']['string_abi'],
            'native_case_origin': 'compiler_matrix', 'base_case': item['base_case'],
            'registered_public_directory': item['public_directory'],
            'registered_public_sha256': item['inputs_sha256'],
            'registered_build_receipt': item['build_receipt'],
            'registered_build_receipt_sha256': item['build_receipt_sha256'],
            'target_build': item['target_build']})
    return result


def public_directory(case):
    directory = relative(case['registered_public_directory'])
    receipt_path = relative(case['registered_build_receipt'])
    if records.sha(receipt_path) != case['registered_build_receipt_sha256']:
        raise ValueError('Registered build receipt changed')
    receipt = records.read(receipt_path)
    if (receipt.get('status') != 'locally_verified' or receipt.get('point_id') != case['id']
            or receipt.get('experiment') != case['experiment'] or receipt.get('suite') != case['suite']
            or receipt.get('base_case') != case['base_case']
            or receipt.get('public_directory') != case['registered_public_directory']
            or receipt.get('binary_sha256') != case['registered_public_sha256']['target']
            or any(receipt.get(key) != value for key, value in case['target_build'].items())):
        raise ValueError('Registered build verification does not match the selected case')
    for name, expected in case['registered_public_sha256'].items():
        path = relative(case['registered_public_directory'] + '/' + name)
        if path.is_symlink() or records.sha(path) != expected:
            raise ValueError('Registered native public input changed: ' + name)
        if receipt.get('artifacts_sha256', {}).get(str(path.relative_to(records.ROOT))) != expected:
            raise ValueError('Registered public input lacks matching build verification')
    manifest = records.read(directory / 'manifest.json')
    if (manifest.get('id') != case['id'] or manifest.get('binary_sha256') != case['registered_public_sha256']['target']
            or any(manifest.get(key) != value for key, value in case['target_build'].items())):
        raise ValueError('Registered native manifest identity changed')
    return directory
