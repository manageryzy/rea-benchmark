"""Separate original-execution actions; private vectors do not alter builds."""
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime
from grading.c_executor.context import host
from . import build, reference

ROOT = action_cache.ROOT
SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
           'benchmarks/scripts/capabilities/optimized_locations/native_worker.py')


def calibrate(mode, *, nonces=None, cache=None):
    nonces = list(reference.NONCES if nonces is None else nonces)
    if (not 1 <= len(nonces) <= 64 or any(type(n) is not int or not 0 <= n < 2**64 for n in nonces)
            or len(set(nonces)) != len(nonces)):
        raise ValueError('Invalid optimized-location calibration vectors')
    local = cache or action_cache.Cache()
    target = build.build(mode, cache=local)
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    request = {'private_hex': target['private_hex'], 'nonces': nonces}
    platform = {'compiler_image': image_manifest.load('cpp')['id'], **host()}
    selected = action_cache.Action('capability_optimized_location_native_v1',
        {**sources, 'request.json': action_cache.canonical(request)},
        arguments=['closed-original-optimized-location-calibration'], platform=platform, timeout_seconds=30)
    hit = local.lookup(selected)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
    else:
        with runtime.image('cpp') as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-optimized-native-', dir='/tmp') as temporary:
            if pinned['id'] != platform['compiler_image']:
                raise RuntimeError('Original calibration runtime changed before execution')
            work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
            work.mkdir()
            snapshot.mkdir()
            for name, raw in sources.items():
                path = snapshot / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            arguments = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
            arguments += ['/usr/bin/python3', '-I', '-S', '-B',
                          '/environment/benchmarks/scripts/capabilities/optimized_locations/native_worker.py']
            environment = {**pinned['manifest']['environment'], 'REA_OPTIMIZED_LOCATION_WORKER': '1'}
            token = runtime._authorized.set(tuple(arguments))
            try:
                reply = process.run(arguments, input=action_cache.canonical(request), timeout=30,
                                    output_limit=262144, env=environment)
            finally:
                runtime._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Isolated original calibration failed: ' + reply.stderr.decode(errors='replace')[-4000:])
            if (sources != {name: (ROOT / name).read_bytes() for name in SOURCES}
                    or image_manifest.load('cpp')['id'] != platform['compiler_image']):
                raise RuntimeError('Original calibration inputs changed during execution')
            result = {**json.loads(reply.stdout), 'runtime': platform}
        local.commit(selected, {'result.json': action_cache.canonical(result)})
    if result['runtime'] != platform:
        raise ValueError('Original calibration cache runtime mismatch')
    expected = [(role, nonce) for nonce in nonces for role in range(9)]
    observed = [(row['selected'], row['nonce']) for row in result['observations']]
    if observed != expected:
        raise ValueError('Original calibration cache has a different vector contract')
    return {**result, 'action_id': selected.key, 'original_build_id': target['build_id'], 'cache_hit': bool(hit)}
