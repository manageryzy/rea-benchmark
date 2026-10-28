"""Development Rust buffer grades over the same one-call host-owned executor."""
import json
from pathlib import Path

import action_cache
from capabilities import buffers
from grading.c_executor import runtime as executor
from . import build, reference, rust_runtime

ROOT = action_cache.ROOT
HOST_SOURCE = build.MODULE + 'rust_grade.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()
SOURCES = (HOST_SOURCE, build.MODULE + 'reference.py', 'benchmarks/scripts/capabilities/buffers.py')


def stable(value):
    return {key: item for key, item in value.items() if key != 'cache_hit'}


def selected_sources():
    return {**{name: (ROOT / name).read_bytes() for name in SOURCES}, **executor.sources('c_execute')}


def grading_action(source, family, cases, oracle, compiled):
    return action_cache.Action('capability_rust_buffer_grade_v1',
        {**selected_sources(), 'solution.rs': source.encode(), 'vectors.json': action_cache.canonical(cases),
         'oracle.json': action_cache.canonical(stable(oracle)),
         'compile.json': action_cache.canonical({'id': compiled['action_id'], 'status': compiled['status'],
                                                'elf_sha256': compiled.get('elf_sha256')}),
         'execute-profile.json': action_cache.canonical(executor.execute_profile())},
        arguments=['host-owned-rust2021-buffer-v1', family], platform=executor.platform(True))


def _guard(sources, environment):
    if (Path(__file__).read_bytes() != LOADED
            or sources != selected_sources()
            or environment != executor.platform(True)):
        raise RuntimeError('Rust buffer grading sources/runtime changed')


def grade(source, family, *, cases=None, oracle=None, cache=None):
    if Path(__file__).read_bytes() != LOADED or family not in build.FAMILIES:
        raise RuntimeError('Loaded Rust buffer grader or family differs')
    cases = reference.vectors(family) if cases is None else cases
    if not isinstance(cases, list) or not 1 <= len(cases) <= 128:
        raise ValueError('Invalid Rust buffer invocation list')
    for case in cases: buffers.initial(case)
    actual = reference.observe('rust', family, cases)
    if oracle is not None and (not isinstance(oracle, dict) or stable(oracle) != stable(actual)):
        raise ValueError('Rust candidate invocation/oracle binding differs')
    oracle = actual
    sources = selected_sources()
    environment = executor.platform(True)
    # Exact same kernel-memory/status oracle as C17. Original-language source
    # and its compiler have separate actions; vectors never reach the compiler.
    compiled = rust_runtime.compile(source, cache=cache)
    action = grading_action(source, family, cases, oracle, compiled)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['grade.json']['digest']))
        _guard(sources, environment)
        return {**result, 'grading_id': action.key, 'cache_hit': True}
    if compiled['status'] != 'compiled':
        result = {'status': compiled['status'], 'artifact_valid': True, 'compiled': False,
                'passed': 0, 'total': len(cases), 'task_success': False, 'capabilities': {},
                'compile_action_id': compiled['action_id'], 'return_provenance': 'not_executed',
                'development_only': True, 'release_accepted': False}
    else:
        checks = []
        for case, expected in zip(cases, oracle['observations']):
            observed = executor.invoke(compiled['elf'], buffers.invocation(case), cache=cache)
            checks.append(buffers.compare(observed, expected, case))
        scores = {}
        for capability in ('C1', 'C2', 'C3', 'C4'):
            relevant = [row for case, row in zip(cases, checks) if capability in case['capabilities']]
            scores[capability] = {'passed': sum(row['passed'] for row in relevant), 'total': len(relevant)}
        passed = sum(row['passed'] for row in checks)
        result = {'status': 'graded', 'artifact_valid': True, 'compiled': True,
                'passed': passed, 'total': len(checks), 'task_success': passed == len(checks),
                'capabilities': scores, 'checks': checks, 'compile_action_id': compiled['action_id'],
                'original_reference_id': oracle['reference_id'], 'return_provenance': 'host_emulator_return_v1',
                'development_only': True, 'release_accepted': False}
    _guard(sources, environment)
    local.commit(action, {'grade.json': action_cache.canonical(result)})
    return {**result, 'grading_id': action.key, 'cache_hit': False}
