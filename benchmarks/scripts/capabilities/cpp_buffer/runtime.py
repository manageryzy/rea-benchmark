"""C++ compile actions, isolated from original builds, vectors and report storage."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime as images
from grading.c_executor import runtime as executor
from grading.c_executor.context import host

ROOT = action_cache.ROOT
POLICY = ROOT / 'benchmarks/config/cpp-buffer.json'
PREFIX = 'benchmarks/scripts/capabilities/cpp_buffer/'
SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py',
    'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py',
    'benchmarks/scripts/grading/c_executor/elf.py', 'benchmarks/scripts/grading/c_executor/link.ld',
    'benchmarks/scripts/capabilities/__init__.py', PREFIX + '__init__.py', PREFIX + 'runtime.py',
    PREFIX + 'worker.py', PREFIX + 'contract.hpp', PREFIX + 'helpers.cpp')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def header():
    return (ROOT / (PREFIX + 'contract.hpp')).read_text()


def policy():
    value = json.loads(POLICY.read_bytes())
    if (value.get('schema_version') != 1 or value.get('id') != 'freestanding-cpp20-buffer-v1'
            or value.get('compiler_image') != 'cpp'
            or any(type(n) is not int or n <= 0 for n in value['limits'].values())):
        raise ValueError('Unsupported C++ buffer compiler profile')
    limits = executor.execute_profile()['limits']
    return {**value, 'elf_limits': {name: limits[name] for name in
        ('max_elf_bytes', 'max_image_bytes', 'max_image_writable_bytes')}}


def sources():
    return {name: (ROOT / name).read_bytes() for name in SOURCES}


def platform():
    return {'compiler_image': image_manifest.load('cpp')['id'], **host()}


def compile_action(source):
    if not isinstance(source, str):
        raise ValueError('C++ source must be text')
    raw, values = source.encode('utf-8'), policy()
    if not 1 <= len(raw) <= values['limits']['source_bytes'] or b'\0' in raw:
        raise ValueError('Invalid C++ source byte transport')
    frozen, environment = sources(), platform()
    request = {'source': source}
    action = action_cache.Action('capability_cpp_buffer_compile_v1',
        {**frozen, 'solution.cpp': raw, 'profile.json': action_cache.canonical(values)},
        arguments=['g++-freestanding-cpp20-buffer'], platform=environment,
        timeout_seconds=values['limits']['worker_wall_seconds'])
    return action, request, frozen, environment, values


def _compile(request, frozen, environment, values):
    if frozen != sources() or environment != platform() or values != policy():
        raise RuntimeError('C++ buffer compiler inputs changed before worker launch')
    with images.image('cpp') as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-cpp-buffer-', dir='/tmp') as temporary:
        if pinned['id'] != environment['compiler_image']:
            raise RuntimeError('C++ compiler image changed after hashing')
        temporary = Path(temporary)
        work, snapshot = temporary / 'work', temporary / 'source'
        work.mkdir(); snapshot.mkdir()
        for name, raw in frozen.items():
            path = snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (snapshot / 'profile.json').write_bytes(action_cache.canonical(values))
        command = images.sandbox(base, pinned['manifest'], work, source=snapshot) + [
            '/usr/bin/python3', '-I', '-S', '-B', '/environment/' + PREFIX + 'worker.py']
        token = images._authorized.set(tuple(command))
        try:
            reply = process.run(command, input=action_cache.canonical(request),
                timeout=values['limits']['worker_wall_seconds'], output_limit=values['limits']['pipe_bytes'],
                env={**pinned['manifest']['environment'], 'REA_CPP_BUFFER_WORKER': '1'})
        finally:
            images._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed C++ buffer worker failed: ' + reply.stderr.decode(errors='replace')[-4000:])
        result = json.loads(reply.stdout)
    if (any((ROOT / name).read_bytes() != raw for name, raw in frozen.items()) or environment != platform()
            or values != policy() or result.get('host') != host()):
        raise RuntimeError('C++ compiler inputs changed during execution')
    result['runtime'] = {**environment, 'profile_sha256': sha(action_cache.canonical(values)),
                         'sources_sha256': {name: sha(raw) for name, raw in frozen.items()}}
    return result


def compile(source, *, cache=None):
    from .evidence import require_validation
    require_validation()
    action, request, frozen, environment, values = compile_action(source)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['compile.json']['digest']))
    else:
        result = _compile(request, frozen, environment, values)
        local.commit(action, {'compile.json': action_cache.canonical(result)})
    expected = {**environment, 'profile_sha256': sha(action_cache.canonical(values)),
                'sources_sha256': {name: sha(raw) for name, raw in frozen.items()}}
    if result.get('runtime') != expected or result.get('source_sha256') != sha(source.encode('utf-8')):
        raise ValueError('C++ compile cache lacks its source/runtime binding')
    raw = bytes.fromhex(result['elf_hex']) if result['status'] == 'compiled' else None
    if raw is not None and sha(raw) != result['elf_sha256']:
        raise ValueError('C++ compile artifact changed')
    return {**result, 'elf': raw, 'action_id': action.key, 'cache_hit': bool(hit)}
