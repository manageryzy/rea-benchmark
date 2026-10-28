"""Optional GCC LTO closure; never refresh the shared C++ compiler image."""
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
CACHE = ROOT / '.cache/benchmark-capabilities/optimized-lto-tooling-v1'


def setup():
    if (CACHE / 'active.json').is_file():
        return record()
    base = image_manifest.load('cpp')
    compiler = Path(base['manifest']['compiler']['command'])
    if image_manifest.file_sha(compiler.resolve()) != base['manifest']['compiler']['executable_sha256']:
        raise ValueError('Installed compiler differs from the pinned C++ image')
    selected = Path(toolchains.command([str(compiler), '-print-prog-name=lto1']))
    if not selected.is_absolute() or not selected.is_file():
        raise RuntimeError('The matching installed GCC lto1 is unavailable')
    builder = toolchains.Builder()
    builder.allow('/usr', '/lib', '/lib64')
    builder.add(selected)
    builder.libraries()
    files = builder.files()
    manifest = {'schema_version': 1, 'base_image_id': base['id'],
                'compiler_executable_sha256': base['manifest']['compiler']['executable_sha256'],
                'lto1': str(selected).lstrip('/'), 'files': files}
    identity = image_manifest.digest(manifest)
    destination = CACHE / identity
    destination.mkdir(parents=True, exist_ok=True)
    for name, item in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(builder.paths[name][0], path)
        path.chmod(item['mode'])
        if image_manifest.file_sha(path) != item['sha256']:
            raise RuntimeError('LTO component changed during capture')
    toolchains.atomic(CACHE / (identity + '.json'), manifest)
    toolchains.atomic(CACHE / 'active.json', {'id': identity})
    return record()


def record():
    if not (CACHE / 'active.json').is_file():
        raise RuntimeError('LTO closure is not pinned; run make setup-optimized-locations')
    identity = json.loads((CACHE / 'active.json').read_bytes())['id']
    action_cache.valid_digest({'hash': identity, 'size_bytes': 0})
    manifest = json.loads((CACHE / (identity + '.json')).read_bytes())
    if image_manifest.digest(manifest) != identity or manifest.get('schema_version') != 1:
        raise ValueError('LTO closure manifest changed')
    base = image_manifest.load('cpp')
    if (manifest['base_image_id'] != base['id']
            or manifest['compiler_executable_sha256'] != base['manifest']['compiler']['executable_sha256']
            or manifest['lto1'] not in manifest['files']):
        raise ValueError('LTO closure has a different compiler base')
    for name in manifest['files']:
        image_manifest.relative(name)
    return {'id': identity, 'manifest': manifest}


@contextmanager
def image():
    """Copy the admitted base before adding tools; preserve its inode metadata."""
    selected = record()
    addon, identity = selected['manifest'], selected['id']
    with runtime.image('cpp') as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-lto-image-', dir='/tmp') as temporary:
        if pinned['id'] != addon['base_image_id']:
            raise RuntimeError('Compiler changed after LTO identity resolution')
        root = Path(temporary) / 'root'
        # Hard links would change the base inodes' ctime and weaken its drift
        # guard. Only this private composite copy receives additional files.
        shutil.copytree(base, root)
        combined = copy.deepcopy(pinned['manifest'])
        source = CACHE / identity
        actual = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file() or p.is_symlink()}
        if actual != set(addon['files']):
            raise ValueError('Unexpected LTO closure files')
        for name, item in addon['files'].items():
            original = source / name
            if (original.is_symlink() or not original.is_file() or original.stat().st_size != item['bytes']
                    or image_manifest.file_sha(original) != item['sha256']):
                raise ValueError('LTO closure file changed')
            if name in combined['files']:
                existing = combined['files'][name]
                if any(existing[key] != item[key] for key in ('sha256', 'bytes', 'mode')):
                    raise ValueError('LTO closure conflicts with the pinned compiler')
            else:
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, destination)
                destination.chmod(item['mode'])
                if image_manifest.file_sha(destination) != item['sha256']:
                    raise ValueError('LTO component changed while copying')
                combined['files'][name] = item
        actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
        if actual != set(combined['files']):
            raise ValueError('Composite LTO runtime has an unexpected file inventory')
        def verify(item):
            name, expected = item
            path = root / name
            if (path.is_symlink() or path.stat().st_size != expected['bytes']
                    or path.stat().st_mode & 0o777 != expected['mode']
                    or image_manifest.file_sha(path) != expected['sha256']):
                raise ValueError('Composite LTO runtime content differs from its pins')
        with ThreadPoolExecutor(max_workers=16) as workers:
            list(workers.map(verify, combined['files'].items()))
        before = runtime.image_signature(root)
        yield root, {'base_image_id': pinned['id'], 'lto_image_id': identity, 'manifest': combined}
        if runtime.image_signature(root) != before or record()['id'] != identity:
            raise RuntimeError('Composite LTO runtime changed during execution')
