"""Hint-free L6 public packages and private same-build diagnostic truth."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest
from grading.artifacts import object_fields, digest, integer
from . import build, scoring

ROOT = action_cache.ROOT
CACHE = ROOT / '.cache/benchmark-capabilities'
FILES = ('target', 'prompt.txt', 'manifest.json', 'tools.json')
MODES = {'optimized_recover': 'optimized', 'lto_recover': 'lto'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def contract(declaration):
    profile = declaration['profile']
    if (profile.get('kind') != 'optimized_localization' or profile.get('id') not in MODES
            or profile.get('variant') != MODES[profile['id']]
            or profile.get('artifact_schema') != {'instances.json': 1}
            or type(profile['artifact_schema']['instances.json']) is not int
            or profile.get('output_language') is not None
            or declaration.get('family_id') != 'dispatch_registry'
            or declaration.get('task_id') != 'dispatch_registry_' + profile['id']):
        raise ValueError('Unregistered optimized localization declaration')
    return MODES[profile['id']]


def sources():
    names = {*build.SOURCES, *(build.PREFIX + name for name in build.FILES),
             build.MODULE + 'build.py', build.MODULE + 'tooling.py', build.MODULE + 'tasks.py',
             build.MODULE + 'scoring.py', build.MODULE + 'review.py', build.MODULE + 'dwarf.py',
             build.MODULE + 'artifact.py', build.MODULE + 'reference.py'}
    return {name: sha((ROOT / name).read_bytes()) for name in sorted(names)}


def manifest(target, mode):
    pinned = image_manifest.load('cpp')
    binary = bytes.fromhex(target['public_hex'])
    return {'schema_version': 1, 'family_id': 'dispatch_registry', 'build_id': target['build_id'],
            'task_id': 'task_' + sha(binary + b'optimized_recover')[:20],
            'capability_ids': ['L6'], 'source_language': 'cpp', 'output_language': None,
            'artifact_schema': {'instances.json': 1}, 'binary_sha256': sha(binary),
            'architecture': 'x86_64', 'hint_profile': 'no_entry_optimized_locations_v1',
            'condition': 'optimized_recover',
            'compiler': {'family': 'gcc', 'version': pinned['manifest']['compiler']['version'].splitlines()[0],
                         'image_sha256': pinned['id'], 'standard': 'c++20', 'optimization': 'O3',
                         'lto': mode == 'lto', 'link': 'ELF_ET_EXEC', 'architecture': 'x86_64'},
            'given_facts': {'pointer_bytes': 8, 'receiver_owner_delta': -16,
                            'owner_cookie_offset': 8, 'receiver_value_argument': 'rsi',
                            'scalar_value_argument': 'rdi'},
            'diagnostic_only': True, 'release_accepted': False}


def prompt():
    return (scoring.prompt() + '\nUse only supplied static REA/Ghidra tools. No target execution, shell, original source, '
            'prior answers or compiler feedback. No function entry or matching range is supplied. '
            'Close the binary session when finished. Give a brief evidence explanation, then exactly one '
            'Markdown code block labelled instances.json.\n')


def validate_original(target, mode):
    selected = build.action(mode)[0]
    if target.get('build_id') != selected.key:
        raise ValueError('Optimized localization original build differs')
    local = action_cache.Cache()
    saved = local.lookup(selected)
    if not saved:
        raise ValueError('Optimized localization original action is missing')
    expected = json.loads(local.get(saved['output_files']['result.json']['digest']))
    if set(target) != set(expected) | {'build_id'} or action_cache.canonical(
            {name: target[name] for name in expected}) != action_cache.canonical(expected):
        raise ValueError('Optimized localization target differs from original AC/CAS outputs')


def freeze(declaration, settings, *, publish=True):
    mode = contract(declaration)
    before = sources()
    target = {name: value for name, value in build.build(mode).items() if name != 'cache_hit'}
    validate_original(target, mode)
    metadata = manifest(target, mode)
    files = {'target': bytes.fromhex(target['public_hex']), 'prompt.txt': prompt().encode(),
             'manifest.json': action_cache.canonical(metadata),
             'tools.json': (ROOT / settings['analysis_tools_file']).read_bytes()}
    hashes = {name: sha(raw) for name, raw in sorted(files.items())}
    package = sha(action_cache.canonical(hashes))
    directory = CACHE / 'task-freezes' / package
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pending-', dir=directory.parent) as temporary:
            work = Path(temporary)
            for name, raw in files.items():
                (work / name).write_bytes(raw)
            work.rename(directory)
    if (directory.is_symlink() or {p.name for p in directory.iterdir()} != set(FILES)
            or any((directory / name).is_symlink() or (directory / name).read_bytes() != raw for name, raw in files.items())):
        raise ValueError('Optimized localization public package changed')
    spec = {'schema_version': 1, 'kind': 'optimized_localization', 'condition': 'optimized_recover',
            'mode': mode, 'binary_sha256': metadata['binary_sha256'], 'target': target,
            'truth': scoring.truth(target, mode)}
    raw = action_cache.canonical(spec)
    spec_id = sha(raw)
    path = CACHE / 'grade-specs' / (spec_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    if path.is_symlink() or path.read_bytes() != raw or sources() != before:
        raise ValueError('Optimized localization grading inputs changed')
    record = {'schema_version': 1, 'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': 'dispatch_registry', 'lineage_id': 'optimized_mix_locations_v1',
        'build_id': target['build_id'], 'capability_ids': ['L6'], 'source_language': 'cpp', 'output_language': None,
        'artifact_schema': {'instances.json': 1}, 'kind': 'optimized_localization', 'variant': mode,
        'profile_id': declaration['profile']['id'], 'hint_profile': metadata['hint_profile'],
        'binary_sha256': metadata['binary_sha256'], 'public_package_sha256': package,
        'public_directory': str(directory.relative_to(ROOT)), 'public_files_sha256': hashes,
        'grading_spec': str(path.relative_to(ROOT)), 'grading_spec_sha256': spec_id,
        'source_inputs_sha256': before}
    if publish:
        from capabilities.tasks import publish_index
        publish_index(record)
    return record


def validate_frozen(value, declaration, settings):
    mode = contract(declaration)
    integer(value.get('schema_version'), 1, 1)
    for name, expected in {'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': 'dispatch_registry', 'lineage_id': 'optimized_mix_locations_v1',
        'kind': 'optimized_localization', 'profile_id': declaration['profile']['id'], 'variant': mode,
        'capability_ids': ['L6'], 'source_language': 'cpp', 'output_language': None,
        'artifact_schema': {'instances.json': 1}, 'hint_profile': 'no_entry_optimized_locations_v1'}.items():
        if action_cache.canonical(value.get(name)) != action_cache.canonical(expected):
            raise ValueError('Optimized localization frozen contract differs: ' + name)
    package, spec_id = value['public_package_sha256'], value['grading_spec_sha256']
    digest(package)
    digest(spec_id)
    directory = ROOT.joinpath(*action_cache.relative(value['public_directory']).parts)
    spec_path = ROOT.joinpath(*action_cache.relative(value['grading_spec']).parts)
    if (directory.is_symlink() or directory.resolve() != (CACHE / 'task-freezes' / package).resolve()
            or {p.name for p in directory.iterdir()} != set(FILES)
            or any((directory / name).is_symlink() for name in FILES)
            or spec_path.is_symlink() or spec_path.resolve() != (CACHE / 'grade-specs' / (spec_id + '.json')).resolve()):
        raise ValueError('Optimized localization package escapes its exact boundary')
    files = {name: (directory / name).read_bytes() for name in FILES}
    hashes = {name: sha(raw) for name, raw in sorted(files.items())}
    spec_raw = spec_path.read_bytes()
    if hashes != value['public_files_sha256'] or sha(action_cache.canonical(hashes)) != package or sha(spec_raw) != spec_id:
        raise ValueError('Optimized localization immutable inputs changed')
    recorded_sources = value.get('source_inputs_sha256')
    if not isinstance(recorded_sources, dict) or not recorded_sources:
        raise ValueError('Invalid optimized localization source input manifest')
    for name, source_digest in recorded_sources.items():
        action_cache.relative(name)
        digest(source_digest)
    if recorded_sources != sources():
        raise RuntimeError('Optimized localization source inputs changed; prepare the selected task first')
    spec = json.loads(spec_raw)
    object_fields(spec, ('schema_version', 'kind', 'condition', 'mode', 'binary_sha256', 'target', 'truth'))
    integer(spec['schema_version'], 1, 1)
    if spec['kind'] != 'optimized_localization' or spec['condition'] != 'optimized_recover' or spec['mode'] != mode:
        raise ValueError('Optimized localization private contract differs')
    validate_original(spec['target'], mode)
    expected = manifest(spec['target'], mode)
    if (action_cache.canonical(json.loads(files['manifest.json'])) != action_cache.canonical(expected)
            or value['build_id'] != expected['build_id'] or value['binary_sha256'] != expected['binary_sha256']
            or spec['binary_sha256'] != expected['binary_sha256'] or sha(files['target']) != expected['binary_sha256']
            or action_cache.canonical(spec['truth']) != action_cache.canonical(scoring.truth(spec['target'], mode))
            or files['prompt.txt'] != prompt().encode()
            or files['tools.json'] != (ROOT / settings['analysis_tools_file']).read_bytes()):
        raise ValueError('Optimized localization truth or public premises differ from the original')
    return value
