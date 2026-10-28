"""Pinned Clang 20 extension of a private copy of the existing C++ image."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import shutil
import tempfile

import action_cache
from grading import image_manifest, runtime, toolchains

ROOT = action_cache.ROOT
CACHE = ROOT / '.cache/benchmark-capabilities/baseline-clang20-tooling-v1'
COMPILER = '/usr/bin/clang++-20'
COMPOSITION = 'private-copy-declared-compression-library-overlay-v1'
ALLOWED_OVERRIDES = {'usr/lib/x86_64-linux-gnu/liblzma.so.5'}
LOADED = Path(__file__).read_bytes()


def setup(*, refresh=False):
    if (CACHE / 'active.json').exists() and not refresh:
        return record()
    base = image_manifest.load('cpp')
    version = toolchains.command([COMPILER, '--version']).splitlines()[0]
    if 'clang version 20.' not in version:
        raise RuntimeError('The baseline requires installed Clang 20')
    resource = Path(toolchains.command([COMPILER, '-print-resource-dir']))
    if not resource.is_absolute() or not resource.is_dir() or not resource.is_relative_to('/usr'):
        raise ValueError('Clang resource directory is outside the reviewed system root')
    builder = toolchains.Builder()
    builder.allow('/usr', '/lib', '/lib64')
    builder.add(COMPILER)
    builder.add(resource)
    builder.libraries()
    files = builder.files()
    overrides = {}
    for name in set(files) & set(base['manifest']['files']):
        original, extension = base['manifest']['files'][name], files[name]
        if any(original[key] != extension[key] for key in ('sha256', 'bytes', 'mode')):
            if name not in ALLOWED_OVERRIDES or not original['elf'] or not extension['elf']:
                raise ValueError('Unreviewed Clang/base runtime conflict: ' + name)
            overrides[name] = {'base_sha256': original['sha256'], 'extension_sha256': extension['sha256']}
    compiler = COMPILER.removeprefix('/')
    manifest = {'schema_version': 1, 'base_image_id': base['id'],
                'compiler': {'command': COMPILER, 'version': version,
                             'executable_sha256': files[compiler]['sha256']},
                'resource_directory': str(resource), 'files': files,
                'composition': COMPOSITION, 'overrides': overrides}
    identity = image_manifest.digest(manifest)
    destination = CACHE / identity
    destination.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(builder.paths[name][0], path)
        path.chmod(value['mode'])
        if image_manifest.file_sha(path) != value['sha256']:
            raise RuntimeError('Clang component changed during capture')
    toolchains.atomic(CACHE / (identity + '.json'), manifest)
    active = CACHE / 'active.json'
    with tempfile.NamedTemporaryFile(dir=CACHE, prefix='active-', delete=False) as stream:
        stream.write(image_manifest.canonical({'id': identity}))
        pending = Path(stream.name)
    pending.replace(active)
    return record()


def record():
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded Clang extension host changed')
    if not (CACHE / 'active.json').is_file():
        raise RuntimeError('Clang extension is not pinned; prepare the baseline toolchain first')
    identity = json.loads((CACHE / 'active.json').read_bytes())['id']
    action_cache.valid_digest({'hash': identity, 'size_bytes': 0})
    manifest = json.loads((CACHE / (identity + '.json')).read_bytes())
    compiler = manifest['compiler']
    name = str(image_manifest.relative(compiler['command'].removeprefix('/')))
    if (image_manifest.digest(manifest) != identity or manifest.get('schema_version') != 1
            or manifest.get('composition') != COMPOSITION or set(manifest.get('overrides', {})) - ALLOWED_OVERRIDES
            or manifest['base_image_id'] != image_manifest.load('cpp')['id']
            or compiler['command'] != COMPILER or 'clang version 20.' not in compiler['version']
            or manifest['files'][name]['sha256'] != compiler['executable_sha256']):
        raise ValueError('Clang extension/compiler binding differs')
    base = image_manifest.load('cpp')['manifest']['files']
    expected = {name: {'base_sha256': base[name]['sha256'], 'extension_sha256': item['sha256']}
                for name, item in manifest['files'].items() if name in base
                and any(base[name][key] != item[key] for key in ('sha256', 'bytes', 'mode'))}
    if manifest['overrides'] != expected:
        raise ValueError('Clang composite override inventory differs')
    for name in manifest['files']: image_manifest.relative(name)
    return {'id': identity, 'manifest': manifest}


@contextmanager
def image():
    selected = record()
    addon = selected['manifest']
    with runtime.image('cpp') as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-clang-baseline-', dir='/tmp') as temporary:
        if pinned['id'] != addon['base_image_id']:
            raise RuntimeError('Clang base image changed before materialization')
        root = Path(temporary) / 'root'
        # A separate copy preserves the pinned base's bytes and inode metadata.
        shutil.copytree(base, root)
        manifest = copy.deepcopy(pinned['manifest'])
        source = CACHE / selected['id']
        actual = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file() or p.is_symlink()}
        if actual != set(addon['files']):
            raise ValueError('Clang extension file inventory changed')
        for name, value in addon['files'].items():
            original = source / name
            if (original.is_symlink() or not original.is_file() or original.stat().st_size != value['bytes']
                    or original.stat().st_mode & 0o777 != value['mode'] or image_manifest.file_sha(original) != value['sha256']):
                raise ValueError('Clang extension content changed')
            if name in manifest['files']:
                if any(manifest['files'][name][key] != value[key] for key in ('sha256', 'bytes', 'mode')):
                    if name not in addon['overrides']:
                        raise ValueError('Clang extension conflicts with the pinned base')
                    destination = root / name
                    destination.chmod(0o600)
                    shutil.copyfile(original, destination)
                    destination.chmod(value['mode'])
                    manifest['files'][name] = value
            else:
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, destination)
                destination.chmod(value['mode'])
                if image_manifest.file_sha(destination) != value['sha256']:
                    raise ValueError('Clang component changed while copying')
                manifest['files'][name] = value
        actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
        if actual != set(manifest['files']):
            raise ValueError('Unexpected composite Clang image contents')
        def verify(item):
            name, expected = item
            path = root / name
            if (path.is_symlink() or path.stat().st_size != expected['bytes']
                    or path.stat().st_mode & 0o777 != expected['mode']
                    or image_manifest.file_sha(path) != expected['sha256']):
                raise ValueError('Composite Clang image differs from its declared content')
        with ThreadPoolExecutor(max_workers=16) as workers:
            list(workers.map(verify, manifest['files'].items()))
        before = runtime.image_signature(root)
        yield root, {'id': pinned['id'], 'extension_id': selected['id'], 'manifest': manifest}
        if runtime.image_signature(root) != before or record()['id'] != selected['id']:
            raise RuntimeError('Clang composite image changed during execution')
