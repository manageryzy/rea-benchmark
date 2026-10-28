"""Shared frozen-original object probes for new T4/T6 paths. Existing T3 and ABI action identities are unchanged."""
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
COMMON = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py', 'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py', 'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py', 'benchmarks/scripts/capabilities/__init__.py', 'benchmarks/scripts/capabilities/abi/__init__.py', 'benchmarks/scripts/capabilities/abi/compiler.py', 'benchmarks/scripts/capabilities/abi/link.ld')

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

class Engine:

    def __init__(self, *, module, prefix, policy, contracts, capability, namespace, family, validation):
        self.ROOT = action_cache.ROOT
        self.MODULE, self.PREFIX, self.POLICY = (module, prefix, Path(policy))
        self.contracts, self.capability = (contracts, capability)
        self.namespace, self.family, self.require_validation = (namespace, family, validation)
        self.HOST_SOURCE = 'benchmarks/scripts/capabilities/object_probes.py'
        self._loaded_sources = {name: sha((self.ROOT / name).read_bytes())
                                for name in (self.HOST_SOURCE, module + 'runtime.py', module + 'contracts.py')}
        for name in (module.rstrip('/'), prefix.rstrip('/')):
            action_cache.relative(name)
        if not module.startswith('benchmarks/scripts/capabilities/') or not prefix.startswith('benchmarks/suites/capabilities-v1/sources/'):
            raise ValueError('Object probe sources escape their declared package')
        if capability not in ('T4', 'T6') or not callable(validation):
            raise ValueError('Unregistered object probe capability')

    def require_host(self):
        # The analysis/controller process can outlive a source edit. Restart
        # rather than certifying old loaded code with new on-disk digests.
        if any(sha((self.ROOT / name).read_bytes()) != expected
               for name, expected in self._loaded_sources.items()):
            raise RuntimeError('Loaded object runtime changed; restart before evaluation')

    def policy(self):
        value = json.loads(self.POLICY.read_bytes())
        if value.get('schema_version') != 1 or value.get('platform') != 'linux-x86_64' or value.get('compiler_image') != 'cpp' or any((type(n) is not int or n <= 0 for n in value['limits'].values())):
            raise ValueError('Unsupported object-probe policy')
        return value

    def sources(self, mode, variant=None):
        names = list(COMMON) + [self.MODULE + '__init__.py']
        if mode == 'original':
            if variant not in self.contracts.VARIANTS:
                raise ValueError('Unknown original object-record layout')
            names += [self.MODULE + 'build_worker.py', self.PREFIX + 'original.cpp', self.PREFIX + 'fixtures.hpp', self.PREFIX + variant + '.hpp', 'benchmarks/suites/capabilities-v1/sources/abi/helpers.cpp']
        elif mode == 'probe':
            names += [self.MODULE + name for name in ('probe_worker.py', 'artifacts.py', 'codegen.py')]
            names += ['benchmarks/scripts/grading/artifacts.py', self.PREFIX + 'fixtures.hpp']
        elif mode == 'native':
            names += [self.MODULE + 'native_worker.py']
        else:
            raise ValueError('Unknown object compiler stage')
        return {name: (self.ROOT / name).read_bytes() for name in names}

    def platform(self, mode):
        value = {'compiler_image': image_manifest.load('cpp')['id'], **host()}
        if mode == 'original':
            value['binutils_image'] = build.tooling()[0]
        return value

    def _run(self, request, frozen, environment, values, variant=None):
        mode = request['mode']
        if frozen != self.sources(mode, variant) or values != self.policy() or environment != self.platform(mode):
            raise RuntimeError('Object-probe inputs changed before worker launch')
        with images.image('cpp') as (base, pinned), build.tools_image() if mode == 'original' else nullcontext(None) as addon, tempfile.TemporaryDirectory(prefix='rea-object-worker-', dir='/tmp') as temporary:
            if pinned['id'] != environment['compiler_image']:
                raise RuntimeError('Object-probe compiler image changed after hashing')
            work, snapshot = (Path(temporary) / 'work', Path(temporary) / 'source')
            work.mkdir()
            snapshot.mkdir()
            for name, raw in frozen.items():
                path = snapshot / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            if mode == 'original':
                (snapshot / self.PREFIX / 'record.hpp').write_bytes(frozen[self.PREFIX + variant + '.hpp'])
            (snapshot / 'profile.json').write_bytes(action_cache.canonical(values))
            arguments = images.sandbox(base, pinned['manifest'], work, source=snapshot)
            env = {**pinned['manifest']['environment'], 'REA_ABI_PROBE_WORKER': '1'}
            if addon:
                tools, identity, manifest = addon
                if identity != environment['binutils_image']:
                    raise RuntimeError('Object-probe binutils image changed after hashing')
                arguments += ['--ro-bind', str(tools), '/tools']
                env['LD_LIBRARY_PATH'] = ':'.join(sorted({'/tools/' + str(Path(name).parent) for name, item in manifest['files'].items() if item['elf']}))
            worker = {'original': 'build_worker.py', 'probe': 'probe_worker.py', 'native': 'native_worker.py'}[mode]
            arguments += ['/usr/bin/python3', '-I', '-S', '-B', '/environment/' + self.MODULE + worker]
            token = images._authorized.set(tuple(arguments))
            try:
                reply = process.run(arguments, input=action_cache.canonical(request), timeout=150, output_limit=values['limits']['pipe_bytes'], env=env)
            finally:
                images._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Closed object-probe worker failed: ' + reply.stderr.decode(errors='replace')[-6000:])
            result = json.loads(reply.stdout)
            if result.get('host') != host() or frozen != self.sources(mode, variant) or values != self.policy() or (environment != self.platform(mode)):
                raise RuntimeError('Object-probe sources, policy or runtime changed during execution')
            result['runtime'] = self.proof(frozen, environment, values)
            return result

    def proof(self, frozen, environment, values):
        return {**environment, 'policy_sha256': sha(action_cache.canonical(values)), 'source_sha256': {name: sha(raw) for name, raw in frozen.items()}}

    def compile_action(self, mode, request, variant=None):
        self.require_host()
        frozen, environment, values = (self.sources(mode, variant), self.platform(mode), self.policy())
        inputs = {**frozen, 'profile.json': action_cache.canonical(values), 'request.json': action_cache.canonical(request)}
        if mode == 'original':
            inputs['source-alias.json'] = action_cache.canonical({self.PREFIX + 'record.hpp': self.PREFIX + variant + '.hpp'})
        action = action_cache.Action('capability_' + self.namespace + '_' + mode + '_v1', inputs, arguments=['closed-' + self.namespace + '-' + mode], platform=environment, timeout_seconds=150)
        return (action, frozen, environment, values)

    def _cached_compile(self, mode, request, variant=None, cache=None):
        action, frozen, environment, values = self.compile_action(mode, request, variant)
        local = cache or action_cache.Cache()
        hit = local.lookup(action)
        if hit:
            result = json.loads(local.get(hit['output_files']['result.json']['digest']))
        else:
            result = self._run(request, frozen, environment, values, variant)
            local.commit(action, {'result.json': action_cache.canonical(result)})
        if result.get('runtime') != self.proof(frozen, environment, values):
            raise ValueError('Object compiler cache has an incorrect runtime binding')
        return {**result, 'action_id': action.key, 'cache_hit': bool(hit)}

    def validate_target(self, target, *, cache=None):
        variant = target['variant']
        action = self.compile_action('original', {'mode': 'original'}, variant)[0]
        private, public = elf.stripped_matches(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']))
        names = ('original_callee', 'original_caller', 'original_reference', 'recovered_slot')
        symbols = private.symbols()
        if target['build_id'] != action.key or target.get('family') != self.family or target.get('convention') != 'sysv_amd64' or target['binary_sha256'] != sha(public.raw) or any((sha(bytes.fromhex(target[n + '_obj_hex'])) != target[n + '_obj_sha256'] for n in ('original', 'helpers'))) or any((n not in symbols for n in names)) or (target['entries'] != {n: symbols[n]['rva'] for n in names}) or (len(set(target['entries'].values())) != 4):
            raise ValueError('Original object-probe build, object or endpoint integrity failed')
        local = cache or action_cache.Cache()
        hit = local.lookup(action)
        if hit is None or set(hit['output_files']) != {'result.json'}:
            raise ValueError('Frozen original object-probe build receipt missing or invalid')
        saved = json.loads(local.get(hit['output_files']['result.json']['digest']))
        bound = ('private_hex', 'public_hex', 'original_obj_hex', 'original_obj_sha256',
                 'helpers_obj_hex', 'helpers_obj_sha256', 'runtime')
        if target.get('action_id') != action.key or any(target.get(n) != saved.get(n) for n in bound):
            raise ValueError('Object-probe endpoints differ from frozen original build outputs')

    def original(self, variant='baseline', *, cache=None):
        result = self._cached_compile('original', {'mode': 'original'}, variant, cache)
        private = elf.ELF(bytes.fromhex(result['private_hex']))
        target = {**result, 'variant': variant, 'family': self.family, 'convention': 'sysv_amd64', 'build_id': result['action_id'], 'binary_sha256': sha(bytes.fromhex(result['public_hex'])), 'entries': {n: private.symbols()[n]['rva'] for n in ('original_callee', 'original_caller', 'original_reference', 'recovered_slot')}}
        self.validate_target(target, cache=cache)
        return target

    def object_request(self, mode, target):
        return {'mode': mode, **{n + suffix: target[n + suffix] for n in ('original', 'helpers') for suffix in ('_obj_hex', '_obj_sha256')}}

    def probe(self, raw, target, *, cache=None):
        self.validate_target(target, cache=cache)
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 262144:
            raise ValueError('Object artifact must contain bounded exact bytes')
        request = {**self.object_request('probe', target), 'artifact_hex': raw.hex(), 'binary_sha256': target['binary_sha256']}
        result = self._cached_compile('probe', request, cache=cache)
        if result['status'] == 'compiled' and any((result[n + '_obj_sha256'] != target[n + '_obj_sha256'] for n in ('original', 'helpers'))):
            raise ValueError('Generated object probe lost its unchanged original-object binding')
        return result

    def vectors(self, nonces=None):
        value = list(self.contracts.NONCES if nonces is None else nonces)
        if not 1 <= len(value) <= 32 or any((type(n) is not int or not 0 <= n < 2 ** 64 for n in value)) or len(set(value)) != len(value):
            raise ValueError('Invalid object-probe vector set')
        return value

    def native(self, target, *, nonces=None, cache=None):
        self.validate_target(target, cache=cache)
        nonces = self.vectors(nonces)
        result = self._cached_compile('native', {**self.object_request('native', target), 'nonces': nonces}, cache=cache)
        if any((result[n + '_obj_sha256'] != target[n + '_obj_sha256'] for n in ('original', 'helpers'))) or [row['nonce'] for row in result['observations']] != nonces or any((len(bytes.fromhex(row['output_hex'])) != self.contracts.WIDTH for row in result['observations'])):
            raise ValueError('Native original object-reference calibration cache mismatch')
        return result

    def observe(self, image, entry, nonce, cache=None):
        invocation = {'id': nonce % 10 ** 12, 'return_type': 'i64', 'callbacks': [], 'arguments': [nonce, {'region': 'output', 'offset': 0}], 'regions': [{'id': 'output', 'data_hex': 'cd' * self.contracts.WIDTH, 'readable': [[0, self.contracts.WIDTH]], 'writable': [[0, self.contracts.WIDTH]]}]}
        result = executor.invoke(image, invocation, entry=entry, cache=cache)
        if result['status'] != 'returned':
            return {'returned': False, 'status': result['status'], 'output_hex': None, 'action_id': result['action_id']}
        return {'returned': True, 'status': int(bytes.fromhex(result['return_record']['value_hex']).decode('ascii')), 'output_hex': next((r['data_hex'] for r in result['regions'] if r['id'] == 'output')), 'action_id': result['action_id']}

    def grade_action(self, raw, target, nonces):
        self.require_host()
        frozen = self.sources('probe')
        frozen[self.HOST_SOURCE] = (self.ROOT / self.HOST_SOURCE).read_bytes()
        frozen.update({self.MODULE + name: (self.ROOT / (self.MODULE + name)).read_bytes() for name in ('runtime.py', 'contracts.py')})
        frozen.update(executor.sources('c_execute'))
        return action_cache.Action('capability_' + self.namespace + '_grade_v1', {**frozen, 'artifact.json': raw, 'profile.json': action_cache.canonical(self.policy()), 'original-reference.elf': bytes.fromhex(target['private_hex']), 'original.o': bytes.fromhex(target['original_obj_hex']), 'helpers.o': bytes.fromhex(target['helpers_obj_hex']), 'contract.json': action_cache.canonical({k: target[k] for k in ('variant', 'build_id', 'binary_sha256', 'entries')}), 'nonces.json': action_cache.canonical(nonces), 'execution.json': action_cache.canonical(executor.execute_profile())}, arguments=[self.capability, 'both-original-endpoint-directions'], platform={'compiler': self.platform('probe'), 'execution': executor.platform(True)})

    def grade(self, raw, target, *, nonces=None, cache=None):
        self.require_validation()
        executor.admit()
        self.validate_target(target, cache=cache)
        nonces = self.vectors(nonces)
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 262144:
            raise ValueError('Invalid object-description transport')
        action = self.grade_action(raw, target, nonces)
        local = cache or action_cache.Cache()
        hit = local.lookup(action)
        if hit:
            result = json.loads(local.get(hit['output_files']['grade.json']['digest']))
            if result.get('original_build_id') != target['build_id'] or result.get('capability_ids') != [self.capability] or result.get('original_obj_sha256') != target['original_obj_sha256']:
                raise ValueError('Object grade cache has a different original/score binding')
            if self.grade_action(raw, target, nonces).key != action.key:
                raise RuntimeError('Object-probe grading inputs changed during cache validation')
            return {**result, 'grading_id': action.key, 'cache_hit': True}
        compiled = self.probe(raw, target, cache=cache)
        result = {'status': compiled['status'], 'artifact_valid': compiled['artifact_valid'], 'capability_ids': [self.capability], 'original_build_id': target['build_id'], 'original_obj_sha256': target['original_obj_sha256'], 'compile_id': compiled['action_id'], 'return_provenance': 'not_executed', 'passed': 0, 'total': 2 * len(nonces), 'checks': []}
        if compiled['status'] == 'compiled':
            result['return_provenance'] = 'host_emulator_return_v1'
            for nonce in nonces:
                expected = self.observe(bytes.fromhex(target['private_hex']), 'original_reference', nonce, cache)
                if not expected['returned'] or expected['status'] != 0:
                    raise RuntimeError('Original object-reference violated its execution contract')
                for direction, entry in (('original_caller_to_replacement', 'original_caller'), ('bridge_to_original_callee', 'generated_bridge')):
                    actual = self.observe(bytes.fromhex(compiled['private_hex']), entry, nonce, cache)
                    result['checks'].append({'nonce': nonce, 'direction': direction, 'observation': actual, 'reference_action': expected['action_id'], 'passed': actual['returned'] and actual['status'] == 0 and (actual['output_hex'] == expected['output_hex'])})
            result['status'] = 'graded'
            result['passed'] = sum((row['passed'] for row in result['checks']))
        else:
            result['reason'] = compiled.get('reason')
        result['task_success'] = result['passed'] == result['total']
        result['capabilities'] = {self.capability: {'passed': result['passed'], 'total': result['total']}}
        if self.grade_action(raw, target, nonces).key != action.key:
            raise RuntimeError('Object-probe grading inputs changed during evaluation')
        local.commit(action, {'grade.json': action_cache.canonical(result)})
        return {**result, 'grading_id': action.key, 'cache_hit': False}
