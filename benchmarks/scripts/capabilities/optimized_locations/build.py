"""Independent optimized/LTO actions; no changes to legacy compiler identities."""
import argparse
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime
from grading.c_executor.context import host
from capabilities import build as family_build, elf
from . import tooling

ROOT = action_cache.ROOT
PREFIX = 'benchmarks/suites/capabilities-v1/sources/optimized_locations/'
MODULE = 'benchmarks/scripts/capabilities/optimized_locations/'
SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
           MODULE + 'build_worker.py')
FILES = ('mixer.hpp', 'mixer.cpp', 'program.cpp')


def settings(mode):
    if mode not in ('optimized', 'lto'):
        raise ValueError('Unsupported optimized-location build condition')
    flags = ['-std=c++20', '-O3', '-g', '-gdwarf-4', '-Wall', '-Wextra', '-fno-pie',
             '-fno-ipa-icf', '-fipa-cp-clone', '-freorder-blocks-and-partition',
             '-fno-exceptions', '-fno-rtti', '-fcf-protection=none',
             '-ffile-prefix-map=/work=.', '-fdebug-prefix-map=/work=.']
    link = ['-O3', '-no-pie', '-Wl,--build-id=none']
    if mode == 'lto':
        flags += ['-flto=1', '-fno-fat-lto-objects']
        link += ['-flto=1', '-save-temps=obj']
    return flags, link


def action(mode):
    flags, link = settings(mode)
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    request = {'mode': mode, 'files': {name: (ROOT / PREFIX / name).read_text() for name in FILES},
               'compiler_flags': flags, 'link_flags': link}
    platform = {'compiler_image': image_manifest.load('cpp')['id'],
                'binutils_image': family_build.tooling()[0], **host()}
    if mode == 'lto':
        platform['lto_image_id'] = tooling.record()['id']
    value = action_cache.Action('capability_optimized_location_build_v1',
        {**sources, 'request.json': action_cache.canonical(request)},
        arguments=['closed-optimized-location-build', mode], platform=platform, timeout_seconds=180)
    return value, request, sources, platform


def build(mode, *, cache=None):
    selected, request, sources, platform = action(mode)
    local = cache or action_cache.Cache()
    hit = local.lookup(selected)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
    else:
        context = tooling.image() if mode == 'lto' else runtime.image('cpp')
        with context as (base, pinned), family_build.tools_image() as (tools, identity, manifest), \
             tempfile.TemporaryDirectory(prefix='rea-optimized-build-', dir='/tmp') as temporary:
            compiler = pinned['base_image_id'] if mode == 'lto' else pinned['id']
            if compiler != platform['compiler_image'] or identity != platform['binutils_image']:
                raise RuntimeError('Optimized-location runtime changed after action resolution')
            if mode == 'lto' and pinned['lto_image_id'] != platform['lto_image_id']:
                raise RuntimeError('LTO runtime changed after action resolution')
            work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
            work.mkdir()
            snapshot.mkdir()
            for name, raw in sources.items():
                path = snapshot / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            arguments = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
            arguments += ['--ro-bind', str(tools), '/tools', '/usr/bin/python3', '-I', '-S', '-B',
                          '/environment/' + MODULE + 'build_worker.py']
            environment = {**pinned['manifest']['environment'], 'REA_OPTIMIZED_LOCATION_WORKER': '1',
                'LD_LIBRARY_PATH': ':'.join(sorted({'/tools/' + str(Path(name).parent)
                    for name, item in manifest['files'].items() if item['elf']}))}
            token = runtime._authorized.set(tuple(arguments))
            try:
                reply = process.run(arguments, input=action_cache.canonical(request), timeout=180,
                                    output_limit=32 * 1024**2, env=environment)
            finally:
                runtime._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Isolated optimized-location build failed: ' + reply.stderr.decode(errors='replace')[-6000:])
            if action(mode)[0].key != selected.key:
                raise RuntimeError('Optimized-location build inputs changed during execution')
            result = {**json.loads(reply.stdout), 'runtime': platform}
        local.commit(selected, {'result.json': action_cache.canonical(result)})
    if result.get('runtime') != platform:
        raise ValueError('Optimized-location cache has a different runtime binding')
    elf.stripped_matches(bytes.fromhex(result['private_hex']), bytes.fromhex(result['public_hex']))
    return {**result, 'build_id': selected.key, 'cache_hit': bool(hit)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--mode', choices=('optimized', 'lto', 'all'), default='all')
    arguments = parser.parse_args()
    if arguments.setup:
        print('LTO TOOLING PINNED:', tooling.setup()['id'])
        return
    for mode in ('optimized', 'lto') if arguments.mode == 'all' else (arguments.mode,):
        value = build(mode)
        print('OPTIMIZED LOCATIONS:', mode, value['build_id'], 'CACHED' if value['cache_hit'] else 'BUILT', flush=True)


if __name__ == '__main__':
    main()
