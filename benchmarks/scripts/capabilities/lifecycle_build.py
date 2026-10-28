"""Build the additional lifecycle interfaces without revising older builders."""
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime
from grading.c_executor.context import host
from . import build

ROOT = action_cache.ROOT


def action(family):
    if family not in ('callback_policy', 'session_state'):
        raise ValueError('Unknown lifecycle family')
    directory = build.SUITE / 'sources/lifecycle'
    source = (directory / (family + '.cpp')).read_bytes()
    header = (directory / 'common.hpp').read_bytes()
    request = {'source': source.decode(), 'header': header.decode(), 'absent': False, 'pie': False}
    frozen = {name: (ROOT / name).read_bytes() for name in build.SOURCES}
    environment = {'compiler_image': image_manifest.load('cpp')['id'], 'binutils_image': build.tooling()[0], **host()}
    # Use the existing worker protocol and action contract. Its exact request,
    # compiler flags and images determine the artifact, independently of the
    # new caller's location or unrelated source/report changes.
    action = action_cache.Action('capability_target_build_v1',
        {**frozen, 'target/request.json': action_cache.canonical(request)},
        arguments=['cpp', '-O2', '-std=c++20'], platform=environment, timeout_seconds=150)
    return action, request, frozen, environment


def original(family, *, cache=None):
    selected, request, frozen, environment = action(family)
    local = cache or action_cache.Cache()
    hit = local.lookup(selected)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
        return {**result, 'build_id': selected.key, 'cache_hit': True}
    with runtime.image('cpp') as (base, pinned), build.tools_image() as (tools, identity, manifest), \
         tempfile.TemporaryDirectory(prefix='rea-lifecycle-original-', dir='/tmp') as temporary:
        if pinned['id'] != environment['compiler_image'] or identity != environment['binutils_image']:
            raise RuntimeError('Lifecycle original environment changed after hashing')
        work, snapshot = Path(temporary) / 'work', Path(temporary) / 'snapshot'
        work.mkdir()
        snapshot.mkdir()
        for name, raw in frozen.items():
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        command = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
        command += ['--ro-bind', str(tools), '/tools', '/usr/bin/python3', '-I', '-S', '-B',
                    '/environment/benchmarks/scripts/capabilities/build_worker.py']
        environment_variables = {**pinned['manifest']['environment'], 'REA_CAPABILITY_BUILD': '1',
            'LD_LIBRARY_PATH': ':'.join(sorted({'/tools/' + str(Path(name).parent)
                for name, item in manifest['files'].items() if item['elf']}))}
        token = runtime._authorized.set(tuple(command))
        try:
            reply = process.run(command, input=action_cache.canonical(request), timeout=150,
                                output_limit=32 * 1024**2, env=environment_variables)
        finally:
            runtime._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed lifecycle original build failed: ' + reply.stderr.decode(errors='replace')[-2000:])
        if frozen != {name: (ROOT / name).read_bytes() for name in build.SOURCES}:
            raise RuntimeError('Original worker changed during lifecycle compilation')
        result = json.loads(reply.stdout)
    local.commit(selected, {'result.json': action_cache.canonical(result)})
    return {**result, 'build_id': selected.key, 'cache_hit': False}
