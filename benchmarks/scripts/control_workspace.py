"""Private offline-control snapshots; selected caches retain normal Cache semantics."""
import hashlib
import json
from pathlib import Path
import re
import shutil

from parallel_controls import canonical, relative, write_json


# Compiler/executor/preparation actions only. Never seed inference or submissions.
ACTION_KINDS = frozenset(('c_compile', 'c_execute', 'capability_go_buffer_execute_v1', 'capability_prepare_v1',
    'capability_optimized_location_grade_v1', 'capability_abi_grade_v1',
    'capability_pointer_probe_v1', 'capability_abi_original_v1',
    'capability_cpp_buffer_grade_v1', 'capability_c_buffer_grade_v1',
    'capability_target_build_v1', 'capability_pointer_grade_v1',
    'capability_rust_buffer_compile_v1', 'capability_baseline_reference_v1',
    'capability_abi_probe_compile_v1', 'capability_baseline_target_build_v1',
    'capability_original_reference_v1', 'capability_optimized_location_build_v1',
    'capability_abi_original_native_v1', 'capability_optimized_location_native_v1',
    'capability_lifecycle_reference_v1', 'capability_go_buffer_compile_v1',
    'capability_native_language_abi_grade_v1', 'capability_pointer_original_v1',
    'capability_indirect_call_original_v1', 'capability_native_language_abi_build_v1',
    'capability_pointer_native_v1', 'capability_variant_object_original_v1',
    'capability_cpp_buffer_compile_v1', 'capability_rust_buffer_grade_v1',
    'capability_go_buffer_grade_v1'))


def copy_file(source, destination):
    """Real copies: worker mutation never changes another worker or the repository."""
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError('Control snapshot requires a regular source file')
    raw = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    destination.chmod(source.stat().st_mode & 0o777)
    return hashlib.sha256(raw).hexdigest()


def copy_tree(source, destination):
    """Copy source trees without following links or carrying Python bytecode."""
    source, destination = Path(source), Path(destination)
    if source.is_symlink():
        raise ValueError('Control source directory must not be a symlink')
    if not source.is_dir():
        raise FileNotFoundError(source)
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir()):
        if child.name in ('__pycache__', '.pytest_cache'):
            continue
        if child.is_symlink():
            raise ValueError('Control source tree contains a symlink')
        if child.is_dir():
            copy_tree(child, destination / child.name)
        elif child.is_file():
            copy_file(child, destination / child.name)
        else:
            raise ValueError('Control source tree contains a special file')


def seed_actions(source, destination):
    """Validate selected AC/CAS closures and copy them into a private default cache."""
    import action_cache as ac
    source, destination = ac.Cache(source), ac.Cache(destination)
    blobs, directories = set(), set()
    counts = {'actions': 0, 'excluded': 0, 'blobs': 0}

    def transfer(digest):
        ac.valid_digest(digest)
        key = digest['hash']
        if key not in blobs:
            raw = source.get(digest)
            if destination.put(raw) != digest:
                raise ValueError('Control CAS transfer changed content')
            blobs.add(key)
        return destination.get(digest)

    def directory(digest):
        if digest['hash'] in directories:
            return
        node = json.loads(transfer(digest))
        if set(node) != {'files', 'directories'}:
            raise ValueError('Invalid control input tree')
        names = set()
        for category in ('files', 'directories'):
            for entry in node[category]:
                name = relative(entry['name'])
                if '/' in name or name in names:
                    raise ValueError('Duplicate or invalid control tree member')
                names.add(name)
                if category == 'files':
                    if set(entry) != {'name', 'digest', 'is_executable'} or type(entry['is_executable']) is not bool:
                        raise ValueError('Invalid control input mode')
                    transfer(entry['digest'])
                else:
                    if set(entry) != {'name', 'digest'}:
                        raise ValueError('Invalid control directory entry')
                    directory(entry['digest'])
        directories.add(digest['hash'])

    if not (source.root / 'ac').is_dir():
        return counts
    for path in sorted((source.root / 'ac').iterdir()):
        if path.is_symlink() or not path.is_file() or not re.fullmatch('[0-9a-f]{64}', path.name):
            raise ValueError('Invalid control action record')
        raw = path.read_bytes()
        record = json.loads(raw)
        action = record['action']
        if action.get('kind') not in ACTION_KINDS:
            counts['excluded'] += 1
            continue
        if (set(record) != {'format', 'action', 'exit_code', 'output_files'}
                or record['format'] != ac.FORMAT or action['format'] != ac.FORMAT
                or record['exit_code'] != 0 or ac.digest(ac.canonical(action))['hash'] != path.name):
            raise ValueError('Invalid control action identity')
        command = json.loads(transfer(action['command_digest']))
        if set(command) != {'arguments', 'environment_variables', 'platform'}:
            raise ValueError('Invalid control command')
        directory(action['input_root_digest'])
        destination.put(ac.canonical(action))
        for name, output in record['output_files'].items():
            relative(name)
            if set(output) != {'digest', 'is_executable'} or type(output['is_executable']) is not bool:
                raise ValueError('Invalid control action output')
            transfer(output['digest'])
        destination._write(destination._path('ac', path.name), raw)
        counts['actions'] += 1
    counts['blobs'] = len(blobs)
    return counts


def copy_sdk(source, destination):
    """SDK payloads are read-only shared inputs; mutable metadata stays private."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir()):
        if child.name in ('worker-state', 'build.lock'):
            continue
        target = destination / child.name
        if child.is_symlink():
            if re.fullmatch('[0-9a-f]{64}', child.name) and child.is_dir():
                target.symlink_to(child.resolve(), target_is_directory=True)
                continue
            raise ValueError('SDK metadata must use admitted regular source paths')
        if child.is_dir():
            if re.fullmatch('[0-9a-f]{64}', child.name):
                target.symlink_to(child.resolve(), target_is_directory=True)
            elif child.name == 'archives':
                target.mkdir()
                for archive in child.iterdir():
                    if not re.fullmatch(r'[0-9a-f]{64}\.tar\.gz', archive.name) or not archive.is_file():
                        raise ValueError('Invalid SDK archive')
                    (target / archive.name).symlink_to(archive.resolve())
            else:
                copy_sdk(child, target)
        elif child.is_file():
            copy_file(child, target)
        else:
            raise ValueError('Invalid SDK snapshot entry')


def snapshot(root, destination):
    """Prepare a sparse offline workspace without model outputs or credentials."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(root):
        raise ValueError('Control snapshot must be a fresh directory outside the checkout')
    destination.mkdir(parents=True)
    for name in ('benchmarks/scripts', 'benchmarks/harnesses',
                 'benchmarks/suites/capabilities-v1', 'bin'):
        copy_tree(root / name, destination / name)
    config = root / 'benchmarks/config'
    for source in sorted(config.glob('*.json')):
        copy_file(source, destination / source.relative_to(root))
    copy_file(root / 'Makefile', destination / 'Makefile')
    # Planning imports these frozen manifests, never their attempt directories.
    for source in sorted((root / 'benchmarks/runs').glob('*/run-manifest.json')):
        copy_file(source, destination / source.relative_to(root))
    legacy = 'benchmarks/runs/zcode-glm53-global-v1/inputs/rust-data-v2'
    for name in ('target', 'prompt.txt', 'tools.json', 'manifest.json'):
        copy_file(root / legacy / name, destination / legacy / name)
    for name in ('task-index', 'task-freezes', 'grade-specs'):
        prefix = '.cache/benchmark-capabilities/' + name
        copy_tree(root / prefix, destination / prefix)
    for prefix in ('.cache/benchmark-grading/v1', '.cache/benchmark-c-executor/v1',
                   '.cache/benchmark-capabilities/tooling-v1',
                   '.cache/benchmark-capabilities/baseline-clang20-tooling-v1',
                   '.cache/benchmark-capabilities/optimized-lto-tooling-v1'):
        copy_sdk(root / prefix, destination / prefix)
    (destination / '.tools').symlink_to((root / '.tools').resolve(), target_is_directory=True)
    (destination / '.cache/benchmark-runtime').mkdir(parents=True)
    prefix = '.cache/benchmark-actions/v3'
    counts = seed_actions(root / prefix, destination / prefix)
    write_json(destination / 'control-snapshot.json', {'schema_version': 1,
        'action_seed': counts, 'provider_launches': 0, 'model_api_calls': 0})
    return counts
