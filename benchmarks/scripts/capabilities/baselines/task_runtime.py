"""Production admission bridge for matched reconstruction and generation tasks."""
from pathlib import Path

import action_cache
from capabilities import buffers
from grading.c_executor import runtime as executor
from . import evidence, tasks

ROOT = action_cache.ROOT
HOST_SOURCE = tasks.build.MODULE + 'task_runtime.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()


from startup_policy import offline_gate

@offline_gate
def require_validation(language):
    executor.admit()
    if not evidence.current():
        raise RuntimeError('Matched original controls missing or stale; run make check-matched-baselines')
    if language in ('c', 'cpp'):
        from capabilities.buffer_evidence import require_validation as c_gate
        c_gate()
        if language == 'cpp':
            from capabilities.cpp_buffer.evidence import require_validation as cpp_gate
            cpp_gate()
    elif language == 'rust':
        from . import rust_evidence
        if not rust_evidence.current():
            raise RuntimeError('Rust candidate controls missing or stale; run make check-rust-buffer')
    elif language == 'go':
        from . import go_evidence
        if not go_evidence.current():
            raise RuntimeError('Go candidate controls missing or stale; run make check-go-buffer')
    else:
        raise ValueError('Unsupported matched candidate language')


def fixed_inputs(value):
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded matched task grader changed')
    original = tasks.validate_private(value)
    language = value['output_language']
    require_validation(language)
    names = {HOST_SOURCE, tasks.HOST_SOURCE, 'benchmarks/scripts/capabilities/buffers.py',
             tasks.build.MODULE + 'reference.py', tasks.build.MODULE + 'build.py'}
    names.update(tasks.reference.SOURCES)
    material = {name: (ROOT / name).read_bytes() for name in names}
    if language in ('c', 'cpp'):
        material.update(executor.sources('c_execute'))
        if language == 'cpp':
            from capabilities.cpp_buffer import runtime as compiler
            material.update(compiler.sources())
            profile, platform = compiler.policy(), compiler.platform()
        else:
            material.update(executor.sources('c_compile'))
            profile, platform = executor.compile_profile(), executor.platform()
        execution = executor.execute_profile()
    elif language == 'rust':
        from . import rust_runtime as compiler, rust_grade
        material.update({name: (ROOT / name).read_bytes() for name in compiler.SOURCES})
        material.update(rust_grade.selected_sources())
        profile, platform = compiler.policy(), compiler.platform()
        execution = executor.execute_profile()
    else:
        from . import go_runtime as compiler, go_grade, go_execute
        material.update({name: (ROOT / name).read_bytes() for name in (*compiler.SOURCES, *go_grade.SOURCES, *go_execute.SOURCES)})
        profile, platform = compiler.policy(), compiler.platform()
        execution = go_execute.profile()
    material.update({'compile-profile.json': action_cache.canonical(profile),
                     'execute-profile.json': action_cache.canonical(execution)})
    return material, {'compile': platform, 'execution': executor.platform(True), 'original': original['runtime']}


def grade(files, value):
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded matched task grader changed')
    tasks.validate_private(value)
    language = value['output_language']
    require_validation(language)
    filename = tasks.FILENAMES[language]
    source = None
    if isinstance(files, dict) and set(files) == {filename} and isinstance(files[filename], bytes):
        raw = files[filename]
        if 1 <= len(raw) <= 262144 and b'\0' not in raw:
            try:
                source = raw.decode('utf-8')
            except UnicodeError:
                pass
    if source is None:
        return {'status': 'invalid_artifact', 'artifact_valid': False, 'compiled': False,
                'passed': 0, 'total': len(value['cases']), 'capabilities': {}, 'task_success': False,
                'return_provenance': 'not_executed', 'release_accepted': False}
    if language in ('c', 'cpp'):
        result = buffers.grade(source, value['cases'], value['oracle'], language=language)
    elif language == 'rust':
        from . import rust_grade
        result = rust_grade.grade(source, value['family_id'], cases=value['cases'], oracle=value['oracle'])
    else:
        from . import go_grade
        result = go_grade.grade(source, value['family_id'], cases=value['cases'], oracle=value['oracle'])
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Matched task grader changed before returning observations')
    result.update(artifact_valid=True, release_accepted=False,
        task_success=bool(result['total'] and result['passed'] == result['total']),
        return_provenance='host_emulator_return_v1' if result.get('compiled') else 'not_executed')
    return result
