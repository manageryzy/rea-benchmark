"""Independent original-build, description-compile and host-execution actions."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime as images
from grading.c_executor import runtime as executor
from grading.c_executor.context import host
from capabilities import build as family_build, elf
from . import contracts

ROOT = action_cache.ROOT
POLICY = ROOT / 'benchmarks/config/abi-probes.json'
PREFIX = 'benchmarks/suites/capabilities-v1/sources/abi/'
COMMON = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py',
    'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py',
    'benchmarks/scripts/capabilities/abi/link.ld', 'benchmarks/scripts/capabilities/__init__.py',
    'benchmarks/scripts/capabilities/abi/__init__.py', 'benchmarks/scripts/capabilities/abi/compiler.py')
PROBE = ('benchmarks/scripts/grading/artifacts.py', 'benchmarks/scripts/capabilities/abi/contracts.py',
    'benchmarks/scripts/capabilities/abi/artifacts.py', 'benchmarks/scripts/capabilities/abi/codegen.py',
    'benchmarks/scripts/capabilities/abi/machine_codegen.py')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def policy():
    value = json.loads(POLICY.read_bytes())
    if (value.get('schema_version') != 1 or value.get('platform') != 'linux-x86_64'
            or value.get('compiler_image') != 'cpp' or tuple(value['families']) != contracts.FAMILIES
            or tuple(value['conventions']) != contracts.CONVENTIONS
            or any(type(number) is not int or number <= 0 for number in value['limits'].values())):
        raise ValueError('Unsupported ABI probe policy')
    return value


def sources(mode, family=None, variant=None):
    names = list(COMMON)
    if mode == 'original':
        names += ['benchmarks/scripts/capabilities/abi/build_worker.py']
        names += [PREFIX + name for name in ('original.cpp', 'default_slot.cpp', 'helpers.cpp')]
        if variant is None:
            names += [PREFIX + family + '.hpp']
        else:
            from .variants import select
            names += [select(family, variant)['header']]
        names += ['benchmarks/scripts/capabilities/build.py']
    else:
        names += ['benchmarks/scripts/capabilities/abi/probe_worker.py']
        names += list(PROBE)
    return {name: (ROOT / name).read_bytes() for name in names}


def original_policy(family, variant=None):
    values = policy()
    if variant is not None:
        from .variants import select
        values = {**values, 'source_aliases': {PREFIX + family + '.hpp': select(family, variant)['header']}}
    return values


def platform(mode):
    result = {'compiler_image': image_manifest.load('cpp')['id'], **host()}
    if mode == 'original':
        result['binutils_image'] = family_build.tooling()[0]
    return result


def _compile(request, frozen, environment, values, *, variant=None):
    expected_policy = original_policy(request.get('family'), variant) if request['mode'] == 'original' else policy()
    if (frozen != sources(request['mode'], request.get('family'), variant) or values != expected_policy
            or environment != platform(request['mode'])):
        raise RuntimeError('ABI compiler inputs changed before worker launch')
    context = family_build.tools_image() if request['mode'] == 'original' else nullcontext(None)
    with images.image('cpp') as (base, pinned), context as addon, tempfile.TemporaryDirectory(prefix='rea-abi-build-', dir='/tmp') as temporary:
        if pinned['id'] != environment['compiler_image']:
            raise RuntimeError('ABI compiler image changed after hashing')
        temporary = Path(temporary)
        work, snapshot = temporary / 'work', temporary / 'source'
        work.mkdir()
        snapshot.mkdir()
        for name, raw in frozen.items():
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        # Aliases are declared in the action profile. The unchanged original
        # worker sees the selected authored header at its fixed logical path.
        for logical, physical in values.get('source_aliases', {}).items():
            destination = snapshot.joinpath(*action_cache.relative(logical).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(frozen[physical])
        (snapshot / 'profile.json').write_bytes(action_cache.canonical(values))
        arguments = images.sandbox(base, pinned['manifest'], work, source=snapshot)
        env = {**pinned['manifest']['environment'], 'REA_ABI_PROBE_WORKER': '1'}
        if addon:
            tools, identity, manifest = addon
            if identity != environment['binutils_image']:
                raise RuntimeError('ABI strip/objdump image changed after hashing')
            arguments += ['--ro-bind', str(tools), '/tools']
            env['LD_LIBRARY_PATH'] = ':'.join(sorted({'/tools/' + str(Path(name).parent) for name, item in manifest['files'].items() if item['elf']}))
        worker = 'build_worker.py' if request['mode'] == 'original' else 'probe_worker.py'
        arguments += ['/usr/bin/python3', '-I', '-S', '-B', '/environment/benchmarks/scripts/capabilities/abi/' + worker]
        token = images._authorized.set(tuple(arguments))
        try:
            reply = process.run(arguments, input=action_cache.canonical(request), timeout=150,
                                output_limit=values['limits']['pipe_bytes'], env=env)
        finally:
            images._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed ABI compiler failed: ' + reply.stderr.decode(errors='replace')[-8000:])
        result = json.loads(reply.stdout)
        if (result.get('host') != host() or frozen != sources(request['mode'], request.get('family'), variant)
                or expected_policy != (original_policy(request.get('family'), variant) if request['mode'] == 'original' else policy())):
            raise RuntimeError('ABI compiler sources or host changed during execution')
        result['runtime'] = {**environment, 'policy_sha256': sha(action_cache.canonical(values)),
                             'source_sha256': {name: sha(raw) for name, raw in frozen.items()}}
        return result


def original_action(family, convention, variant=None):
    values = original_policy(family, variant)
    if family not in contracts.FAMILIES or convention not in contracts.CONVENTIONS:
        raise ValueError('Unknown original ABI build')
    frozen, environment = sources('original', family, variant), platform('original')
    request = {'mode': 'original', 'family': family, 'convention': convention}
    action = action_cache.Action('capability_abi_original_v1',
        {**frozen, 'profile.json': action_cache.canonical(values), 'request.json': action_cache.canonical(request)},
        arguments=['closed-original-object-and-stripped-ELF'], platform=environment, timeout_seconds=150)
    return action, request, frozen, environment, values


def original(family, convention, *, cache=None, variant=None):
    action, request, frozen, environment, values = original_action(family, convention, variant)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['build.json']['digest']))
    else:
        result = _compile(request, frozen, environment, values, variant=variant)
        local.commit(action, {'build.json': action_cache.canonical(result)})
    private, public = elf.stripped_matches(bytes.fromhex(result['private_hex']), bytes.fromhex(result['public_hex']))
    for name in ('original', 'helpers'):
        if sha(bytes.fromhex(result[name + '_obj_hex'])) != result[name + '_obj_sha256']:
            raise ValueError('Frozen ABI object changed')
    if result.get('runtime') != {**environment, 'policy_sha256': sha(action_cache.canonical(values)),
                                'source_sha256': {name: sha(raw) for name, raw in frozen.items()}}:
        raise ValueError('Original ABI build cache has an incorrect compiler attestation')
    symbols = private.symbols()
    required = ('original_callee', 'original_caller', 'original_reference', 'recovered_slot')
    if any(name not in symbols for name in required) or len({symbols[name]['rva'] for name in required}) != len(required):
        raise ValueError('Original ABI boundaries were removed or merged')
    return {**result, **({'variant': variant} if variant is not None else {}),
            'build_id': action.key, 'cache_hit': bool(hit), 'family': family,
            'convention': convention, 'binary_sha256': sha(public.raw),
            'entries': {name: symbols[name]['rva'] for name in required}}


def probe_action(raw, target, condition):
    if not isinstance(raw, bytes) or len(raw) > 262144 or condition not in contracts.CONDITIONS:
        raise ValueError('Invalid type/ABI artifact transport')
    values, frozen, environment = policy(), sources('probe'), platform('probe')
    contract = {key: target[key] for key in ('family', 'binary_sha256', 'original_obj_sha256', 'helpers_obj_sha256')}
    contract.update(condition=condition, convention=target['convention'], entry_rva=target['entries']['original_callee'])
    request = {'mode': 'probe', 'contract': contract, 'artifact_hex': raw.hex(),
               **{key: target[key] for key in ('original_obj_hex', 'helpers_obj_hex')}}
    action = action_cache.Action('capability_abi_probe_compile_v1',
        {**frozen, 'profile.json': action_cache.canonical(values), 'request.json': action_cache.canonical(request)},
        arguments=['submitted-description-to-frozen-original-probes'], platform=environment, timeout_seconds=150)
    return action, request, frozen, environment, values


def probe(raw, target, condition, *, cache=None):
    action, request, frozen, environment, values = probe_action(raw, target, condition)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['probe.json']['digest']))
    else:
        result = _compile(request, frozen, environment, values)
        local.commit(action, {'probe.json': action_cache.canonical(result)})
    proof = {**environment, 'policy_sha256': sha(action_cache.canonical(values)),
             'source_sha256': {name: sha(data) for name, data in frozen.items()}}
    if result.get('runtime') != proof or result.get('status') == 'compiled' and any(result[name] != target[name] for name in ('original_obj_sha256', 'helpers_obj_sha256')):
        raise ValueError('Generated ABI probe lacks its original-object/compiler binding')
    return {**result, 'compile_id': action.key, 'cache_hit': bool(hit)}


def invocation(nonce, family):
    width = 8 * len(contracts.paths(family))
    return {'id': nonce % 10**12, 'return_type': 'i64', 'callbacks': [],
        'arguments': [nonce, {'region': 'output', 'offset': 0}],
        'regions': [{'id': 'output', 'data_hex': 'cd' * width, 'readable': [[0, width]], 'writable': [[0, width]]}]}


def observe(image, entry, nonce, family, cache=None):
    result = executor.invoke(image, invocation(nonce, family), entry=entry, cache=cache)
    if result.get('status') != 'returned':
        return {'status': result['status'], 'returned': False, 'output_hex': None, 'action_id': result['action_id']}
    status = int(bytes.fromhex(result['return_record']['value_hex']).decode('ascii'))
    output = next(region['data_hex'] for region in result['regions'] if region['id'] == 'output')
    return {'status': status, 'returned': True, 'output_hex': output, 'action_id': result['action_id']}


def grade_action(raw, target, condition, nonces):
    frozen = sources('probe')
    for name in ('runtime.py',):
        frozen['benchmarks/scripts/capabilities/abi/' + name] = (ROOT / ('benchmarks/scripts/capabilities/abi/' + name)).read_bytes()
    frozen.update(executor.sources('c_execute'))
    inputs = {**frozen, 'artifact.json': raw, 'profile.json': action_cache.canonical(policy()),
        'original.o': bytes.fromhex(target['original_obj_hex']), 'helpers.o': bytes.fromhex(target['helpers_obj_hex']),
        'original-reference.elf': bytes.fromhex(target['private_hex']),
        'contract.json': action_cache.canonical({key: target[key] for key in ('family', 'convention', 'binary_sha256', 'build_id', 'entries', 'original_obj_sha256', 'helpers_obj_sha256')}),
        'condition.json': action_cache.canonical(condition), 'nonces.json': action_cache.canonical(nonces),
        'execution-profile.json': action_cache.canonical(executor.execute_profile())}
    return action_cache.Action('capability_abi_grade_v1', inputs, arguments=['two-original-endpoint-directions', condition],
        platform={'compiler': platform('probe'), 'execution': executor.platform(True)})


def grade(raw, target, condition, *, nonces=None, cache=None):
    from .evidence import require_validation
    require_validation()
    executor.admit()
    nonces = list(contracts.NONCES if nonces is None else nonces)
    if not 1 <= len(nonces) <= 16 or len(set(nonces)) != len(nonces) or any(type(value) is not int or not 0 <= value < 2**64 for value in nonces):
        raise ValueError('Invalid ABI probe vector set')
    if not isinstance(raw, bytes) or len(raw) > 262144 or condition not in contracts.CONDITIONS:
        raise ValueError('Invalid ABI grade contract')
    private, public = elf.stripped_matches(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']))
    if sha(public.raw) != target['binary_sha256'] or any(sha(bytes.fromhex(target[name + '_obj_hex'])) != target[name + '_obj_sha256'] for name in ('original', 'helpers')):
        raise ValueError('Frozen ABI reference/object integrity failed')
    action = grade_action(raw, target, condition, nonces)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['grade.json']['digest']))
        if (result.get('original_build_id') != target['build_id'] or result.get('original_obj_sha256') != target['original_obj_sha256']
                or result.get('capability_ids') != [contracts.CONDITIONS[condition]]):
            raise ValueError('ABI grade cache differs from its original endpoint/score contract')
        return {**result, 'grading_id': action.key, 'cache_hit': True}
    compiled = probe(raw, target, condition, cache=cache)
    capability = contracts.CONDITIONS[condition]
    result = {'status': compiled['status'], 'artifact_valid': compiled['artifact_valid'], 'capability_ids': [capability],
              'compile_id': compiled['compile_id'], 'original_build_id': target['build_id'],
              'original_obj_sha256': target['original_obj_sha256'], 'return_provenance': 'host_emulator_return_v1',
              'passed': 0, 'total': len(nonces) * 2, 'checks': []}
    if compiled['status'] == 'compiled':
        original_image, probe_image = bytes.fromhex(target['private_hex']), bytes.fromhex(compiled['private_hex'])
        for nonce in nonces:
            expected = observe(original_image, 'original_reference', nonce, target['family'], cache)
            if not expected['returned'] or expected['status'] != 0:
                raise RuntimeError('Frozen original reference failed its own supported execution contract')
            for direction, entry in (('original_caller_to_replacement', 'original_caller'), ('bridge_to_original_callee', 'generated_bridge')):
                actual = observe(probe_image, entry, nonce, target['family'], cache)
                result['checks'].append({'nonce': nonce, 'direction': direction, 'passed': actual['returned'] and actual['status'] == 0 and actual['output_hex'] == expected['output_hex'],
                                         'observation': actual, 'reference_action': expected['action_id']})
        result['passed'] = sum(check['passed'] for check in result['checks'])
        result['status'] = 'graded'
    else:
        result['reason'] = compiled.get('reason')
    result['capabilities'] = {capability: {'passed': result['passed'], 'total': result['total']}}
    result['task_success'] = result['passed'] == result['total']
    local.commit(action, {'grade.json': action_cache.canonical(result)})
    return {**result, 'grading_id': action.key, 'cache_hit': False}
