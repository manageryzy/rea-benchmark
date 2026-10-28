"""Read-only preparation state and a separate Merkle action for frozen tasks.

Preparation may compile trusted originals. It never prepares credentials or
launches a model. Its outputs are immutable public packages/private specs plus
a separately selected task index; none of this changes inference identity.
"""
import hashlib
import json
from pathlib import Path
import sys

import action_cache
from grading import image_manifest
from grading.c_executor.context import host
from . import build, buffers, config, tasks

ROOT = action_cache.ROOT
FILES = ('target', 'prompt.txt', 'tools.json', 'manifest.json')
COMMON = ('benchmarks/scripts/capabilities/__init__.py',
    'benchmarks/scripts/capabilities/preparation.py', 'benchmarks/scripts/capabilities/config.py',
    'benchmarks/scripts/capabilities/tasks.py', 'benchmarks/scripts/capabilities/public.py',
    'benchmarks/scripts/capabilities/elf.py', 'benchmarks/scripts/grading/artifacts.py')


def action(declaration):
    """Hash the selected preparation inputs, excluding run/pricing/report settings."""
    settings, _ = config.load()
    registered = next((row for row in tasks.declared() if row['task_id'] == declaration.get('task_id')), None)
    if registered != declaration:
        raise ValueError('Preparation requires an exact registered declaration')
    profile = declaration['profile']
    selected = {key: declaration[key] for key in ('task_id', 'family_id', 'absent')}
    if 'convention' in declaration:
        selected['convention'] = declaration['convention']
    selected['profile'] = {key: profile[key] for key in
        ('id', 'kind', 'output_language', 'artifact_schema', 'condition', 'variant', 'original_compiler') if key in profile}
    names = set(COMMON) | set(build.SOURCES)
    names.add('benchmarks/scripts/capabilities/build.py')
    names.add('benchmarks/scripts/grading/image_manifest.py')
    if profile['kind'] == 'matched_buffer':
        from .baselines import tasks as selected_tasks
        names.difference_update(build.SOURCES)
        names.update(selected_tasks.selected_sources(profile['original_compiler'],
                     profile['output_language'], declaration['family_id']))
    elif profile['kind'] == 'optimized_localization':
        from .optimized_locations import tasks as selected_tasks
        names.update(selected_tasks.sources())
    elif profile['kind'] == 'native_abi':
        from .native_abi import tasks as selected_tasks
        names.difference_update(build.SOURCES)
        names.add('benchmarks/scripts/grading/c_executor/context.py')
        names.update(selected_tasks.sources(profile['variant']))
    elif profile['kind'] == 'indirect_call':
        from .indirect_calls import runtime
        names.update(runtime.sources('original', profile['variant']))
        names.add(runtime.engine.HOST_SOURCE)
        names.update(('benchmarks/scripts/capabilities/indirect_calls/tasks.py',
                      'benchmarks/scripts/capabilities/indirect_calls/runtime.py',
                      'benchmarks/scripts/capabilities/indirect_calls/contracts.py',
                      'benchmarks/config/indirect-call-probes.json'))
    elif profile['kind'] == 'variant_object':
        from .variant_objects import runtime
        names.update(runtime.sources('original', profile['variant']))
        names.add(runtime.engine.HOST_SOURCE)
        names.update(('benchmarks/scripts/capabilities/variant_objects/tasks.py',
                      'benchmarks/scripts/capabilities/variant_objects/runtime.py',
                      'benchmarks/scripts/capabilities/variant_objects/contracts.py',
                      'benchmarks/config/variant-object-probes.json'))
    elif profile['kind'] == 'pointer_probe':
        from .pointers import runtime
        names.update(runtime.sources('original', profile['variant']))
        names.update(('benchmarks/scripts/capabilities/pointers/tasks.py',
                      'benchmarks/scripts/capabilities/pointers/runtime.py',
                      'benchmarks/scripts/capabilities/pointers/contracts.py', 'benchmarks/config/pointer-probes.json'))
    elif profile['kind'] == 'abi_probe':
        from .abi import runtime
        names.update(runtime.sources('original', declaration['family_id'], profile.get('variant')))
        names.update(('benchmarks/scripts/capabilities/abi/tasks.py',
            'benchmarks/scripts/capabilities/abi/runtime.py', 'benchmarks/scripts/capabilities/abi/contracts.py',
            'benchmarks/config/abi-probes.json'))
        if profile.get('variant'):
            from .abi import variants
            names.update(('benchmarks/scripts/capabilities/abi/variants.py', str(variants.CATALOG.relative_to(ROOT))))
    elif profile['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
        from . import lifecycle
        names.update(('benchmarks/scripts/capabilities/lifecycle_tasks.py',
            'benchmarks/scripts/capabilities/lifecycle.py', 'benchmarks/scripts/capabilities/lifecycle_build.py',
            'benchmarks/suites/capabilities-v1/sources/lifecycle/common.hpp',
            'benchmarks/suites/capabilities-v1/sources/lifecycle/' + declaration['family_id'] + '.cpp'))
        names.update(lifecycle.REFERENCE_SOURCES)
        if profile['kind'] == 'cpp_lifecycle':
            names.add('benchmarks/scripts/capabilities/cpp_lifecycle/contract.hpp')
    else:
        names.update(('benchmarks/scripts/capabilities/locations.py', settings['families_file'],
            'benchmarks/suites/capabilities-v1/sources/common.hpp',
            'benchmarks/suites/capabilities-v1/sources/' + declaration['family_id'] + '.cpp'))
        if profile['kind'] != 'localization':
            names.add('benchmarks/scripts/capabilities/buffers.py')
            names.update(buffers.REFERENCE_SOURCES)
        if profile['kind'] == 'cpp_buffer':
            names.update(('benchmarks/scripts/capabilities/cpp_buffer/runtime.py',
                          'benchmarks/scripts/capabilities/cpp_buffer/contract.hpp'))
    names.add(settings['analysis_tools_file'])
    inputs = {name: (ROOT / name).read_bytes() for name in sorted(names)}
    inputs['preparation/declaration.json'] = action_cache.canonical(selected)
    platform = {'compiler_image': image_manifest.load('cpp')['id'], 'binutils_image': build.tooling()[0],
        'python_version': sys.version, 'python_sha256': hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),
        **host()}
    if profile['kind'] == 'matched_buffer':
        from .baselines import build as matched_build
        original = profile['original_compiler']
        platform['compiler_image'] = image_manifest.load('cpp' if original == 'clang20' else original)['id']
        if original == 'clang20':
            from .baselines import tooling
            platform['compiler_extension'] = tooling.record()['id']
    elif profile['kind'] == 'native_abi':
        platform['compiler_image'] = image_manifest.load(profile['variant'])['id']
    elif profile['kind'] == 'optimized_localization' and profile['variant'] == 'lto':
        from .optimized_locations import tooling
        platform['lto_image_id'] = tooling.record()['id']
    return action_cache.Action('capability_prepare_v1', inputs, arguments=['freeze', profile['kind']], platform=platform)


def _contract(value, declaration):
    profile = declaration['profile']
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('task_id') != declaration['task_id'] or value.get('id') != declaration['task_id']
            or value.get('family_id') != declaration['family_id'] or value.get('profile_id') != profile['id']
            or value.get('kind') != profile['kind'] or value.get('artifact_schema') != profile['artifact_schema']
            or value.get('output_language') != profile['output_language']):
        raise ValueError('Cached preparation differs from its declared case')
    for key in ('public_package_sha256', 'grading_spec_sha256'):
        action_cache.valid_digest({'hash': value[key], 'size_bytes': 0})
    relative = str(tasks.CACHE.relative_to(ROOT))
    if (value['public_directory'] != relative + '/task-freezes/' + value['public_package_sha256']
            or value['grading_spec'] != relative + '/grade-specs/' + value['grading_spec_sha256'] + '.json'
            or set(value['public_files_sha256']) != set(FILES)):
        raise ValueError('Cached preparation escapes its immutable package/spec boundary')
    if tasks.sha(action_cache.canonical(value['public_files_sha256'])) != value['public_package_sha256']:
        raise ValueError('Cached preparation has an inconsistent public identity')
    return value


def _validate(value, declaration):
    _contract(value, declaration)
    tasks.validate_frozen(value, declaration)
    from . import store
    store.spec({**value, 'track': 'capability', 'artifact_contract_version': 1})
    return value


def _outputs(value):
    return {'record.json': action_cache.canonical(value),
        **{'public/' + name: (ROOT / value['public_directory'] / name).read_bytes() for name in FILES},
        'private/grading.json': (ROOT / value['grading_spec']).read_bytes()}


def _read(local, hit, declaration):
    expected = {'record.json', 'private/grading.json', *('public/' + name for name in FILES)}
    if set(hit['output_files']) != expected:
        raise ValueError('Cached preparation contains unapproved outputs')
    files = {name: local.get(item['digest']) for name, item in hit['output_files'].items()}
    value = _contract(json.loads(files['record.json']), declaration)
    if (files['record.json'] != action_cache.canonical(value)
            or tasks.sha(files['private/grading.json']) != value['grading_spec_sha256']
            or {name: tasks.sha(files['public/' + name]) for name in FILES} != value['public_files_sha256']):
        raise ValueError('Cached preparation output content differs from its record')
    return value, files


def inspect(declaration, *, cache=None):
    """Inventory one declared case, including missing/stale/invalid preparations."""
    local = cache or action_cache.Cache()
    try:
        selected = action(declaration)
        # Inspect existing bytes even on a miss: corruption is not an excuse to
        # overwrite a historical package or manufacture a new model attempt.
        # frozen() already validates the public package and original binding.
        # Keep the preparation/output and private-spec checks without repeating
        # the full original/toolchain admission a second time.
        value = _contract(tasks.frozen(declaration['task_id']), declaration)
        from . import store
        store.spec({**value, 'track': 'capability', 'artifact_contract_version': 1})
        hit = local.lookup(selected)
        if not hit:
            return {'status': 'pending', 'reason': 'preparation_inputs_changed_or_not_recorded'}
        expected, _ = _read(local, hit, declaration)
        if expected != value:
            return {'status': 'pending', 'reason': 'preparation_selection_changed'}
        return {'status': 'ready', 'case': value, 'action_key': selected.key}
    except (RuntimeError, FileNotFoundError) as error:
        return {'status': 'pending', 'reason': str(error)[:512]}
    except (ValueError, KeyError, TypeError, OSError) as error:
        return {'status': 'invalid', 'reason': str(error)[:512]}


def _restore(value, files):
    destinations = {ROOT / value['grading_spec']: files['private/grading.json']}
    destinations.update({ROOT / value['public_directory'] / name: files['public/' + name] for name in FILES})
    for destination, raw in destinations.items():
        if destination.is_symlink() or not destination.resolve().is_relative_to(tasks.CACHE.resolve()):
            raise ValueError('Preparation output path is a symlink or escapes its cache')
        for parent in destination.parents:
            if parent == tasks.CACHE:
                break
            if parent.is_symlink():
                raise ValueError('Preparation output parent is a symlink')
        action_cache.Cache._write(destination, raw)


def prepare(declaration, *, cache=None):
    """Build/restore exactly one trusted preparation, then publish its index."""
    local = cache or action_cache.Cache()
    selected = action(declaration)
    index = tasks.CACHE / 'task-index' / (declaration['task_id'] + '.json')
    if index.is_symlink():
        raise ValueError('Capability task index is a symlink')
    hit = local.lookup(selected)
    if hit:
        value, files = _read(local, hit, declaration)
        _restore(value, files)
    else:
        value = tasks.freeze_one(declaration, publish=False)
        _validate(value, declaration)
        files = _outputs(value)
    _validate(value, declaration)
    if action(declaration).key != selected.key:
        raise RuntimeError('Capability preparation inputs changed before publication')
    if not hit:
        local.commit(selected, files)
    tasks.publish_index(value)
    return value
