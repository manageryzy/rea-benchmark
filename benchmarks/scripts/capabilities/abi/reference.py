"""Independent native calibration of the frozen original-reference endpoint."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime as images
from grading.c_executor.context import host
from . import contracts

ROOT = action_cache.ROOT
SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py',
    'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py',
    'benchmarks/scripts/capabilities/__init__.py', 'benchmarks/scripts/capabilities/abi/__init__.py',
    'benchmarks/scripts/capabilities/abi/reference.py',
    'benchmarks/scripts/capabilities/abi/compiler.py', 'benchmarks/scripts/capabilities/abi/reference_worker.py')
FLAGS = ['-std=c++20', '-O2', '-no-pie', '-Wl,--build-id=none']


def native(target, nonces=None, *, cache=None):
    from . import runtime
    nonces = list(contracts.NONCES if nonces is None else nonces)
    if not 1 <= len(nonces) <= 16 or any(type(n) is not int or not 0 <= n < 2**64 for n in nonces):
        raise ValueError('Invalid original calibration nonces')
    record = image_manifest.load('cpp')
    environment = {'compiler_image': record['id'], **host()}
    frozen = {name: (ROOT / name).read_bytes() for name in SOURCES}
    policy = runtime.policy()
    request = {'original_obj_hex': target['original_obj_hex'], 'helpers_obj_hex': target['helpers_obj_hex'],
               'width': 8 * len(contracts.paths(target['family'])), 'nonces': nonces, 'compile_flags': FLAGS}
    action = action_cache.Action('capability_abi_original_native_v1',
        {**frozen, 'profile.json': action_cache.canonical(policy), 'request.json': action_cache.canonical(request)},
        arguments=['native-frozen-original-reference-only'], platform=environment, timeout_seconds=150)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['reference.json']['digest']))
    else:
        with images.image('cpp') as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-abi-native-', dir='/tmp') as temporary:
            if pinned['id'] != record['id']:
                raise RuntimeError('Native calibration compiler image changed')
            temporary = Path(temporary)
            work, source = temporary / 'work', temporary / 'source'
            work.mkdir(); source.mkdir()
            for name, raw in frozen.items():
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            (source / 'profile.json').write_bytes(action_cache.canonical(policy))
            command = images.sandbox(base, pinned['manifest'], work, source=source) + [
                '/usr/bin/python3', '-I', '-S', '-B', '/environment/benchmarks/scripts/capabilities/abi/reference_worker.py']
            token = images._authorized.set(tuple(command))
            try:
                reply = process.run(command, input=action_cache.canonical(request), timeout=150,
                    output_limit=1048576, env={**pinned['manifest']['environment'], 'REA_ABI_PROBE_WORKER': '1'})
            finally:
                images._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Closed native ABI calibration failed: ' + reply.stderr.decode(errors='replace')[-3000:])
            result = json.loads(reply.stdout)
        result['runtime'] = {**environment, 'sources': {name: runtime.sha(raw) for name, raw in frozen.items()}}
        if any((ROOT / name).read_bytes() != raw for name, raw in frozen.items()) or result.get('host') != host():
            raise RuntimeError('Native ABI calibration source/host drift')
        local.commit(action, {'reference.json': action_cache.canonical(result)})
    if (result.get('runtime') != {**environment, 'sources': {name: runtime.sha(raw) for name, raw in frozen.items()}}
            or any(result.get(name) != target[name] for name in ('original_obj_sha256', 'helpers_obj_sha256'))
            or [item['nonce'] for item in result['observations']] != nonces):
        raise ValueError('Native original-reference cache integrity failed')
    return {**result, 'reference_id': action.key, 'cache_hit': bool(hit)}
