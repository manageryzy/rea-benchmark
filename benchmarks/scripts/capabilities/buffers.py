"""Independent C17 buffer conditions: frozen original oracles and host-owned effects."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime as sandbox
from grading.c_executor import runtime as c_runtime
from grading.c_executor.context import host
from .build import ROOT

HEADER = '''#include <stdint.h>
#include <stddef.h>
int32_t reconstructed(const uint8_t *input, size_t input_len,
                      uint8_t *output, size_t output_capacity, size_t *output_len);
'''
REFERENCE_SOURCES = ('benchmarks/scripts/capabilities/reference_worker.py',
    'benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py',
    'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py')


def vectors(family_id):
    common = [b'', b'\0', b'\xff', b'\r\n\0\xff', bytes(range(16)),
              b'\xa7\x02\xff\x7f', b'\xa7\x03\x00\x80\x0d',
              b'\x00\x80\xff\x7f\xff\xff\0\0\xff\xff',
              b'\x03\0\x21\0abc', b'\x04\0\x21\0abc',
              bytes((index * 29 + 7) % 256 for index in range(512)),
              bytes((index * 31 + 3) % 256 for index in range(513))]
    if family_id == 'record_codec':
        common += [bytes([tag, count, low, high]) for tag, count, low, high in
                   [(0xa7, 0, 0, 0), (0xa7, 255, 255, 255), (0xa6, 7, 13, 10), (0xa7, 1, 10, 13)]]
    if family_id == 'numeric_aggregation':
        common += [b'\x01\0\x01\0\xff\xff\0\x80', b'\xff\x7f\0\x80\0\0']
    cases = []
    def add(raw, capacity, offset, tags):
        cases.append({'id': len(cases), 'input_hex': raw.hex(), 'capacity': capacity,
                      'output_offset': offset, 'seed': (len(cases) * 17 + 19) % 256, 'capabilities': tags})
    for raw in common:
        add(raw, min(520, 8 + len(raw)), 1024, ['C1', 'C2', 'C3', 'C4'])
    for raw in common[:6]:
        for offset in (62, 64, 66):
            add(raw, 8 + len(raw), offset, ['C3', 'C4'])
    for capacity in (0, 1, 7, 11, 12, 520):
        add(b'\r\n\0\xff', capacity, 64, ['C3', 'C4'])
    return cases


def initial(case):
    raw = bytes.fromhex(case['input_hex'])
    if (set(case) != {'id', 'input_hex', 'capacity', 'output_offset', 'seed', 'capabilities'}
            or type(case['id']) is not int or not 0 <= case['id'] < 10**12
            or len(raw) > 513 or type(case['capacity']) is not int or not 0 <= case['capacity'] <= 520
            or type(case['output_offset']) is not int or not 0 <= case['output_offset'] <= 1528
            or type(case['seed']) is not int or not 0 <= case['seed'] <= 255):
        raise ValueError('Invalid buffer invocation contract')
    arena = bytearray((case['seed'] + 37 * index) % 256 for index in range(2048))
    arena[64:64 + len(raw)] = raw
    return bytes(arena)


def reference(build, cases, *, cache=None):
    if not 1 <= len(cases) <= 128 or len({row['id'] for row in cases}) != len(cases):
        raise ValueError('Reference cases must be bounded and uniquely numbered')
    for case in cases:
        initial(case)
    target = bytes.fromhex(build['public_hex'])
    record = image_manifest.load('cpp')
    frozen = {name: (ROOT / name).read_bytes() for name in REFERENCE_SOURCES}
    environment = {'image_id': record['id'], **host()}
    request = {'target_hex': target.hex(), 'cases': cases}
    action = action_cache.Action('capability_original_reference_v1',
        {**frozen, 'target': target, 'cases.json': action_cache.canonical(cases)},
        arguments=['frozen-original-buffer-contract-v1'], platform=environment, timeout_seconds=15)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['reference.json']['digest']))
        if (result.get('binary_sha256') != hashlib.sha256(target).hexdigest()
                or result.get('vectors_sha256') != hashlib.sha256(action_cache.canonical(cases)).hexdigest()
                or result.get('runtime') != environment):
            raise ValueError('Original reference cache differs from its inputs')
        return {**result, 'reference_id': action.key, 'cache_hit': True}
    with sandbox.image('cpp') as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-original-reference-', dir='/tmp') as temporary:
        if pinned['id'] != environment['image_id']:
            raise RuntimeError('Original reference image changed after hashing')
        temporary = Path(temporary)
        work, source = temporary / 'work', temporary / 'source'
        work.mkdir()
        source.mkdir()
        for name, raw in frozen.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        command = sandbox.sandbox(base, pinned['manifest'], work, source=source) + [
            '/usr/bin/python3', '-I', '-S', '-B', '/environment/benchmarks/scripts/capabilities/reference_worker.py']
        token = sandbox._authorized.set(tuple(command))
        try:
            reply = process.run(command, input=action_cache.canonical(request), timeout=15, output_limit=4 * 1024**2,
                                env={**pinned['manifest']['environment'], 'REA_CAPABILITY_REFERENCE': '1'})
        finally:
            sandbox._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed original reference failed: ' + reply.stderr.decode(errors='replace')[-2000:])
        result = json.loads(reply.stdout)
        if result.get('host') != host() or frozen != {name: (ROOT / name).read_bytes() for name in REFERENCE_SOURCES}:
            raise RuntimeError('Original reference sources or host changed during execution')
    result.update(binary_sha256=hashlib.sha256(target).hexdigest(), runtime=environment,
                  vectors_sha256=hashlib.sha256(action_cache.canonical(cases)).hexdigest())
    local.commit(action, {'reference.json': action_cache.canonical(result)})
    return {**result, 'reference_id': action.key, 'cache_hit': False}


def invocation(case):
    arena = initial(case)
    readable = [[64, len(bytes.fromhex(case['input_hex']))]] if case['input_hex'] else []
    if case['capacity']:
        readable.append([case['output_offset'], case['capacity']])
    return {'id': case['id'], 'return_type': 'i32', 'callbacks': [],
        'regions': [{'id': 'arena', 'data_hex': arena.hex(), 'readable': readable,
                     'writable': [[case['output_offset'], case['capacity']]] if case['capacity'] else []},
                    {'id': 'output_length', 'data_hex': 'ff' * 8, 'readable': [[0, 8]], 'writable': [[0, 8]]}],
        'arguments': [{'region': 'arena', 'offset': 64}, len(bytes.fromhex(case['input_hex'])),
                      {'region': 'arena', 'offset': case['output_offset']}, case['capacity'],
                      {'region': 'output_length', 'offset': 0}]}


def compare(observed, expected, case):
    valid_return = observed.get('status') == 'returned' and observed.get('return_record') is not None
    if not valid_return:
        return {'id': case['id'], 'passed': False, 'failure': observed.get('status'), 'components': {}}
    value = observed['return_record']
    status = int(bytes.fromhex(value['value_hex']).decode('ascii'))
    memory = {region['id']: bytes.fromhex(region['data_hex']) for region in observed['regions']}
    length = int.from_bytes(memory['output_length'], 'little')
    wanted = bytes.fromhex(expected['arena_hex'])
    start, written = case['output_offset'], expected['length'] if expected['status'] == 0 else 0
    components = {'status': status == expected['status'], 'length': length == expected['length'],
        'output_bytes': memory['arena'][start:start + written] == wanted[start:start + written],
        'preserved_bytes': memory['arena'][:start] == wanted[:start] and memory['arena'][start + written:] == wanted[start + written:]}
    return {'id': case['id'], 'passed': all(components.values()), 'components': components,
            'return_provenance': observed['provenance']}


def grading_action(source, cases, oracle, *, language='c'):
    semantic = ('benchmarks/scripts/capabilities/buffers.py',
                'benchmarks/scripts/grading/__init__.py')
    frozen = {name: (ROOT / name).read_bytes() for name in semantic}
    if language not in ('c', 'cpp'):
        raise ValueError('Unsupported paired buffer output language')
    frozen.update(c_runtime.sources('c_execute'))
    if language == 'cpp':
        from .cpp_buffer import runtime as compiler
        frozen.update(compiler.sources())
        filename, contract, compilation = 'solution.cpp', compiler.header(), compiler.policy()
        environment = {'compile': compiler.platform(), 'execution': c_runtime.platform(True)}
    else:
        frozen.update(c_runtime.sources('c_compile'))
        filename, contract, compilation = 'solution.c', HEADER, c_runtime.compile_profile()
        environment = c_runtime.platform(True)
    stable_oracle = {key: value for key, value in oracle.items() if key != 'cache_hit'}
    return action_cache.Action('capability_cpp_buffer_grade_v1' if language == 'cpp' else 'capability_c_buffer_grade_v1',
        {**frozen, filename: source.encode('utf-8'), 'contract.h': contract.encode(),
         'vectors.json': action_cache.canonical(cases), 'oracle.json': action_cache.canonical(stable_oracle),
         'compile-profile.json': action_cache.canonical(compilation),
         'execute-profile.json': action_cache.canonical(c_runtime.execute_profile())},
        arguments=['host-owned-cpp20-buffer-contract-v1' if language == 'cpp' else 'host-owned-c17-buffer-contract-v1'], platform=environment)


def grade(source, cases, oracle, *, cache=None, language='c'):
    from .buffer_evidence import require_validation
    require_validation()
    if language == 'cpp':
        from .cpp_buffer.evidence import require_validation as cpp_gate
        cpp_gate()
    if (oracle['binary_sha256'] == '' or oracle.get('vectors_sha256') != hashlib.sha256(action_cache.canonical(cases)).hexdigest()
            or [row['id'] for row in cases] != [row['id'] for row in oracle['observations']]):
        raise ValueError('Buffer vectors and frozen original oracle differ')
    c_runtime.admit()
    action = grading_action(source, cases, oracle, language=language)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        value = json.loads(local.get(hit['output_files']['grade.json']['digest']))
        return {**value, 'grading_id': action.key, 'cache_hit': True}
    if language == 'cpp':
        from .cpp_buffer.runtime import compile as compile_cpp
        compiled = compile_cpp(source, cache=cache)
    else:
        compiled = c_runtime.compile(source, header=HEADER, cache=cache)
    if compiled['status'] != 'compiled':
        result = {'status': compiled['status'], 'compiled': False, 'passed': 0, 'total': len(cases),
                'capabilities': {}, 'compile_action': compiled['action_id']}
        local.commit(action, {'grade.json': action_cache.canonical(result)})
        return {**result, 'grading_id': action.key, 'cache_hit': False}
    checks = []
    for case, expected in zip(cases, oracle['observations']):
        # Only this invocation crosses into the candidate worker. Reference
        # outputs, the target and every other vector stay in the parent.
        observed = c_runtime.invoke(compiled['elf'], invocation(case), cache=cache)
        checks.append(compare(observed, expected, case))
    scores = {}
    for capability in ('C1', 'C2', 'C3', 'C4'):
        relevant = [check for case, check in zip(cases, checks) if capability in case['capabilities']]
        scores[capability] = {'passed': sum(item['passed'] for item in relevant), 'total': len(relevant)}
    result = {'status': 'graded', 'compiled': True, 'passed': sum(check['passed'] for check in checks), 'total': len(checks),
            'capabilities': scores, 'checks': checks, 'compile_action': compiled['action_id'],
            'oracle_id': oracle['reference_id'], 'return_provenance': 'host_emulator_return_v1'}
    local.commit(action, {'grade.json': action_cache.canonical(result)})
    return {**result, 'grading_id': action.key, 'cache_hit': False}
