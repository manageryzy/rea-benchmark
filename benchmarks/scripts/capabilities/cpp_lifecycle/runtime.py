"""An isolated header binding for the existing sealed C++ compiler worker.

The worker and helpers are shared by content; the older buffer contract and its
action keys stay unchanged. A declared logical source alias supplies the new
public declaration, without changing a file in the older compiler module.
"""
import json
from pathlib import Path
import tempfile

import action_cache
from grading import process
from grading import runtime as images
from grading.c_executor.context import host
from ..cpp_buffer import runtime as base

ROOT = action_cache.ROOT
PREFIX = 'benchmarks/scripts/capabilities/cpp_lifecycle/'
ALIAS = base.PREFIX + 'contract.hpp'


def header():
    return (ROOT / (PREFIX + 'contract.hpp')).read_text()


def policy():
    return {**base.policy(), 'id': 'freestanding-cpp20-lifecycle-v1',
            'source_aliases': {ALIAS: PREFIX + 'contract.hpp'}}


def sources():
    result = {name: (ROOT / name).read_bytes() for name in base.SOURCES if name != ALIAS}
    result[ALIAS] = header().encode('utf-8')
    for name in ('__init__.py', 'runtime.py', 'contract.hpp'):
        result[PREFIX + name] = (ROOT / (PREFIX + name)).read_bytes()
    result[str(base.POLICY.relative_to(ROOT))] = base.POLICY.read_bytes()
    return result


def platform():
    return base.platform()


def compile_action(source):
    if not isinstance(source, str):
        raise ValueError('C++ lifecycle source must be text')
    raw, values = source.encode('utf-8'), policy()
    if not 1 <= len(raw) <= values['limits']['source_bytes'] or b'\0' in raw:
        raise ValueError('Invalid C++ lifecycle source transport')
    frozen, environment = sources(), platform()
    action = action_cache.Action('capability_cpp_lifecycle_compile_v1',
        {**frozen, 'solution.cpp': raw, 'profile.json': action_cache.canonical(values)},
        arguments=['g++-freestanding-cpp20-lifecycle'], platform=environment,
        timeout_seconds=values['limits']['worker_wall_seconds'])
    return action, {'source': source}, frozen, environment, values


def _compile(request, frozen, environment, values):
    if frozen != sources() or environment != platform() or values != policy():
        raise RuntimeError('C++ lifecycle compiler inputs changed before launch')
    with images.image('cpp') as (root, pinned), tempfile.TemporaryDirectory(prefix='rea-cpp-lifecycle-', dir='/tmp') as temporary:
        if pinned['id'] != environment['compiler_image']:
            raise RuntimeError('C++ lifecycle compiler image changed after hashing')
        work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
        work.mkdir()
        snapshot.mkdir()
        for name, raw in frozen.items():
            path = snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (snapshot / 'profile.json').write_bytes(action_cache.canonical(values))
        command = images.sandbox(root, pinned['manifest'], work, source=snapshot) + [
            '/usr/bin/python3', '-I', '-S', '-B', '/environment/' + base.PREFIX + 'worker.py']
        token = images._authorized.set(tuple(command))
        try:
            reply = process.run(command, input=action_cache.canonical(request),
                timeout=values['limits']['worker_wall_seconds'], output_limit=values['limits']['pipe_bytes'],
                env={**pinned['manifest']['environment'], 'REA_CPP_BUFFER_WORKER': '1'})
        finally:
            images._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed C++ lifecycle compiler failed: ' + reply.stderr.decode(errors='replace')[-4000:])
        result = json.loads(reply.stdout)
    if frozen != sources() or environment != platform() or values != policy() or result.get('host') != host():
        raise RuntimeError('C++ lifecycle compiler inputs changed during execution')
    result['runtime'] = {**environment, 'profile_sha256': base.sha(action_cache.canonical(values)),
                         'sources_sha256': {name: base.sha(raw) for name, raw in frozen.items()}}
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
    expected = {**environment, 'profile_sha256': base.sha(action_cache.canonical(values)),
                'sources_sha256': {name: base.sha(raw) for name, raw in frozen.items()}}
    if result.get('runtime') != expected or result.get('source_sha256') != base.sha(source.encode('utf-8')):
        raise ValueError('C++ lifecycle compile cache lacks its source/runtime binding')
    raw = bytes.fromhex(result['elf_hex']) if result['status'] == 'compiled' else None
    if raw is not None and base.sha(raw) != result['elf_sha256']:
        raise ValueError('C++ lifecycle artifact digest mismatch')
    return {**result, 'elf': raw, 'action_id': action.key, 'cache_hit': bool(hit)}
