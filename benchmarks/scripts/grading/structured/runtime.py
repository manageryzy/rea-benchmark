"""One closed Python artifact action, independent of model and native-code grading."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process
from grading import runtime as sandbox
from grading.c_executor import image
from grading.c_executor.context import host

ROOT = action_cache.ROOT
CONFIG = ROOT / 'benchmarks/config/structured-grading.json'
SOURCES = (
    'benchmarks/scripts/startup_policy.py',
    'benchmarks/scripts/grading/__init__.py',
    'benchmarks/scripts/grading/artifacts.py',
    'benchmarks/scripts/grading/localization.py',
    'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/grading/runtime.py',
    'benchmarks/scripts/grading/image_manifest.py',
    'benchmarks/scripts/grading/c_executor/__init__.py',
    'benchmarks/scripts/grading/c_executor/image.py',
    'benchmarks/scripts/grading/c_executor/context.py',
    'benchmarks/scripts/grading/structured/__init__.py',
    'benchmarks/scripts/grading/structured/runtime.py',
    'benchmarks/scripts/grading/structured/worker.py')
_controls = ContextVar('structured_validation_controls', default=False)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def policy():
    value = json.loads(CONFIG.read_bytes())
    expected = {'wall_seconds', 'cpu_seconds', 'memory_bytes', 'input_bytes', 'output_bytes', 'open_files'}
    if (set(value) != {'schema_version', 'id', 'platform', 'limits'} or value['schema_version'] != 1
            or value['id'] != 'closed-structured-grading-v1' or value['platform'] != 'linux-x86_64'
            or set(value['limits']) != expected
            or any(type(number) is not int or number <= 0 for number in value['limits'].values())):
        raise ValueError('Invalid structured grading policy; zero never disables bounds')
    limits = value['limits']
    if (limits['wall_seconds'] > 60 or limits['cpu_seconds'] > limits['wall_seconds']
            or not 64 * 1024**2 <= limits['memory_bytes'] <= 2 * 1024**3
            or limits['input_bytes'] > 8 * 1024**2 or limits['output_bytes'] > 4 * 1024**2
            or not 16 <= limits['open_files'] <= 128):
        raise ValueError('Structured worker limits exceed the supported profile')
    return value


def sources():
    return {name: (ROOT / name).read_bytes() for name in SOURCES}


def prepare(files, truth):
    if not isinstance(files, dict) or len(files) > 4:
        raise ValueError('Invalid structured artifact set')
    for name, value in files.items():
        action_cache.relative(name)
        if not isinstance(value, bytes) or len(value) > 262144:
            raise ValueError('Invalid structured artifact byte limit')
    selected = image.record()
    environment = {'runtime_id': selected['id'], 'python_version': selected['manifest']['python_version'], **host()}
    profile, frozen = policy(), sources()
    request = {'kind': 'localization', 'files_hex': {name: value.hex() for name, value in sorted(files.items())}, 'truth': truth}
    raw = image_manifest.canonical(request)
    if len(raw) > profile['limits']['input_bytes']:
        raise ValueError('Structured request exceeds its byte limit')
    inputs = {**frozen, 'request.json': raw, 'policy.json': image_manifest.canonical(profile)}
    action = action_cache.Action('structured_localization_v1', inputs, arguments=['closed-python', 'locations-v1'],
                                platform=environment, timeout_seconds=profile['limits']['wall_seconds'])
    return action, request, profile, environment, frozen


def invoke(request, profile, environment, frozen):
    if frozen != sources() or profile != policy() or environment != {
            'runtime_id': image.record()['id'], 'python_version': image.record()['manifest']['python_version'], **host()}:
        raise RuntimeError('Structured grading inputs changed before worker launch')
    with image.materialize() as (base, record), tempfile.TemporaryDirectory(prefix='rea-structured-', dir='/tmp') as temporary:
        if record['id'] != environment['runtime_id']:
            raise RuntimeError('Structured Python image changed after action preparation')
        temporary = Path(temporary)
        work, source = temporary / 'work', temporary / 'source'
        work.mkdir()
        source.mkdir()
        for name, raw in frozen.items():
            destination = source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        (source / 'profile.json').write_bytes(image_manifest.canonical(profile))
        (source / 'runtime.json').write_bytes(image_manifest.canonical({
            'id': record['id'], 'python_version': record['manifest']['python_version']}))
        (source / 'sources.json').write_bytes(image_manifest.canonical(sorted(frozen)))
        command = sandbox.sandbox(base, record['manifest'], work, source=source) + [
            '/usr/bin/python3', '-I', '-S', '-B', '/environment/benchmarks/scripts/grading/structured/worker.py']
        token = sandbox._authorized.set(tuple(command))
        try:
            reply = process.run(command, input=image_manifest.canonical(request),
                                env={**record['manifest']['environment'], 'REA_STRUCTURED_WORKER': '1'},
                                timeout=profile['limits']['wall_seconds'], output_limit=profile['limits']['output_bytes'])
        finally:
            sandbox._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed structured worker failed; no model correctness grade: ' + reply.stderr.decode(errors='replace')[-2000:])
        value = json.loads(reply.stdout)
        expected_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in frozen.items()}
        if value.get('runtime_id') != record['id'] or value.get('host') != host() or value.get('source_inputs_sha256') != expected_hashes:
            raise RuntimeError('Structured worker runtime/source attestation mismatch')
    if sources() != frozen or policy() != profile:
        raise RuntimeError('Structured grading inputs changed during execution')
    return {**value['result'], 'grading_runtime': {**environment, 'source_inputs_sha256': expected_hashes,
            'scope': 'closed_data_only_artifact_worker_v1', 'integrity': 'full_content_then_pre_post_stat_checks'}}


def grade(files, truth, *, cache=None):
    if not _controls.get():
        from .evidence import require_validation
        require_validation()
    action, request, profile, environment, frozen = prepare(files, truth)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        result = json.loads(local.get(hit['output_files']['grade.json']['digest']))
    else:
        result = invoke(request, profile, environment, frozen)
        local.commit(action, {'grade.json': image_manifest.canonical(result)})
    if (result.get('grading_runtime', {}).get('runtime_id') != environment['runtime_id']
            or result['grading_runtime'].get('source_inputs_sha256') != {name: hashlib.sha256(raw).hexdigest() for name, raw in frozen.items()}):
        raise ValueError('Structured cached grade differs from its declared action')
    return {**result, 'grading_id': action.key, 'cache_hit': bool(hit)}
