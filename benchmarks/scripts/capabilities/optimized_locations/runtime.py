"""Sealed L6 grade actions; no inference or original-program execution."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import process, runtime as sandbox
from grading.c_executor import image
from grading.c_executor.context import host
from grading.structured import runtime as structured
from . import build, evidence, tasks

ROOT = action_cache.ROOT
HOST_SOURCE = build.MODULE + 'runtime.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()
SOURCES = (
    'benchmarks/scripts/action_cache.py', 'benchmarks/scripts/grading/__init__.py',
    'benchmarks/scripts/grading/artifacts.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py',
    'benchmarks/scripts/grading/toolchains.py', 'benchmarks/scripts/grading/c_executor/__init__.py',
    'benchmarks/scripts/grading/c_executor/context.py', 'benchmarks/scripts/grading/c_executor/image.py',
    'benchmarks/scripts/capabilities/__init__.py', 'benchmarks/scripts/capabilities/elf.py',
    'benchmarks/scripts/capabilities/build.py', 'benchmarks/scripts/capabilities/public.py',
    'benchmarks/scripts/capabilities/locations.py', build.MODULE + '__init__.py',
    build.MODULE + 'artifact.py', build.MODULE + 'reference.py', build.MODULE + 'dwarf.py',
    build.MODULE + 'review.py', build.MODULE + 'scoring.py', build.MODULE + 'grade_worker.py', HOST_SOURCE)


def _binding():
    selected = image.record()
    return {'runtime_id': selected['id'], 'python_version': selected['manifest']['python_version'], **host()}


def grade(raw, mode, *, target=None, cache=None):
    if (ROOT / HOST_SOURCE).read_bytes() != LOADED:
        raise RuntimeError('Loaded optimized grading host changed')
    evidence.require_validation()
    if not isinstance(raw, bytes) or len(raw) > 262144:
        raise ValueError('Invalid optimized artifact transport')
    target = ({k: v for k, v in build.build(mode).items() if k != 'cache_hit'} if target is None else target)
    tasks.validate_original(target, mode)
    profile, environment = structured.policy(), _binding()
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    request = {'artifact_hex': raw.hex(), 'target': target, 'mode': mode}
    payload = action_cache.canonical(request)
    if len(payload) > profile['limits']['input_bytes']:
        raise ValueError('Optimized grade exceeds its declared byte budget')
    action = action_cache.Action('capability_optimized_location_grade_v1',
        {**sources, 'request.json': payload, 'profile.json': action_cache.canonical(profile)},
        arguments=['closed-optimized-location-grade'], platform=environment,
        timeout_seconds=profile['limits']['wall_seconds'])
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['grade.json']['digest']))
    else:
        result = _invoke(request, profile, environment, sources)
        _guard(sources, profile, environment)
        local.commit(action, {'grade.json': action_cache.canonical(result)})
    _guard(sources, profile, environment)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    if (result.get('grading_runtime', {}).get('runtime_id') != environment['runtime_id']
            or result['grading_runtime'].get('source_inputs_sha256') != hashes):
        raise ValueError('Optimized cached grade has a different source/runtime binding')
    return {**result, 'grading_id': action.key, 'cache_hit': bool(hit)}


def _guard(sources, profile, environment):
    if (sources != {name: (ROOT / name).read_bytes() for name in SOURCES}
            or profile != structured.policy() or environment != _binding()
            or (ROOT / HOST_SOURCE).read_bytes() != LOADED):
        raise RuntimeError('Optimized grading source/runtime drift before publication')


def _invoke(request, profile, environment, sources):
    _guard(sources, profile, environment)
    with image.materialize() as (base, record), tempfile.TemporaryDirectory(prefix='rea-optimized-grade-', dir='/tmp') as temporary:
        if record['id'] != environment['runtime_id']:
            raise RuntimeError('Optimized Python image changed before launch')
        work, source = Path(temporary) / 'work', Path(temporary) / 'source'
        work.mkdir()
        source.mkdir()
        for name, data in {**sources, 'profile.json': action_cache.canonical(profile),
                           'runtime.json': action_cache.canonical({'id': record['id'], 'python_version': record['manifest']['python_version']}),
                           'sources.json': action_cache.canonical(sorted(sources))}.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        command = sandbox.sandbox(base, record['manifest'], work, source=source)
        command += ['/usr/bin/python3', '-I', '-S', '-B', '/environment/' + build.MODULE + 'grade_worker.py']
        token = sandbox._authorized.set(tuple(command))
        try:
            reply = process.run(command, input=action_cache.canonical(request),
                env={**record['manifest']['environment'], 'REA_OPTIMIZED_GRADE_WORKER': '1'},
                timeout=profile['limits']['wall_seconds'], output_limit=profile['limits']['output_bytes'])
        finally:
            sandbox._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed optimized grader failed; no model correctness grade: ' + reply.stderr.decode(errors='replace')[-3000:])
        response = json.loads(reply.stdout)
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
        if (response.get('runtime_id') != environment['runtime_id'] or response.get('host') != host()
                or response.get('source_inputs_sha256') != hashes):
            raise ValueError('Closed optimized grading attestation differs')
        return {**response['result'], 'grading_runtime': {**environment, 'source_inputs_sha256': hashes,
                'scope': 'closed_optimized_data_only_v1', 'integrity': 'full_content_then_pre_post_stat_checks'}}
