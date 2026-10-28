"""Separate closed observation actions for native ABI preparation."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import process, runtime
from grading.c_executor import runtime as executor
from grading.c_executor.context import host
from . import build, images

ROOT = action_cache.ROOT
HOST_SOURCE = build.MODULE + 'execute.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()
SOURCES = (
    'benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/returns.py',
    'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py',
    'benchmarks/scripts/grading/c_executor/machine.py', 'benchmarks/scripts/grading/c_executor/elf.py',
    'benchmarks/scripts/capabilities/__init__.py', 'benchmarks/scripts/capabilities/elf.py',
    build.MODULE + '__init__.py', build.MODULE + 'images.py', build.MODULE + 'observer_worker.py',
    build.MODULE + 'observe_worker.py', HOST_SOURCE)


def profile():
    base = executor.execute_profile()
    return {**base, 'id': 'bounded-native-aggregate-boundaries-v1',
            'scope': {'original': 'frozen-pinned-Rust-Go-native-endpoints',
                      'runtime_startup': 'not-executed', 'generated_probes': 'bounded-ABI-descriptions',
                      'calls': 'whitelisted-originals-and-probes-only', 'syscalls': 'forbidden',
                      'return_records': 'host-owned-native-record-u64x3'}}


def observe(language, values, *, direction='reference', probe=None, invocation_id=0, cache=None):
    if (language not in ('rust', 'go') or direction not in ('reference', 'replacement', 'bridge')
            or type(invocation_id) is not int or not 0 <= invocation_id < 10**12
            or not isinstance(values, dict) or set(values) != {'nonce', 'first', 'second', 'third'}
            or any(type(v) is not int or not 0 <= v < 2**64 for v in values.values())
            or probe is not None and (not isinstance(probe, bytes) or not 1 <= len(probe) <= 4194304)
            or direction != 'reference' and probe is None):
        raise ValueError('Invalid native ABI invocation before worker admission')
    if (ROOT / HOST_SOURCE).read_bytes() != LOADED:
        raise RuntimeError('Loaded native ABI execution host source changed')
    from .evidence import require_validation
    require_validation()
    executor.admit()
    target = build.build(language)
    images.validate(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']), language)
    materials = {'target/original.elf': bytes.fromhex(target['private_hex']),
                 'target/public.elf': bytes.fromhex(target['public_hex']), 'target/probe.elf': probe or b''}
    request = {'language': language, 'direction': direction, 'values': values, 'id': invocation_id}
    configuration = profile()
    platform = executor.platform(True)
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    selected = action_cache.Action('capability_native_language_abi_execute_v1',
        {**sources, **materials, 'invocation.json': action_cache.canonical(request),
         'profile.json': action_cache.canonical(configuration)},
        arguments=['closed-native-abi-observation'], platform=platform,
        timeout_seconds=configuration['limits']['worker_wall_seconds'])
    local = cache or action_cache.Cache()
    hit = local.lookup(selected)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
    else:
        with executor.image.materialize() as (base, pinned), executor.addon() as (addon, engine_id, engine), \
             tempfile.TemporaryDirectory(prefix='rea-native-abi-observe-', dir='/tmp') as temporary:
            if pinned['id'] != platform['base_image'] or engine_id != platform['engine']:
                raise RuntimeError('Native ABI observation runtime changed after preparation')
            work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
            work.mkdir()
            snapshot.mkdir()
            for name, data in {**sources, **materials, 'profile.json': action_cache.canonical(configuration)}.items():
                path = snapshot / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            command = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
            command += ['--ro-bind', str(addon), '/addon', '/usr/bin/python3', '-I', '-S', '-B',
                        '/environment/' + build.MODULE + 'observe_worker.py']
            token = runtime._authorized.set(tuple(command))
            try:
                reply = process.run(command, input=action_cache.canonical(request),
                    timeout=configuration['limits']['worker_wall_seconds'],
                    output_limit=configuration['limits']['worker_output_bytes'],
                    env={**pinned['manifest']['environment'], 'REA_NATIVE_ABI_WORKER': '1',
                         'LIBUNICORN_PATH': '/addon/unicorn/lib', 'LD_LIBRARY_PATH': '/addon/dependencies'})
            finally:
                runtime._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Closed native ABI observer failed: ' + reply.stderr.decode(errors='replace')[-4000:])
            result = {**json.loads(reply.stdout), 'runtime': platform}
        _guard(sources, configuration, platform)
        _validate(result, request, materials, configuration, platform)
        local.commit(selected, {'result.json': action_cache.canonical(result)})
    _guard(sources, configuration, platform)
    _validate(result, request, materials, configuration, platform)
    return {**result, 'action_id': selected.key, 'cache_hit': bool(hit), 'original_build_id': target['build_id']}


def _guard(sources, configuration, platform):
    if (sources != {name: (ROOT / name).read_bytes() for name in SOURCES}
            or executor.platform(True) != platform or profile() != configuration
            or (ROOT / HOST_SOURCE).read_bytes() != LOADED):
        raise RuntimeError('Native ABI observation sources/runtime changed before result return')


def _validate(result, request, materials, configuration, platform):
    expected = hashlib.sha256(materials['target/original.elf'] + materials['target/probe.elf']
                              + (request['language'] + '/' + request['direction']).encode()).hexdigest()
    if (result.get('runtime') != platform or result.get('host') != host()
            or result.get('request_sha256') != hashlib.sha256(action_cache.canonical(request)).hexdigest()
            or result.get('invocation_id') != request['id']
            or result.get('provenance') != 'host_native_abi_return_v1'
            or result.get('engine_version') != configuration['engine']['version']
            or result.get('elf_sha256') != expected or result.get('original_endpoints_preserved') is not True):
        raise ValueError('Native ABI observer cache/source binding differs')
