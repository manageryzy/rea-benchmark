"""Separate matched-baseline original actions; existing task identities untouched."""
import argparse
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime
from grading.c_executor.context import host
from capabilities import build as family_build, elf
from capabilities.native_abi import images
from . import tooling

ROOT = action_cache.ROOT
MODULE = 'benchmarks/scripts/capabilities/baselines/'
PREFIX = 'benchmarks/suites/capabilities-v1/sources/baselines/'
FAMILIES = ('record_codec', 'dispatch_registry', 'numeric_aggregation', 'variable_objects', 'callback_policy', 'session_state')
COMPILERS = ('clang20', 'rust', 'go')
SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py', MODULE + 'build_worker.py')
LOADED = Path(__file__).read_bytes()


def flags(compiler):
    if compiler == 'clang20':
        resource = tooling.record()['manifest']['resource_directory']
        return ['-std=c++20', '-O2', '-g', '-gdwarf-4', '-fno-pie', '-no-pie', '-Wl,--build-id=none',
                '--gcc-toolchain=/usr', '-resource-dir=' + resource]
    if compiler == 'rust':
        return ['--edition=2021', '-C', 'opt-level=2', '-C', 'debuginfo=2', '-C', 'panic=abort',
                '-C', 'lto=off', '-C', 'codegen-units=1', '-C', 'relocation-model=static',
                '-C', 'metadata=rea-baselines-v1', '-C', 'linker=/usr/bin/gcc', '--remap-path-prefix=/work=.']
    if compiler == 'go':
        return ['-trimpath', '-buildvcs=false', '-ldflags=-buildid=', '-gcflags=command-line-arguments=-S']
    raise ValueError('Unsupported baseline compiler')


def inputs(compiler, family):
    if compiler not in COMPILERS or family not in FAMILIES:
        raise ValueError('Unregistered matched baseline')
    if compiler == 'clang20':
        return {'family.cpp': ROOT / 'benchmarks/suites/capabilities-v1/sources' / (family + '.cpp'),
                'common.hpp': ROOT / 'benchmarks/suites/capabilities-v1/sources/common.hpp'}
    extension = 'rs' if compiler == 'rust' else 'go'
    directory = ROOT / PREFIX / compiler
    return {'program.' + extension: directory / ('program.' + extension),
            'family.' + extension: directory / (family + '.' + extension)}


def action(compiler, family):
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded baseline build host changed')
    images.require_loaded()
    files = {name: path.read_text() for name, path in inputs(compiler, family).items()}
    source = {name: (ROOT / name).read_bytes() for name in SOURCES}
    language = 'cpp' if compiler == 'clang20' else compiler
    platform = {'compiler_image': image_manifest.load(language)['id'], 'compiler_id': compiler,
                'binutils_image': family_build.tooling()[0], **host()}
    if compiler == 'clang20': platform['compiler_extension'] = tooling.record()['id']
    request = {'compiler': compiler, 'family': family, 'files': files, 'flags': flags(compiler)}
    selected = action_cache.Action('capability_baseline_target_build_v1',
        {**source, 'request.json': action_cache.canonical(request)},
        arguments=['closed-matched-buffer-baseline', compiler, family], platform=platform, timeout_seconds=300)
    return selected, request, source, platform


def validate(result, compiler):
    private, public = bytes.fromhex(result['private_hex']), bytes.fromhex(result['public_hex'])
    if compiler == 'clang20':
        original, stripped = elf.stripped_matches(private, public)
        entry = original.symbols()['family_buffer']
    else:
        original, stripped = images.validate(private, public, compiler)
        name = 'family_buffer' if compiler == 'rust' else 'main.familyBuffer'
        entry = images.selected_symbols(original, (name,))[name]
    if entry['type'] != 2 or not 1 <= entry['size'] <= 262144:
        raise ValueError('Matched baseline has no bounded callable buffer endpoint')
    return entry


def build(compiler, family, *, cache=None):
    selected, request, source, platform = action(compiler, family)
    local = cache or action_cache.Cache()
    hit = local.lookup(selected)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
    else:
        image = tooling.image() if compiler == 'clang20' else runtime.image(compiler)
        with image as (base, pinned), family_build.tools_image() as (tools, identity, manifest), \
             tempfile.TemporaryDirectory(prefix='rea-baseline-build-', dir='/tmp') as temporary:
            if (pinned['id'] != platform['compiler_image'] or identity != platform['binutils_image']
                    or compiler == 'clang20' and pinned['extension_id'] != platform['compiler_extension']):
                raise RuntimeError('Baseline compiler/tool selection changed')
            work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
            work.mkdir()
            snapshot.mkdir()
            for name, raw in source.items():
                path = snapshot / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            command = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
            command += ['--ro-bind', str(tools), '/tools', '/usr/bin/python3', '-I', '-S', '-B', '/environment/' + MODULE + 'build_worker.py']
            libraries = {'/tools/' + str(Path(name).parent) for name, item in manifest['files'].items() if item['elf']}
            libraries.update('/' + str(Path(name).parent) for name, item in pinned['manifest']['files'].items() if item['elf'])
            token = runtime._authorized.set(tuple(command))
            try:
                reply = process.run(command, input=action_cache.canonical(request), timeout=300,
                    output_limit=96 * 1024**2, env={**pinned['manifest']['environment'],
                        'REA_BASELINE_BUILD_WORKER': '1', 'LD_LIBRARY_PATH': ':'.join(sorted(libraries))})
            finally:
                runtime._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Closed baseline build failed: ' + reply.stderr.decode(errors='replace')[-10000:])
            result = {**json.loads(reply.stdout), 'runtime': platform}
        _guard(compiler, family, selected.key)
        validate(result, compiler)
        local.commit(selected, {'result.json': action_cache.canonical(result)})
    _guard(compiler, family, selected.key)
    if result.get('runtime') != platform:
        raise ValueError('Cached baseline runtime differs')
    entry = validate(result, compiler)
    return {**result, 'build_id': selected.key, 'cache_hit': bool(hit), 'buffer_entry': entry['rva']}


def _guard(compiler, family, key):
    if action(compiler, family)[0].key != key:
        raise RuntimeError('Baseline inputs changed before publication')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--refresh', action='store_true', help='Explicitly select a newly captured Clang extension')
    parser.add_argument('--compiler', choices=(*COMPILERS, 'all'), default='all')
    parser.add_argument('--family', choices=(*FAMILIES, 'all'), default='all')
    selected = parser.parse_args()
    if selected.setup:
        value = tooling.setup(refresh=selected.refresh)
        print('CLANG BASELINE TOOLING PINNED:', value['id'], flush=True)
        return
    if selected.refresh:
        parser.error('--refresh requires --setup')
    for compiler in COMPILERS if selected.compiler == 'all' else (selected.compiler,):
        for family in FAMILIES if selected.family == 'all' else (selected.family,):
            result = build(compiler, family)
            print('MATCHED BASELINE ORIGINAL:', compiler, family, result['build_id'],
                  'CACHED' if result['cache_hit'] else 'BUILT', flush=True)


if __name__ == '__main__':
    main()
