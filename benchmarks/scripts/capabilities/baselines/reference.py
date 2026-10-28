"""Independent buffer semantics and separate cached original observations."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime
from grading.c_executor.context import host
from capabilities import buffers, build as gcc_build
from . import build

ROOT = action_cache.ROOT
HOST_SOURCE = build.MODULE + 'reference.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()
SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
           'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py',
           build.MODULE + 'reference_worker.py')
MASK = 2**64 - 1


def value(family, data):
    if family == 'record_codec':
        decoded = ((data[1] * int.from_bytes(data[2:4], 'little')) ^ 0x6d35
                   if len(data) >= 4 and data[0] == 0xa7 else 0xffff)
        return decoded + (data[4] if len(data) > 4 else 0)
    if family == 'dispatch_registry':
        if not data: return 0
        choice = data[0] & 1
        result = 3 if choice == 0 else 11
        for index, byte in enumerate(data[1:], 1):
            result = (result + byte) & MASK if choice == 0 else result ^ (byte << (8 * (index % 8)))
        return (result + choice) & MASK
    if family == 'numeric_aggregation':
        items = sorted(int.from_bytes(data[i:i + 2], 'little', signed=True) for i in range(0, min(16, len(data) // 2 * 2), 2))
        return (sum(number * (index + 1) for index, number in enumerate(items)) & 0xffffffff) ^ 0x9e3779b9
    if family == 'variable_objects':
        if len(data) < 4: return 0xff00 + 17
        length = int.from_bytes(data[:2], 'little')
        if length > len(data) - 4: return 0xff01 + 17
        result = data[2]
        for byte in data[4:4 + length]: result = (result * 33 + byte) & MASK
        return ((result ^ data[3]) + 17) & MASK
    if family == 'callback_policy':
        return (sum((byte + 1) * (index + 3) for index, byte in enumerate(data)) + 9) & MASK
    if family == 'session_state':
        count = checksum = result = 0
        for byte in data:
            count = (count + 1) & 0xffffffff
            checksum = (checksum * 17 + byte) & 0xffffffff
            result ^= checksum ^ count
        return result
    raise ValueError('Unknown matched semantic family')


def expected(family, case):
    arena = bytearray(buffers.initial(case))
    data = bytes.fromhex(case['input_hex'])
    status, length = -2, 0
    if len(data) <= 512:
        length = len(data) + 8
        status = -1
        if case['capacity'] >= length:
            prefix = value(family, data).to_bytes(8, 'little')
            message = prefix + bytes(byte ^ prefix[index % 8] for index, byte in enumerate(data))
            start = case['output_offset']
            arena[start:start + length] = message
            status = 0
    return {'id': case['id'], 'status': status, 'length': length, 'arena_hex': arena.hex()}


def vectors(family):
    cases = buffers.vectors(family)
    data = [b'\xa7\xff\xff\xff\x0d', b'\xff\xff\x01\0\0\x80\xff\x7f',
            (100).to_bytes(2, 'little') + b'\xff\x93' + bytes((i * 71 + 13) % 256 for i in range(100)),
            (508).to_bytes(2, 'little') + b'\xff\x81' + bytes((i * 31 + 5) % 256 for i in range(508)),
            bytes(range(256)) * 2]
    for raw in data:
        for capacity, offset in ((0, 64), (len(raw) + 7, 66), (len(raw) + 8, 62), (len(raw) + 8, 1528)):
            identity = len(cases)
            cases.append({'id': identity, 'input_hex': raw.hex(), 'capacity': capacity, 'output_offset': offset,
                          'seed': (identity * 19 + 11) % 256, 'capabilities': ['C1', 'C2', 'C3', 'C4']})
    return cases


def profile(compiler):
    return {'memory_bytes': (4 * 1024**3 if compiler == 'go' else 512 * 1024**2),
            'wall_seconds': 15, 'cpu_seconds': 12, 'output_bytes': 4 * 1024**2}


def observe(compiler, family, cases, *, cache=None):
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded baseline observation/oracle host changed')
    if not isinstance(cases, list) or not 1 <= len(cases) <= 128 or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Invalid matched baseline vector set')
    for case in cases:
        buffers.initial(case)
        tags = case['capabilities']
        if (not isinstance(tags, list) or not 1 <= len(tags) <= 4
                or any(not isinstance(tag, str) for tag in tags)
                or len(set(tags)) != len(tags) or set(tags) - {'C1', 'C2', 'C3', 'C4'}):
            raise ValueError('Invalid matched baseline score tags')
    target = build.build(compiler, family)
    binary = bytes.fromhex(target['public_hex'])
    language = 'cpp' if compiler == 'clang20' else compiler
    image_id = image_manifest.load(language)['id']
    environment = {'runtime_image': image_id, **host()}
    policy = profile(compiler)
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    selected = action_cache.Action('capability_baseline_reference_v1',
        {**sources, 'target.elf': binary, 'cases.json': action_cache.canonical(cases),
         'profile.json': action_cache.canonical(policy)},
        arguments=['closed-matched-original-buffer-reference'], platform=environment, timeout_seconds=20)
    local = cache or action_cache.Cache()
    hit = local.lookup(selected)
    if hit:
        result = json.loads(local.get(hit['output_files']['reference.json']['digest']))
    else:
        with runtime.image(language) as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-baseline-reference-', dir='/tmp') as temporary:
            if pinned['id'] != image_id:
                raise RuntimeError('Baseline reference image changed')
            work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
            work.mkdir()
            snapshot.mkdir()
            for name, raw in {**sources, 'target.elf': binary, 'profile.json': action_cache.canonical(policy)}.items():
                path = snapshot / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            command = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
            command += ['/usr/bin/python3', '-I', '-S', '-B', '/environment/' + build.MODULE + 'reference_worker.py']
            token = runtime._authorized.set(tuple(command))
            try:
                reply = process.run(command, input=action_cache.canonical({'cases': cases}), timeout=20,
                    output_limit=policy['output_bytes'], env={**pinned['manifest']['environment'], 'REA_BASELINE_REFERENCE_WORKER': '1'})
            finally:
                runtime._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Closed baseline observation failed: ' + reply.stderr.decode(errors='replace')[-5000:])
            result = {**json.loads(reply.stdout), 'runtime': environment}
        _guard(sources, compiler, family, target['build_id'], image_id, policy)
        _validate(result, binary, cases, environment)
        local.commit(selected, {'reference.json': action_cache.canonical(result)})
    _guard(sources, compiler, family, target['build_id'], image_id, policy)
    _validate(result, binary, cases, environment)
    return {**result, 'reference_id': selected.key, 'original_build_id': target['build_id'], 'cache_hit': bool(hit),
            'vectors_sha256': hashlib.sha256(action_cache.canonical(cases)).hexdigest()}


def _guard(sources, compiler, family, build_id, image_id, policy):
    language = 'cpp' if compiler == 'clang20' else compiler
    if (Path(__file__).read_bytes() != LOADED or sources != {name: (ROOT / name).read_bytes() for name in SOURCES}
            or image_manifest.load(language)['id'] != image_id or profile(compiler) != policy
            or build.action(compiler, family)[0].key != build_id):
        raise RuntimeError('Baseline observation sources/runtime changed')


def _validate(result, binary, cases, environment):
    if (result.get('runtime') != environment or result.get('host') != host()
            or result.get('binary_sha256') != hashlib.sha256(binary).hexdigest()
            or result.get('scope') != 'trusted_original_cli_only'
            or [r['id'] for r in result.get('observations', [])] != [c['id'] for c in cases]):
        raise ValueError('Baseline observation bindings differ')
    for row in result['observations']:
        if (set(row) != {'id', 'status', 'length', 'arena_hex'} or type(row['id']) is not int
                or type(row['status']) is not int or row['status'] not in (0, -1, -2)
                or type(row['length']) is not int or not 0 <= row['length'] <= 520
                or len(bytes.fromhex(row['arena_hex'])) != 2048):
            raise ValueError('Invalid baseline raw observation')


def calibrate(compiler, family):
    cases = vectors(family)
    result = observe(compiler, family, cases)
    expected_rows = [expected(family, case) for case in cases]
    if result['observations'] != expected_rows:
        raise ValueError('Matched baseline disagrees with independent Python semantics')
    original = gcc_build.build(family)
    gcc = buffers.reference(original, cases)
    if gcc['observations'] != expected_rows:
        raise ValueError('Existing GCC original disagrees with the matched baseline contract')
    return {'compiler': compiler, 'family': family, 'original_build_id': result['original_build_id'],
            'reference_id': result['reference_id'], 'gcc_reference_id': gcc['reference_id'],
            'passed': len(cases), 'total': len(cases), 'model_api_calls': 0}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', choices=(*build.COMPILERS, 'all'), default='all')
    parser.add_argument('--family', choices=(*build.FAMILIES, 'all'), default='all')
    args = parser.parse_args()
    for compiler in build.COMPILERS if args.compiler == 'all' else (args.compiler,):
        for family in build.FAMILIES if args.family == 'all' else (args.family,):
            print(json.dumps(calibrate(compiler, family), sort_keys=True), flush=True)
