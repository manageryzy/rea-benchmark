"""Original-execution cache actions independent of native ABI probe generation."""
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime
from grading.c_executor.context import host
from . import build, reference

SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
           build.MODULE + 'native_worker.py')


def calibrate(language, *, vectors=None, cache=None):
    if language not in ('rust', 'go'):
        raise ValueError('Unsupported native ABI runtime')
    vectors = [reference.arguments(nonce) for nonce in reference.NONCES] if vectors is None else vectors
    if (not isinstance(vectors, list) or not 1 <= len(vectors) <= 64
            or any(not isinstance(v, dict) or set(v) != {'nonce', 'first', 'second', 'third'}
                   or any(type(n) is not int or not 0 <= n < 2**64 for n in v.values()) for v in vectors)
            or len({v['nonce'] for v in vectors}) != len(vectors)):
        raise ValueError('Invalid native ABI calibration arguments')
    local = cache or action_cache.Cache()
    target = build.build(language, cache=local)
    sources = {name: (build.ROOT / name).read_bytes() for name in SOURCES}
    request = {'language': language, 'private_hex': target['private_hex'], 'vectors': vectors}
    platform = {'compiler_image': image_manifest.load(language)['id'], 'language': language, **host()}
    selected = action_cache.Action('capability_native_language_abi_native_v1',
        {**sources, 'request.json': action_cache.canonical(request)},
        arguments=['closed-native-language-abi-calibration', language], platform=platform, timeout_seconds=40)
    hit = local.lookup(selected)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
    else:
        with runtime.image(language) as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-native-abi-native-', dir='/tmp') as temporary:
            if pinned['id'] != platform['compiler_image']:
                raise RuntimeError('Native ABI calibration runtime changed before execution')
            work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
            work.mkdir()
            snapshot.mkdir()
            for name, raw in sources.items():
                destination = snapshot / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            arguments = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
            arguments += ['/usr/bin/python3', '-I', '-S', '-B', '/environment/' + build.MODULE + 'native_worker.py']
            token = runtime._authorized.set(tuple(arguments))
            try:
                reply = process.run(arguments, input=action_cache.canonical(request), timeout=40,
                                    output_limit=262144, env={**pinned['manifest']['environment'], 'REA_NATIVE_ABI_WORKER': '1'})
            finally:
                runtime._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Closed native ABI calibration failed: ' + reply.stderr.decode(errors='replace')[-4000:])
            if (sources != {name: (build.ROOT / name).read_bytes() for name in SOURCES}
                    or image_manifest.load(language)['id'] != platform['compiler_image']):
                raise RuntimeError('Native ABI calibration inputs changed during execution')
            result = {**json.loads(reply.stdout), 'runtime': platform}
        local.commit(selected, {'result.json': action_cache.canonical(result)})
    if result.get('runtime') != platform:
        raise ValueError('Native ABI calibration cache runtime mismatch')
    expected = [(selected, values) for values in vectors for selected in range(3 if language == 'rust' else 2)]
    if [(row['selected'], row['arguments']) for row in result['observations']] != expected:
        raise ValueError('Native ABI calibration cache vector contract mismatch')
    return {**result, 'action_id': selected.key, 'original_build_id': target['build_id'], 'cache_hit': bool(hit)}
