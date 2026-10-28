"""Content-addressed original program-family builds, entirely offline after setup."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import action_cache
from grading import image_manifest, process, runtime, toolchains

ROOT = action_cache.ROOT
SUITE = ROOT / 'benchmarks/suites/capabilities-v1'
TOOLS = ROOT / '.cache/benchmark-capabilities/tooling-v1'
SOURCES = ['benchmarks/scripts/capabilities/build_worker.py', 'benchmarks/scripts/grading/__init__.py',
           'benchmarks/scripts/grading/process.py', 'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py',
           'benchmarks/scripts/grading/image_manifest.py', 'benchmarks/scripts/grading/c_executor/context.py']


def setup_tools():
    """Capture only installed, trusted strip/objdump and their loaded libraries.

    Explicit setup pins the selection. Builds never refresh from the host.
    """
    if (TOOLS / 'active.json').is_file():
        return json.loads((TOOLS / 'active.json').read_bytes())
    builder = toolchains.Builder()
    builder.allow('/usr', '/lib', '/lib64')
    for name in ('strip', 'objdump'):
        builder.add('/usr/bin/' + name, 'usr/bin/' + name)
    builder.libraries()
    files = builder.files()
    manifest = {'schema_version': 1, 'files': files}
    identity = image_manifest.digest(manifest)
    directory = TOOLS / identity
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(builder.paths[name][0], destination)
        destination.chmod(value['mode'])
        if image_manifest.file_sha(destination) != value['sha256']:
            raise ValueError('Binutils changed while pinning')
    toolchains.atomic(TOOLS / (identity + '.json'), manifest)
    record = {'id': identity}
    toolchains.atomic(TOOLS / 'active.json', record)
    return record


def tooling():
    if not (TOOLS / 'active.json').exists():
        raise RuntimeError('Binutils addon not pinned; use make setup-capabilities')
    identity = json.loads((TOOLS / 'active.json').read_bytes())['id']
    action_cache.valid_digest({'hash': identity, 'size_bytes': 0})
    manifest = json.loads((TOOLS / (identity + '.json')).read_bytes())
    if image_manifest.digest(manifest) != identity or manifest.get('schema_version') != 1:
        raise ValueError('Binutils addon manifest mismatch')
    return identity, manifest


@contextmanager
def tools_image():
    identity, manifest = tooling()
    with tempfile.TemporaryDirectory(prefix='rea-target-tools-', dir='/tmp') as temporary:
        root = Path(temporary)
        expected = set(manifest['files'])
        original = TOOLS / identity
        actual = {str(path.relative_to(original)) for path in original.rglob('*') if path.is_file() or path.is_symlink()}
        if expected != actual:
            raise ValueError('Unexpected target build-tool files')
        for name, item in manifest['files'].items():
            image_manifest.relative(name)
            path = original / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size != item['bytes']:
                raise ValueError('Invalid build-tool file')
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            if image_manifest.file_sha(destination) != item['sha256']:
                raise ValueError('Build-tool content mismatch')
            destination.chmod(item['mode'])
        signature = runtime.image_signature(root)
        yield root, identity, manifest
        if runtime.image_signature(root) != signature:
            raise ValueError('Build tools changed during target compilation')


def build(family_id, *, absent=False, pie=False, cache=None):
    config = json.loads((SUITE / 'config/families.json').read_bytes())
    family = next((row for row in config['families'] if row['id'] == family_id), None)
    if family is None:
        raise ValueError('Unregistered family')
    record = image_manifest.load('cpp')
    source, header = (SUITE / ('sources/' + family_id + '.cpp')).read_bytes(), (SUITE / 'sources/common.hpp').read_bytes()
    request = {'source': source.decode(), 'header': header.decode(), 'absent': absent, 'pie': pie}
    frozen = {name: (ROOT / name).read_bytes() for name in SOURCES}
    inputs = {**frozen, 'target/request.json': action_cache.canonical(request)}
    from grading.c_executor.context import host
    environment = {'compiler_image': record['id'], 'binutils_image': tooling()[0], **host()}
    action = action_cache.Action('capability_target_build_v1', inputs, arguments=['cpp', '-O2', '-std=c++20'], platform=environment, timeout_seconds=150)
    cache = cache or action_cache.Cache()
    hit = cache.lookup(action)
    if hit:
        result = json.loads(cache.get(hit['output_files']['result.json']['digest']))
        return {**result, 'build_id': action.key, 'cache_hit': True}
    with runtime.image('cpp') as (base, pinned), tools_image() as (tools, identity, manifest), tempfile.TemporaryDirectory(prefix='rea-family-', dir='/tmp') as temporary:
        if pinned['id'] != environment['compiler_image'] or identity != environment['binutils_image']:
            raise ValueError('Target build environment changed after action hashing')
        temporary = Path(temporary)
        work, snapshot = temporary / 'work', temporary / 'snapshot'
        work.mkdir()
        snapshot.mkdir()
        for name, raw in frozen.items():
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        arguments = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
        arguments += ['--ro-bind', str(tools), '/tools']
        env = {**pinned['manifest']['environment'], 'REA_CAPABILITY_BUILD': '1',
               'LD_LIBRARY_PATH': ':'.join(sorted({'/tools/' + str(Path(name).parent) for name, item in manifest['files'].items() if item['elf']}))}
        arguments += ['/usr/bin/python3', '-I', '-S', '-B', '/environment/benchmarks/scripts/capabilities/build_worker.py']
        token = runtime._authorized.set(tuple(arguments))
        try:
            reply = process.run(arguments, input=action_cache.canonical(request), timeout=150, output_limit=32 * 1024**2, env=env)
        finally:
            runtime._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Isolated family build failed: ' + reply.stderr.decode(errors='replace'))
        if frozen != {name: (ROOT / name).read_bytes() for name in SOURCES}:
            raise ValueError('Target builder sources changed during compilation')
        result = json.loads(reply.stdout)
    cache.commit(action, {'result.json': action_cache.canonical(result)})
    return {**result, 'build_id': action.key, 'cache_hit': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', action='store_true')
    args = parser.parse_args()
    if args.setup:
        print('Pinned target build tools:', setup_tools()['id'])
        return
    config = json.loads((SUITE / 'config/families.json').read_bytes())
    for row in config['families']:
        result = build(row['id'])
        print(row['id'], result['build_id'], 'CACHED' if result['cache_hit'] else 'BUILT')


if __name__ == '__main__':
    main()
