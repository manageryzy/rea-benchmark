"""Independent T3 original, submitted-description, invocation and grading actions."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime as images
from grading.c_executor import runtime as executor
from grading.c_executor.context import host
from capabilities import build, elf
from . import contracts

ROOT = action_cache.ROOT
PREFIX = 'benchmarks/suites/capabilities-v1/sources/pointers/'
MODULE = 'benchmarks/scripts/capabilities/pointers/'
POLICY = ROOT / 'benchmarks/config/pointer-probes.json'
COMMON = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py',
    'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py',
    'benchmarks/scripts/capabilities/__init__.py', 'benchmarks/scripts/capabilities/abi/__init__.py',
    'benchmarks/scripts/capabilities/abi/compiler.py', 'benchmarks/scripts/capabilities/abi/link.ld')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def policy():
    value = json.loads(POLICY.read_bytes())
    if (value.get('schema_version') != 1 or value.get('platform') != 'linux-x86_64'
            or value.get('compiler_image') != 'cpp'
            or any(type(n) is not int or n <= 0 for n in value['limits'].values())):
        raise ValueError('Unsupported pointer-probe policy')
    return value


def original_aliases(variant):
    if variant not in contracts.VARIANTS:
        raise ValueError('Unknown original pointer-record layout')
    result = {PREFIX + 'record.hpp': PREFIX + variant + '.hpp'}
    if variant.startswith('indirect'):
        result[PREFIX + 'original.cpp'] = PREFIX + 'indirect_original.cpp'
    return result


def sources(mode, variant=None):
    names = list(COMMON) + [MODULE + '__init__.py']
    if mode == 'original':
        if variant not in contracts.VARIANTS:
            raise ValueError('Unknown original pointer-record layout')
        driver = 'indirect_original.cpp' if variant.startswith('indirect') else 'original.cpp'
        names += [MODULE + 'build_worker.py', PREFIX + driver, PREFIX + 'fixtures.hpp',
                  PREFIX + variant + '.hpp', 'benchmarks/suites/capabilities-v1/sources/abi/helpers.cpp']
    elif mode == 'probe':
        names += [MODULE + name for name in ('probe_worker.py', 'artifacts.py', 'codegen.py')]
        names += ['benchmarks/scripts/grading/artifacts.py', PREFIX + 'fixtures.hpp']
    elif mode == 'native':
        names += [MODULE + 'native_worker.py']
    else:
        raise ValueError('Unknown pointer compiler stage')
    return {name: (ROOT / name).read_bytes() for name in names}


def platform(mode):
    value = {'compiler_image': image_manifest.load('cpp')['id'], **host()}
    if mode == 'original': value['binutils_image'] = build.tooling()[0]
    return value


def _run(request, frozen, environment, values, variant=None):
    mode = request['mode']
    if frozen != sources(mode, variant) or values != policy() or environment != platform(mode):
        raise RuntimeError('Pointer-probe inputs changed before worker launch')
    with (images.image('cpp') as (base, pinned),
          build.tools_image() if mode == 'original' else nullcontext(None) as addon,
          tempfile.TemporaryDirectory(prefix='rea-pointer-worker-', dir='/tmp') as temporary):
        if pinned['id'] != environment['compiler_image']:
            raise RuntimeError('Pointer-probe compiler image changed after hashing')
        work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
        work.mkdir(); snapshot.mkdir()
        for name, raw in frozen.items():
            path = snapshot / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        if mode == 'original':
            for logical, physical in original_aliases(variant).items():
                (snapshot / logical).write_bytes(frozen[physical])
        (snapshot / 'profile.json').write_bytes(action_cache.canonical(values))
        arguments = images.sandbox(base, pinned['manifest'], work, source=snapshot)
        env = {**pinned['manifest']['environment'], 'REA_ABI_PROBE_WORKER': '1'}
        if addon:
            tools, identity, manifest = addon
            if identity != environment['binutils_image']:
                raise RuntimeError('Pointer-probe binutils image changed after hashing')
            arguments += ['--ro-bind', str(tools), '/tools']
            env['LD_LIBRARY_PATH'] = ':'.join(sorted({'/tools/' + str(Path(name).parent)
                for name, item in manifest['files'].items() if item['elf']}))
        worker = {'original': 'build_worker.py', 'probe': 'probe_worker.py', 'native': 'native_worker.py'}[mode]
        arguments += ['/usr/bin/python3', '-I', '-S', '-B', '/environment/' + MODULE + worker]
        token = images._authorized.set(tuple(arguments))
        try:
            reply = process.run(arguments, input=action_cache.canonical(request), timeout=150,
                                output_limit=values['limits']['pipe_bytes'], env=env)
        finally:
            images._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed pointer-probe worker failed: ' + reply.stderr.decode(errors='replace')[-6000:])
        result = json.loads(reply.stdout)
        if (result.get('host') != host() or frozen != sources(mode, variant)
                or values != policy() or environment != platform(mode)):
            raise RuntimeError('Pointer-probe sources, policy or runtime changed during execution')
        result['runtime'] = proof(frozen, environment, values)
        return result


def proof(frozen, environment, values):
    return {**environment, 'policy_sha256': sha(action_cache.canonical(values)),
            'source_sha256': {name: sha(raw) for name, raw in frozen.items()}}


def compile_action(mode, request, variant=None):
    frozen, environment, values = sources(mode, variant), platform(mode), policy()
    inputs = {**frozen, 'profile.json': action_cache.canonical(values), 'request.json': action_cache.canonical(request)}
    if mode == 'original': inputs['source-alias.json'] = action_cache.canonical(original_aliases(variant))
    action = action_cache.Action('capability_pointer_' + mode + '_v1', inputs,
        arguments=['closed-pointer-object-' + mode], platform=environment, timeout_seconds=150)
    return action, frozen, environment, values


def _cached_compile(mode, request, variant=None, cache=None):
    action, frozen, environment, values = compile_action(mode, request, variant)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
    else:
        result = _run(request, frozen, environment, values, variant)
        local.commit(action, {'result.json': action_cache.canonical(result)})
    if result.get('runtime') != proof(frozen, environment, values):
        raise ValueError('Pointer compiler cache has an incorrect runtime binding')
    return {**result, 'action_id': action.key, 'cache_hit': bool(hit)}


def validate_target(target):
    variant = target['variant']
    action = compile_action('original', {'mode': 'original'}, variant)[0]
    private, public = elf.stripped_matches(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']))
    names = ('original_callee', 'original_caller', 'original_reference', 'recovered_slot')
    symbols = private.symbols()
    if (target['build_id'] != action.key or target['binary_sha256'] != sha(public.raw)
            or any(sha(bytes.fromhex(target[n + '_obj_hex'])) != target[n + '_obj_sha256'] for n in ('original', 'helpers'))
            or any(n not in symbols for n in names)
            or target['entries'] != {n: symbols[n]['rva'] for n in names}
            or len(set(target['entries'].values())) != 4):
        raise ValueError('Original pointer-object build, object or endpoint integrity failed')


def original(variant='baseline', *, cache=None):
    result = _cached_compile('original', {'mode': 'original'}, variant, cache)
    private = elf.ELF(bytes.fromhex(result['private_hex']))
    target = {**result, 'variant': variant, 'family': 'variable_objects', 'convention': 'sysv_amd64',
              'build_id': result['action_id'], 'binary_sha256': sha(bytes.fromhex(result['public_hex'])),
              'entries': {n: private.symbols()[n]['rva'] for n in
                          ('original_callee', 'original_caller', 'original_reference', 'recovered_slot')}}
    validate_target(target)
    return target


def object_request(mode, target):
    return {'mode': mode, **{n + suffix: target[n + suffix] for n in ('original', 'helpers')
                             for suffix in ('_obj_hex', '_obj_sha256')}}


def probe(raw, target, *, cache=None):
    validate_target(target)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 262144:
        raise ValueError('Pointer artifact must contain bounded exact bytes')
    request = {**object_request('probe', target), 'artifact_hex': raw.hex(), 'binary_sha256': target['binary_sha256']}
    result = _cached_compile('probe', request, cache=cache)
    if result['status'] == 'compiled' and any(result[n + '_obj_sha256'] != target[n + '_obj_sha256'] for n in ('original', 'helpers')):
        raise ValueError('Generated pointer probe lost its unchanged original-object binding')
    return result


def vectors(nonces=None):
    value = list(contracts.NONCES if nonces is None else nonces)
    if (not 1 <= len(value) <= 32
            or any(type(n) is not int or not 0 <= n < 2**64 for n in value)
            or len(set(value)) != len(value)):
        raise ValueError('Invalid pointer-probe vector set')
    return value


def native(target, *, nonces=None, cache=None):
    validate_target(target)
    nonces = vectors(nonces)
    result = _cached_compile('native', {**object_request('native', target), 'nonces': nonces}, cache=cache)
    if (any(result[n + '_obj_sha256'] != target[n + '_obj_sha256'] for n in ('original', 'helpers'))
            or [row['nonce'] for row in result['observations']] != nonces
            or any(len(bytes.fromhex(row['output_hex'])) != contracts.WIDTH for row in result['observations'])):
        raise ValueError('Native original pointer-reference calibration cache mismatch')
    return result


def observe(image, entry, nonce, cache=None):
    invocation = {'id': nonce % 10**12, 'return_type': 'i64', 'callbacks': [],
        'arguments': [nonce, {'region': 'output', 'offset': 0}],
        'regions': [{'id': 'output', 'data_hex': 'cd' * contracts.WIDTH,
                     'readable': [[0, contracts.WIDTH]], 'writable': [[0, contracts.WIDTH]]}]}
    result = executor.invoke(image, invocation, entry=entry, cache=cache)
    if result['status'] != 'returned':
        return {'returned': False, 'status': result['status'], 'output_hex': None, 'action_id': result['action_id']}
    return {'returned': True, 'status': int(bytes.fromhex(result['return_record']['value_hex']).decode('ascii')),
            'output_hex': next(r['data_hex'] for r in result['regions'] if r['id'] == 'output'), 'action_id': result['action_id']}


def grade_action(raw, target, nonces):
    frozen = sources('probe')
    frozen.update({MODULE + name: (ROOT / (MODULE + name)).read_bytes() for name in ('runtime.py', 'contracts.py')})
    frozen.update(executor.sources('c_execute'))
    return action_cache.Action('capability_pointer_grade_v1',
        {**frozen, 'artifact.json': raw, 'profile.json': action_cache.canonical(policy()),
         'original-reference.elf': bytes.fromhex(target['private_hex']),
         'original.o': bytes.fromhex(target['original_obj_hex']), 'helpers.o': bytes.fromhex(target['helpers_obj_hex']),
         'contract.json': action_cache.canonical({k: target[k] for k in ('variant', 'build_id', 'binary_sha256', 'entries')}),
         'nonces.json': action_cache.canonical(nonces), 'execution.json': action_cache.canonical(executor.execute_profile())},
        arguments=['T3', 'both-original-endpoint-directions'],
        platform={'compiler': platform('probe'), 'execution': executor.platform(True)})


def grade(raw, target, *, nonces=None, cache=None):
    from .evidence import require_validation
    require_validation(); executor.admit()
    validate_target(target)
    nonces = vectors(nonces)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 262144:
        raise ValueError('Invalid pointer-description transport')
    action = grade_action(raw, target, nonces)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['grade.json']['digest']))
        if (result.get('original_build_id') != target['build_id'] or result.get('capability_ids') != ['T3']
                or result.get('original_obj_sha256') != target['original_obj_sha256']):
            raise ValueError('Pointer grade cache has a different original/score binding')
        return {**result, 'grading_id': action.key, 'cache_hit': True}
    compiled = probe(raw, target, cache=cache)
    result = {'status': compiled['status'], 'artifact_valid': compiled['artifact_valid'],
        'capability_ids': ['T3'], 'original_build_id': target['build_id'],
        'original_obj_sha256': target['original_obj_sha256'], 'compile_id': compiled['action_id'],
        'return_provenance': 'not_executed', 'passed': 0, 'total': 2 * len(nonces), 'checks': []}
    if compiled['status'] == 'compiled':
        result['return_provenance'] = 'host_emulator_return_v1'
        for nonce in nonces:
            expected = observe(bytes.fromhex(target['private_hex']), 'original_reference', nonce, cache)
            if not expected['returned'] or expected['status'] != 0:
                raise RuntimeError('Original pointer-reference violated its execution contract')
            for direction, entry in (('original_caller_to_replacement', 'original_caller'),
                                     ('bridge_to_original_callee', 'generated_bridge')):
                actual = observe(bytes.fromhex(compiled['private_hex']), entry, nonce, cache)
                result['checks'].append({'nonce': nonce, 'direction': direction, 'observation': actual,
                    'reference_action': expected['action_id'],
                    'passed': actual['returned'] and actual['status'] == 0 and actual['output_hex'] == expected['output_hex']})
        result['status'] = 'graded'
        result['passed'] = sum(row['passed'] for row in result['checks'])
    else:
        result['reason'] = compiled.get('reason')
    result['task_success'] = result['passed'] == result['total']
    result['capabilities'] = {'T3': {'passed': result['passed'], 'total': result['total']}}
    local.commit(action, {'grade.json': action_cache.canonical(result)})
    return {**result, 'grading_id': action.key, 'cache_hit': False}
