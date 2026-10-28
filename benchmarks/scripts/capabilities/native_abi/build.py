"""Separate Merkle original actions for pinned Rust-native / Go-internal ABI."""
import argparse
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime
from grading.c_executor.context import host
from capabilities import build as family_build
from . import images

ROOT = action_cache.ROOT
PREFIX = 'benchmarks/suites/capabilities-v1/sources/native_abi/'
MODULE = 'benchmarks/scripts/capabilities/native_abi/'
SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py', MODULE + 'build_worker.py')
FILES = {'rust': ('interface.rs', 'library.rs', 'slot.rs', 'driver.rs'), 'go': ('program.go',)}
LOADED_SOURCE = Path(__file__).read_bytes()


def settings(language):
    if language == 'rust':
        return ['--edition=2021', '-C', 'opt-level=2', '-C', 'debuginfo=2', '-C', 'panic=abort',
                '-C', 'lto=off', '-C', 'codegen-units=1', '-C', 'relocation-model=static',
                '-C', 'metadata=rea-native-abi-v1', '-C', 'linker=/usr/bin/gcc', '--remap-path-prefix=/work=.']
    if language == 'go':
        return ['-trimpath', '-buildvcs=false', '-ldflags=-buildid=', '-gcflags=command-line-arguments=-S']
    raise ValueError('Unsupported native ABI compiler condition')


def action(language):
    if Path(__file__).read_bytes() != LOADED_SOURCE:
        raise RuntimeError('Loaded native ABI build host changed')
    images.require_loaded()
    flags = settings(language)
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    request = {'language': language, 'files': {name: (ROOT / PREFIX / language / name).read_text()
                                             for name in FILES[language]}, 'flags': flags}
    platform = {'compiler_image': image_manifest.load(language)['id'], 'language': language,
                'binutils_image': family_build.tooling()[0], **host()}
    selected = action_cache.Action('capability_native_language_abi_build_v1',
        {**sources, 'request.json': action_cache.canonical(request)},
        arguments=['closed-native-language-abi-build', language], platform=platform, timeout_seconds=300)
    return selected, request, sources, platform


def build(language, *, cache=None):
    selected, request, sources, platform = action(language)
    local = cache or action_cache.Cache()
    hit = local.lookup(selected)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
    else:
        with runtime.image(language) as (base, pinned), family_build.tools_image() as (tools, identity, manifest), \
             tempfile.TemporaryDirectory(prefix='rea-native-abi-build-', dir='/tmp') as temporary:
            if pinned['id'] != platform['compiler_image'] or identity != platform['binutils_image']:
                raise RuntimeError('Native ABI build runtime changed after action resolution')
            work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
            work.mkdir()
            snapshot.mkdir()
            for name, raw in sources.items():
                destination = snapshot / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            arguments = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
            arguments += ['--ro-bind', str(tools), '/tools', '/usr/bin/python3', '-I', '-S', '-B',
                          '/environment/' + MODULE + 'build_worker.py']
            environment = {**pinned['manifest']['environment'], 'REA_NATIVE_ABI_WORKER': '1',
                'LD_LIBRARY_PATH': ':'.join(sorted({'/tools/' + str(Path(name).parent)
                    for name, item in manifest['files'].items() if item['elf']}))}
            token = runtime._authorized.set(tuple(arguments))
            try:
                reply = process.run(arguments, input=action_cache.canonical(request), timeout=300,
                                    output_limit=64 * 1024**2, env=environment)
            finally:
                runtime._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Isolated native ABI build failed: ' + reply.stderr.decode(errors='replace')[-10000:])
            if action(language)[0].key != selected.key:
                raise RuntimeError('Native ABI build inputs changed during execution')
            result = {**json.loads(reply.stdout), 'runtime': platform}
        images.validate(bytes.fromhex(result['private_hex']), bytes.fromhex(result['public_hex']), language)
        local.commit(selected, {'result.json': action_cache.canonical(result)})
    if result.get('runtime') != platform:
        raise ValueError('Native ABI build cache runtime mismatch')
    images.validate(bytes.fromhex(result['private_hex']), bytes.fromhex(result['public_hex']), language)
    if action(language)[0].key != selected.key:
        raise RuntimeError('Native ABI build inputs changed before returning cached output')
    return {**result, 'build_id': selected.key, 'cache_hit': bool(hit)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=('rust', 'go', 'all'), default='all')
    arguments = parser.parse_args()
    for language in FILES if arguments.language == 'all' else (arguments.language,):
        value = build(language)
        print('NATIVE ABI ORIGINAL:', language, value['build_id'], 'CACHED' if value['cache_hit'] else 'BUILT', flush=True)


if __name__ == '__main__':
    main()
