"""Append-only aggregate native ABI grades over cached one-call observations."""
import hashlib
import json
from pathlib import Path

import action_cache
from grading.c_executor import runtime as executor
from . import artifacts, build, evidence, execute, probe, reference, tasks

ROOT = action_cache.ROOT
HOST_SOURCE = build.MODULE + 'runtime.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()
SOURCES = tuple(sorted(set(probe.SOURCES) | set(execute.SOURCES) | {
    HOST_SOURCE, build.MODULE + 'reference.py', build.MODULE + 'build.py', build.MODULE + 'tasks.py'}))


def _loaded():
    if ((ROOT / HOST_SOURCE).read_bytes() != LOADED
            or Path(reference.__file__).read_bytes() != reference.LOADED_SOURCE):
        raise RuntimeError('Loaded native ABI grade/oracle source changed')


def grade(language, raw, *, nonces=None, target=None, cache=None):
    _loaded()
    evidence.require_validation()
    if language not in ('rust', 'go') or not isinstance(raw, bytes) or len(raw) > 262144:
        raise ValueError('Invalid native ABI grade transport')
    selected_nonces = list(reference.NONCES if nonces is None else nonces)
    if (not 1 <= len(selected_nonces) <= 64 or any(type(n) is not int or not 0 <= n < 2**64 for n in selected_nonces)
            or len(set(selected_nonces)) != len(selected_nonces)):
        raise ValueError('Invalid native ABI grade vectors')
    target = ({k: v for k, v in build.build(language).items() if k != 'cache_hit'} if target is None else target)
    tasks.validate_original(target, language)
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    profiles = {'compile': executor.compile_profile(), 'execution': execute.profile()}
    platform = {'compile': executor.platform(), 'execution': executor.platform(True),
                'original': target['runtime']}
    contract = {'language': language, 'nonces': selected_nonces, 'build_id': target['build_id'],
                'binary_sha256': hashlib.sha256(bytes.fromhex(target['public_hex'])).hexdigest()}
    action = action_cache.Action('capability_native_language_abi_grade_v1',
        {**sources, 'artifact.json': raw, 'contract.json': action_cache.canonical(contract),
         'profiles.json': action_cache.canonical(profiles), 'target/original.elf': bytes.fromhex(target['private_hex']),
         'target/public.elf': bytes.fromhex(target['public_hex'])},
        arguments=['aggregate-native-language-abi-observations'], platform=platform)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['grade.json']['digest']))
    else:
        total = 2 * len(selected_nonces)
        result = {'schema_version': 1, 'protocol': 'native-language-abi-v1',
                  'capability_ids': ['T7'], 'artifact_valid': False, 'compiled': False,
                  'status': 'invalid_artifact', 'passed': 0, 'total': total, 'checks': [],
                  'return_provenance': 'not_executed', 'release_accepted': False}
        try:
            value = artifacts.admit(raw)
        except ValueError:
            value = None
        if value is not None:
            result['artifact_valid'] = True
            if value['binary_sha256'] != contract['binary_sha256']:
                result['status'] = 'wrong_binary'
            else:
                compiled = probe.compile(language, raw)
                if compiled['status'] != 'compiled':
                    # A bounded description is turned into source by our own
                    # generator; compilation failure is an environment defect.
                    raise RuntimeError('Native ABI generated probe failed; no model correctness grade: ' + compiled.get('log', compiled['status']))
                result.update(compiled=True, status='graded', compile_action_id=compiled['action_id'],
                              return_provenance='host_native_abi_return_v1')
                for nonce in selected_nonces:
                    values = reference.arguments(nonce)
                    for direction in ('replacement', 'bridge'):
                        observed = execute.observe(language, values, direction=direction, probe=compiled['elf'])
                        expected = reference.expected(1 if direction == 'replacement' else 0, values)
                        passed = observed['status'] == 'returned' and observed['values'] == expected
                        result['passed'] += passed
                        result['checks'].append({'nonce': nonce, 'direction': direction, 'passed': passed,
                            'status': observed['status'], 'execution_action_id': observed['action_id']})
        success = result['passed'] == result['total']
        result.update(task_success=success, program_success=success,
                      grading_runtime=platform, original_build_id=target['build_id'])
        result['capabilities'] = {'T7': {'passed': result['passed'], 'total': result['total'],
                                       'accuracy': result['passed'] / result['total'], 'task_success': success}}
        _guard(sources, profiles, platform, language, target['build_id'])
        local.commit(action, {'grade.json': action_cache.canonical(result)})
    _guard(sources, profiles, platform, language, target['build_id'])
    if (result.get('grading_runtime') != platform or result.get('original_build_id') != target['build_id']
            or result.get('total') != 2 * len(selected_nonces)
            or result.get('task_success') != (result.get('passed') == result.get('total'))):
        raise ValueError('Native ABI cached aggregate binding differs')
    return {**result, 'grading_id': action.key, 'cache_hit': bool(hit)}


def _guard(sources, profiles, platform, language, build_id):
    _loaded()
    if (sources != {name: (ROOT / name).read_bytes() for name in SOURCES}
            or profiles != {'compile': executor.compile_profile(), 'execution': execute.profile()}
            or platform['compile'] != executor.platform() or platform['execution'] != executor.platform(True)
            or build.action(language)[0].key != build_id):
        raise RuntimeError('Native ABI aggregate source/runtime changed before publication')
