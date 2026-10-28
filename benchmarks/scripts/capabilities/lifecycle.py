"""C5/C6 explicit lifecycle conditions, with separate original and guest state.

Candidate storage is opaque. Only externally visible results, callback traces and
preserved memory are compared; a different correct state representation passes.
Original executables and future inputs never cross into the candidate worker.
"""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime as sandbox
from grading.c_executor import runtime as executor
from grading.c_executor.context import host
from . import build
from .elf import stripped_matches

ROOT = action_cache.ROOT
FAMILIES = {'callback_policy': ['C5', 'C6'], 'session_state': ['C6']}
HEADER = '''#include <stdint.h>
#include <stddef.h>
typedef uint64_t (*session_callback)(uint64_t operation, uint64_t value, uint64_t index);
int32_t reconstructed(uint8_t *state, uint32_t operation, uint32_t value,
                      uint32_t limit, uint64_t *output, session_callback callback);
'''
REFERENCE_SOURCES = ('benchmarks/scripts/capabilities/lifecycle_reference_worker.py',
    'benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py')
MAXIMUM = 2**64 - 1


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def original(family, *, cache=None):
    if family not in FAMILIES:
        raise ValueError('Unknown lifecycle family')
    from .lifecycle_build import original as compile_original
    value = compile_original(family, cache=cache)
    private, _ = stripped_matches(bytes.fromhex(value['private_hex']), bytes.fromhex(value['public_hex']))
    return {**value, 'entry_rva': private.symbols()['family_session']['rva']}


def vectors(family):
    if family not in FAMILIES:
        raise ValueError('Unknown lifecycle family')
    scenarios = []
    def add(operations):
        steps = [{'id': index, 'instance': instance, 'operation': operation, 'value': value,
                  'limit': limit, 'response': response, 'seed': (index * 29 + 53) % 256}
                 for index, (instance, operation, value, limit, response) in enumerate(operations)]
        scenarios.append({'id': len(scenarios), 'steps': steps})
    # Two live objects, reads between mutations, independent destroy/recreate,
    # reset of a used instance and failed operations followed by valid work.
    add([(0, 1, 2, 0, 0), (0, 0, 0xfffffff1, 3, 7), (1, 0, 23, 2, 5),
         (0, 1, 13, 0, 11), (1, 1, 255, 0, 9), (0, 4, 0, 0, 0),
         (0, 0, 99, 1, 0), (1, 2, 0x80000001, 4, 3), (0, 1, 10, 0, 8),
         (1, 1, 0, 0, 17), (0, 3, 0, 0, 2), (1, 4, 0, 0, 0),
         (0, 1, 7, 0, 0), (0, 0, 27, 1, 13), (0, 4, 0, 0, 0),
         (1, 3, 0, 0, 19), (0, 3, 0, 0, 23), (0, 3, 0, 0, 0)])
    add([(0, 0, 0xffffffff, 0, 0), (0, 0, 123, 1, MAXIMUM),
         (0, 4, 0, 0, 0), (0, 0, 0xffffffff, 1, 1),
         (0, 1, 17, 0, MAXIMUM), (0, 4, 0, 0, 0),
         (0, 1, 17, 0, 0x100000007), (0, 1, 8, 0, 5),
         (0, 2, 5, 2, MAXIMUM), (0, 4, 0, 0, 0),
         (0, 2, 5, 2, 4), (0, 1, 256, 0, 3),
         (0, 3, 0, 0, MAXIMUM), (0, 4, 0, 0, 0), (0, 3, 0, 0, 1),
         (0, 0, 2, 32, 0), (0, 99, 0, 0, 0)])
    # Repeated byte values do not replace the per-instance count; unsigned
    # arithmetic wraps. For C6, value is the full uint32 input domain.
    add([(1, 0, 0x80000000, 7, 0), (0, 0, 1, 8, 0)] +
        [(i % 2, 1, 0xff, 0, i * 3) for i in range(10)] +
        [(1, 4, 0, 0, 0), (0, 4, 0, 0, 0), (1, 2, 3, 0, 0),
         (1, 4, 0, 0, 0), (1, 2, 9, 3, 0), (1, 1, 0xffffffff, 0, 0)])
    validate(scenarios)
    return scenarios


def validate(scenarios):
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 12:
        raise ValueError('Lifecycle scenarios must be bounded')
    seen = set()
    count = 0
    for scenario in scenarios:
        if (not isinstance(scenario, dict) or set(scenario) != {'id', 'steps'}
                or type(scenario['id']) is not int or not 0 <= scenario['id'] < 10**6
                or scenario['id'] in seen or not isinstance(scenario['steps'], list)
                or not 1 <= len(scenario['steps']) <= 32):
            raise ValueError('Invalid lifecycle scenario')
        seen.add(scenario['id'])
        ids = set()
        for step in scenario['steps']:
            fields = {'id', 'instance', 'operation', 'value', 'limit', 'response', 'seed'}
            if not isinstance(step, dict) or set(step) != fields:
                raise ValueError('Invalid lifecycle step fields; oracle data is forbidden')
            bounds = {'id': 10**6 - 1, 'instance': 1, 'operation': 2**32 - 1,
                      'value': 2**32 - 1, 'limit': 2**32 - 1, 'response': MAXIMUM, 'seed': 255}
            if any(type(step[key]) is not int or not 0 <= step[key] <= maximum for key, maximum in bounds.items()):
                raise ValueError('Invalid lifecycle step value')
            if step['id'] in ids:
                raise ValueError('Duplicate lifecycle invocation')
            ids.add(step['id'])
            count += 1
    if count > 256:
        raise ValueError('Too many lifecycle invocations')


def initial(seed):
    arena = bytearray((seed + 37 * index) % 256 for index in range(512))
    for offset in (32, 160):
        arena[offset:offset + 64] = bytes(64)
    return bytes(arena)


def validate_oracle(scenarios, oracle):
    """Admit complete original records before provider work or candidate execution."""
    validate(scenarios)
    order = [(scenario['id'], step['id']) for scenario in scenarios for step in scenario['steps']]
    if (not isinstance(oracle, dict) or oracle.get('vectors_sha256') != sha(action_cache.canonical(scenarios))
            or not isinstance(oracle.get('observations'), list)
            or len(oracle['observations']) != len(order) or any(not isinstance(row, dict) for row in oracle['observations'])
            or [(row.get('scenario_id'), row.get('id')) for row in oracle['observations']] != order):
        raise ValueError('Lifecycle vectors and original observations differ')
    for name in ('binary_sha256', 'reference_id'):
        action_cache.valid_digest({'hash': oracle.get(name), 'size_bytes': 0})
    steps = (step for scenario in scenarios for step in scenario['steps'])
    for row, step in zip(oracle['observations'], steps):
        if (set(row) != {'scenario_id', 'id', 'status', 'arena_hex', 'callbacks'}
                or type(row['id']) is not int or type(row['scenario_id']) is not int
                or type(row['status']) is not int or row['status'] not in (0, -1, -2, -3, -4, -5)
                or not isinstance(row['arena_hex'], str) or len(bytes.fromhex(row['arena_hex'])) != 512
                or not isinstance(row['callbacks'], list) or len(row['callbacks']) > 1):
            raise ValueError('Invalid lifecycle original return or memory record')
        for event in row['callbacks']:
            if (not isinstance(event, dict) or set(event) != {'arguments', 'return'}
                    or not isinstance(event['arguments'], list) or len(event['arguments']) != 3
                    or any(type(value) is not int or not 0 <= value <= MAXIMUM for value in event['arguments'])
                    or type(event['return']) is not int or event['return'] != step['response']):
                raise ValueError('Invalid lifecycle original callback record')
    return order


def invocation(step, arena):
    if not isinstance(arena, bytes) or len(arena) != 512:
        raise ValueError('Lifecycle storage must be a bounded prior snapshot')
    memory = bytearray(arena)
    memory[256:264] = bytes((step['seed'] + 19 * index) % 256 for index in range(8))
    offset = 32 + 128 * step['instance']
    return {'id': step['id'], 'return_type': 'i32',
        'regions': [{'id': 'arena', 'data_hex': memory.hex(),
                     'readable': [[offset, 64], [256, 8]], 'writable': [[offset, 64], [256, 8]]}],
        'arguments': [{'region': 'arena', 'offset': offset}, step['operation'], step['value'], step['limit'],
                      {'region': 'arena', 'offset': 256}, {'callback': 'policy'}],
        'callbacks': [{'id': 'policy', 'returns': [step['response']]}]}


def reference(target, scenarios, *, cache=None):
    validate(scenarios)
    binary = bytes.fromhex(target['public_hex'])
    frozen = {name: (ROOT / name).read_bytes() for name in REFERENCE_SOURCES}
    environment = {'image_id': image_manifest.load('cpp')['id'], **host()}
    action = action_cache.Action('capability_lifecycle_reference_v1',
        {**frozen, 'target': binary, 'scenarios.json': action_cache.canonical(scenarios)},
        arguments=['original-lifecycle-protocol-v1'], platform=environment, timeout_seconds=20)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['reference.json']['digest']))
    else:
        with sandbox.image('cpp') as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-lifecycle-reference-', dir='/tmp') as temporary:
            if pinned['id'] != environment['image_id']:
                raise RuntimeError('Lifecycle original image changed after hashing')
            work, source = Path(temporary) / 'work', Path(temporary) / 'source'
            work.mkdir()
            source.mkdir()
            for name, raw in frozen.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            command = sandbox.sandbox(base, pinned['manifest'], work, source=source) + [
                '/usr/bin/python3', '-I', '-S', '-B', '/environment/benchmarks/scripts/capabilities/lifecycle_reference_worker.py']
            token = sandbox._authorized.set(tuple(command))
            try:
                reply = process.run(command, input=action_cache.canonical({'target_hex': binary.hex(), 'scenarios': scenarios}),
                    timeout=20, output_limit=4 * 1024**2,
                    env={**pinned['manifest']['environment'], 'REA_LIFECYCLE_REFERENCE': '1'})
            finally:
                sandbox._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Lifecycle original failed: ' + reply.stderr.decode(errors='replace')[-2000:])
            result = json.loads(reply.stdout)
            if result['host'] != host() or frozen != {name: (ROOT / name).read_bytes() for name in REFERENCE_SOURCES}:
                raise RuntimeError('Lifecycle original source or host changed during execution')
        result.update(binary_sha256=sha(binary), vectors_sha256=sha(action_cache.canonical(scenarios)), runtime=environment)
        local.commit(action, {'reference.json': action_cache.canonical(result)})
    if (result.get('binary_sha256') != sha(binary) or result.get('runtime') != environment
            or result.get('vectors_sha256') != sha(action_cache.canonical(scenarios))):
        raise ValueError('Lifecycle original oracle differs from its action')
    return {**result, 'reference_id': action.key, 'cache_hit': bool(hit)}


def compare(observed, expected, step, before):
    returned = observed.get('status') == 'returned' and observed.get('return_record') is not None
    if not returned:
        return {'id': step['id'], 'passed': False, 'failure': observed.get('status'), 'components': {}}
    status = int(bytes.fromhex(observed['return_record']['value_hex']).decode('ascii'))
    arena = bytes.fromhex(observed['regions'][0]['data_hex'])
    wanted = bytes.fromhex(expected['arena_hex'])
    events = [{'arguments': event['arguments'][:3], 'return': event['return']} for event in observed['callbacks']]
    permitted = set(range(32 + 128 * step['instance'], 96 + 128 * step['instance'])) | set(range(256, 264))
    components = {'status': status == expected['status'], 'output_bytes': arena[256:264] == wanted[256:264],
        'callbacks': events == expected['callbacks'],
        'preserved_bytes': all(arena[index] == before[index] for index in range(512) if index not in permitted)}
    return {'id': step['id'], 'passed': all(components.values()), 'components': components,
            'return_provenance': observed['provenance']}


def grading_action(source, family, scenarios, oracle, *, language='c'):
    frozen = {'benchmarks/scripts/capabilities/lifecycle.py': (ROOT / 'benchmarks/scripts/capabilities/lifecycle.py').read_bytes()}
    frozen.update(executor.sources('c_execute'))
    if language == 'cpp':
        from .cpp_lifecycle import runtime as compiler
        frozen.update(compiler.sources())
        filename, declaration, compilation = 'solution.cpp', compiler.header(), compiler.policy()
        environment = {'compile': compiler.platform(), 'execution': executor.platform(True)}
    elif language == 'c':
        frozen.update(executor.sources('c_compile'))
        filename, declaration, compilation = 'solution.c', HEADER, executor.compile_profile()
        environment = executor.platform(True)
    else:
        raise ValueError('Unsupported lifecycle output language')
    return action_cache.Action('capability_cpp_lifecycle_grade_v1' if language == 'cpp' else 'capability_lifecycle_grade_v1',
        {**frozen, filename: source.encode('utf-8'), 'contract.h': declaration.encode(),
         'family.json': action_cache.canonical({'id': family, 'capabilities': FAMILIES[family]}),
         'scenarios.json': action_cache.canonical(scenarios),
         'oracle.json': action_cache.canonical({k: v for k, v in oracle.items() if k != 'cache_hit'}),
         'compile-profile.json': action_cache.canonical(compilation),
         'execute-profile.json': action_cache.canonical(executor.execute_profile())},
        arguments=['opaque-state-observable-effects-v1'], platform=environment)


def capability_passes(check, expected, step, family):
    """Separate callback/lifecycle evidence from persistent-state value evidence.

    Whole-task correctness still requires every component. The combined family
    does not award a C5 callback defect a C6 value defect when state/output remain
    correct. Both dimensions retain common status and memory-safety obligations.
    """
    parts = check['components']
    common = bool(parts) and parts['status'] and parts['preserved_bytes']
    scores = {'C6': common and parts.get('output_bytes', False)
              and (parts.get('callbacks', False) if family == 'session_state' else True)}
    if family == 'callback_policy':
        preserves_output = expected['status'] != 0 or step['operation'] not in (1, 4)
        scores['C5'] = common and parts.get('callbacks', False) and (parts.get('output_bytes', False) if preserves_output else True)
    return {capability: bool(scores[capability]) for capability in FAMILIES[family]}


def grade(source, family, scenarios, oracle, *, cache=None, language='c'):
    from .lifecycle_evidence import require_validation
    require_validation()
    if language == 'cpp':
        from .cpp_lifecycle.evidence import require_validation as cpp_gate
        cpp_gate()
    if family not in FAMILIES:
        raise ValueError('Unknown lifecycle family')
    order = validate_oracle(scenarios, oracle)
    executor.admit()
    action = grading_action(source, family, scenarios, oracle, language=language)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        return {**json.loads(local.get(hit['output_files']['grade.json']['digest'])), 'grading_id': action.key, 'cache_hit': True}
    if language == 'cpp':
        from .cpp_lifecycle.runtime import compile as compile_cpp
        compiled = compile_cpp(source, cache=local)
    else:
        compiled = executor.compile(source, header=HEADER, cache=local)
    checks = []
    if compiled['status'] == 'compiled':
        expected = iter(oracle['observations'])
        for scenario in scenarios:
            arena = initial(scenario['steps'][0]['seed'])
            image_state, failed = None, False
            for step in scenario['steps']:
                wanted = next(expected)
                if failed:
                    check = {'id': step['id'], 'passed': False, 'failure': 'prior_execution_fault', 'components': {}}
                else:
                    request = invocation(step, arena)
                    before = bytes.fromhex(request['regions'][0]['data_hex'])
                    observed = executor.invoke(compiled['elf'], request, state=image_state, cache=local)
                    check = compare(observed, wanted, step, before)
                    failed = observed['status'] != 'returned'
                    if not failed:
                        # Preserve the candidate's own context and globals, never
                        # replace either with the original program's state.
                        arena = bytes.fromhex(observed['regions'][0]['data_hex'])
                        image_state = observed['state']
                checks.append({**check, 'scenario_id': scenario['id'],
                               'capability_passed': capability_passes(check, wanted, step, family)})
    result = {'status': 'graded' if compiled['status'] == 'compiled' else compiled['status'],
        'compiled': compiled['status'] == 'compiled', 'passed': sum(row['passed'] for row in checks),
        'total': len(order), 'checks': checks, 'compile_action': compiled['action_id'],
        'capabilities': {cap: {'passed': sum(row['capability_passed'][cap] for row in checks), 'total': len(order)} for cap in FAMILIES[family]},
        'oracle_id': oracle['reference_id'],
        'return_provenance': 'host_emulator_return_v1' if compiled['status'] == 'compiled' else 'not_executed'}
    local.commit(action, {'grade.json': action_cache.canonical(result)})
    return {**result, 'grading_id': action.key, 'cache_hit': False}
