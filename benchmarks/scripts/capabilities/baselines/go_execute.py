"""Cached host-owned observations for one restricted Go buffer invocation."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import process, runtime
from grading.c_executor import runtime as executor
from grading.c_executor.context import host
from . import go_runtime

ROOT = action_cache.ROOT
HOST_SOURCE = go_runtime.MODULE + 'go_execute.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()
SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/grading/returns.py', 'benchmarks/scripts/grading/c_executor/__init__.py',
    'benchmarks/scripts/grading/c_executor/context.py', 'benchmarks/scripts/grading/c_executor/machine.py',
    'benchmarks/scripts/grading/c_executor/elf.py', 'benchmarks/scripts/capabilities/__init__.py',
    'benchmarks/scripts/capabilities/elf.py', 'benchmarks/scripts/capabilities/native_abi/__init__.py',
    'benchmarks/scripts/capabilities/native_abi/images.py', go_runtime.MODULE + '__init__.py',
    go_runtime.MODULE + 'go_image.py', go_runtime.MODULE + 'go_observer.py',
    go_runtime.MODULE + 'go_observe_worker.py', HOST_SOURCE)


def profile():
    return {**executor.execute_profile(), 'id': 'bounded-go-buffer-abiinternal-v1',
            'scope': {'abi': 'go_abi_internal_amd64', 'runtime_startup': 'not_executed',
                      'image_writes': 'forbidden', 'syscalls': 'forbidden', 'return_records': 'host-owned'}}


def invoke(binary, invocation, *, cache=None):
    executor.admit()
    configuration, environment = profile(), executor.platform(True)
    from .go_image import load
    load(binary, configuration['limits'])
    if (not isinstance(invocation, dict) or set(invocation) != {'id', 'regions', 'arguments', 'callbacks', 'return_type'}
            or invocation['return_type'] != 'i32' or invocation['callbacks'] != []
            or not isinstance(invocation['arguments'], list) or len(invocation['arguments']) != 5):
        raise ValueError('Go execution accepts one bounded buffer invocation')
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    _guard(sources, configuration, environment)
    request = {'invocation': invocation}
    action = action_cache.Action('capability_go_buffer_execute_v1',
        {**sources, 'candidate.elf': binary, 'invocation.json': action_cache.canonical(request),
         'profile.json': action_cache.canonical(configuration)},
        arguments=['closed-go-abiinternal-buffer-observation'], platform=environment,
        timeout_seconds=configuration['limits']['worker_wall_seconds'])
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
    else:
        result = _invoke(binary, request, sources, configuration, environment)
        _guard(sources, configuration, environment)
        _validate(result, binary, request, configuration, environment)
        local.commit(action, {'result.json': action_cache.canonical(result)})
    _guard(sources, configuration, environment)
    _validate(result, binary, request, configuration, environment)
    return {**result, 'action_id': action.key, 'cache_hit': bool(hit)}


def _guard(sources, configuration, environment):
    if (Path(__file__).read_bytes() != LOADED or sources != {name: (ROOT / name).read_bytes() for name in SOURCES}
            or profile() != configuration or executor.platform(True) != environment):
        raise RuntimeError('Go buffer execution sources/runtime changed')


def _validate(result, binary, request, configuration, environment):
    if (result.get('runtime') != environment or result.get('host') != host()
            or result.get('request_sha256') != hashlib.sha256(action_cache.canonical(request)).hexdigest()
            or result.get('elf_sha256') != hashlib.sha256(binary).hexdigest()
            or result.get('invocation_id') != request['invocation']['id']
            or result.get('engine_version') != configuration['engine']['version']
            or result.get('abi') != 'go_abi_internal_amd64'
            or result.get('provenance') != 'host_emulator_return_v1'):
        raise ValueError('Go buffer cached observation binding differs')


def _invoke(binary, request, sources, configuration, environment):
    with executor.image.materialize() as (base, pinned), executor.addon() as (addon, engine_id, engine), \
         tempfile.TemporaryDirectory(prefix='rea-go-buffer-observe-', dir='/tmp') as temporary:
        if pinned['id'] != environment['base_image'] or engine_id != environment['engine']:
            raise RuntimeError('Go observer runtime changed after preparation')
        work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
        work.mkdir()
        snapshot.mkdir()
        for name, raw in {**sources, 'candidate.elf': binary, 'profile.json': action_cache.canonical(configuration)}.items():
            path = snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        command = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
        command += ['--ro-bind', str(addon), '/addon', '/usr/bin/python3', '-I', '-S', '-B',
                    '/environment/' + go_runtime.MODULE + 'go_observe_worker.py']
        token = runtime._authorized.set(tuple(command))
        try:
            reply = process.run(command, input=action_cache.canonical(request),
                timeout=configuration['limits']['worker_wall_seconds'],
                output_limit=configuration['limits']['worker_output_bytes'],
                env={**pinned['manifest']['environment'], 'REA_GO_BUFFER_OBSERVER': '1',
                     'LIBUNICORN_PATH': '/addon/unicorn/lib', 'LD_LIBRARY_PATH': '/addon/dependencies'})
        finally:
            runtime._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed Go buffer observer failed: ' + reply.stderr.decode(errors='replace')[-4000:])
        return {**json.loads(reply.stdout), 'runtime': environment}
