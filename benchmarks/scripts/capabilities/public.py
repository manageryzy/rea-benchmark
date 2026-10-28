"""Allowlist-only task packages; private build manifests are never copied out."""
import hashlib
import json
from pathlib import Path

from grading.artifacts import digest, identifier, integer, object_fields

FIELDS = ('schema_version', 'family_id', 'build_id', 'task_id', 'capability_id',
          'source_language', 'artifact_schema', 'binary_sha256', 'architecture', 'hint_profile')


def manifest(private, *, capability, task_id):
    if capability not in ('L1', 'L2', 'L3', 'L4', 'L5'):
        raise ValueError('Unregistered capability projection')
    result = {key: private[key] for key in ('family_id', 'build_id', 'source_language', 'binary_sha256', 'architecture')}
    result.update(schema_version=1, task_id=task_id, capability_id=capability,
                  artifact_schema={'locations.json': 1}, hint_profile='no_business_entry_v1')
    validate_manifest(result)
    return result


def validate_manifest(value):
    object_fields(value, FIELDS, ('capability_ids',))
    integer(value['schema_version'], 1, 1)
    for key in ('family_id', 'task_id'):
        identifier(value[key])
    for key in ('build_id', 'binary_sha256'):
        digest(value[key])
    if (value['capability_id'] not in ('L1', 'L2', 'L3', 'L4', 'L5')
            or value['source_language'] not in ('cpp', 'rust', 'go')
            or value['architecture'] != 'x86_64'
            or value['hint_profile'] != 'no_business_entry_v1'
            or not isinstance(value['artifact_schema'], dict)
            or set(value['artifact_schema']) != {'locations.json'}
            or type(value['artifact_schema']['locations.json']) is not int
            or value['artifact_schema']['locations.json'] != 1):
        raise ValueError('Unsupported public task contract')
    if 'capability_ids' in value:
        ids = value['capability_ids']
        if (not isinstance(ids, list) or not ids or any(item not in ('L1', 'L2', 'L3', 'L4', 'L5') for item in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('Invalid public localization stages')
    return value


def validate_buffer_manifest(value):
    object_fields(value, ('schema_version', 'family_id', 'build_id', 'task_id', 'capability_ids',
        'source_language', 'output_language', 'artifact_schema', 'binary_sha256', 'architecture',
        'hint_profile', 'entry_rva', 'condition'))
    integer(value['schema_version'], 1, 1)
    for name in ('family_id', 'task_id'):
        identifier(value[name])
    for name in ('build_id', 'binary_sha256'):
        digest(value[name])
    integer(value['entry_rva'])
    cpp = value['condition'] in ('cpp_recover', 'cpp_codegen')
    filename, language = ('solution.cpp', 'cpp') if cpp else ('solution.c', 'c')
    if (value['capability_ids'] != ['C1', 'C2', 'C3', 'C4'] or value['source_language'] != 'cpp'
            or value['output_language'] != language or value['architecture'] != 'x86_64'
            or value['hint_profile'] != 'known_buffer_entry_v1' or value['condition'] not in ('c_recover', 'c_codegen', 'cpp_recover', 'cpp_codegen')
            or value['artifact_schema'] != {filename: 1} or type(value['artifact_schema'][filename]) is not int):
        raise ValueError('Unsupported public paired-buffer task contract')
    return value


def package(directory, *, private, capability, task_id, prompt, binary, tools):
    """Materialize a fresh exact four-file package; refuse stale/extra files.

    The caller supplies a fixed reviewed prompt template, never a private manifest
    serialization. Binary-intrinsic strings/runtime metadata remain available.
    """
    public = manifest(private, capability=capability, task_id=task_id)
    if hashlib.sha256(binary).hexdigest() != public['binary_sha256']:
        raise ValueError('Public binary differs from declared build')
    raw_tools = json.loads(tools)
    if not isinstance(raw_tools, dict) or not isinstance(raw_tools.get('tools'), list):
        raise ValueError('Invalid public tool catalog')
    files = {'manifest.json': (json.dumps(public, sort_keys=True, indent=2) + '\n').encode(),
             'prompt.txt': prompt.encode('utf-8'), 'target': binary, 'tools.json': tools}
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('Task projection requires a new empty directory')
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        (directory / name).write_bytes(raw)
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}
