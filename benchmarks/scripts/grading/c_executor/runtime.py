"""Separate compile/execute actions for the authenticated freestanding C profile."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import resource
import shutil
import tempfile
import threading

import action_cache
from grading import image_manifest, process
from grading import runtime as base_runtime
from .context import host
from . import image

ROOT = Path(__file__).resolve().parents[4]
CONFIG = ROOT / 'benchmarks/config/c-executor.json'
CACHE = ROOT / '.cache/benchmark-c-executor/v1'
_addons = {}
_lock = threading.Lock()
_controls = ContextVar('c_executor_validation_controls', default=False)


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


def admit():
    if not _controls.get():
        from .evidence import require_validation
        require_validation()


def policy():
    value = json.loads(CONFIG.read_bytes())
    if value.get('schema_version') != 1 or value.get('platform') != 'linux-x86_64':
        raise ValueError('Unsupported C executor policy')
    limits = value['limits']
    if any(type(item) is not int or item <= 0 for item in limits.values()):
        raise ValueError('Executor limits must be positive integers; zero never disables a bound')
    if (limits['stack_bytes'] % 4096 or not 4096 <= limits['stack_bytes'] <= 1048576
            or limits['max_arguments'] > 16 or limits['max_regions'] > 32 or limits['max_callbacks'] > 16
            or limits['max_callback_calls'] > 4096 or limits['instructions'] > 10000000
            or limits['worker_wall_seconds'] > 60 or limits['worker_cpu_seconds'] > 60
            or limits['emulator_timeout_us'] > limits['worker_wall_seconds'] * 1000000
            or limits['worker_output_bytes'] < 2 * (limits['max_region_bytes'] + limits['max_image_writable_bytes']) + 65536):
        raise ValueError('C executor limits are inconsistent with the bounded profile')
    return value


def engine_record(check_policy=True):
    if not (CACHE / 'active.json').is_file():
        raise RuntimeError('C executor is not prepared; run make setup-c-executor')
    active = json.loads((CACHE / 'active.json').read_bytes())
    if check_policy and active.get('policy_sha256') != image_manifest.file_sha(CONFIG):
        raise RuntimeError('C executor policy changed; run make setup-c-executor')
    identity = active.get('id')
    action_cache.valid_digest({'hash': identity, 'size_bytes': 0})
    record = json.loads((CACHE / (identity + '.json')).read_bytes())
    if (image_manifest.digest(record) != identity or record.get('schema_version') != 3
            or record.get('base_image') != image.record()['id']
            or record.get('engine') != {key: policy()['engine'][key] for key in ('name', 'version')}):
        raise ValueError('C executor module manifest differs from its identity/policy')
    wheel_sha = policy()['engine']['sha256']
    action_cache.valid_digest({'hash': wheel_sha, 'size_bytes': 0})
    provenance = CACHE / 'provenance' / identity / (wheel_sha + '.json')
    if not provenance.is_file() or json.loads(provenance.read_bytes()) != {'engine_id': identity, 'wheel_sha256': wheel_sha}:
        raise ValueError('Pinned executor module lacks wheel provenance')
    if not record.get('files'):
        raise ValueError('Empty C executor module inventory')
    for name, item in record['files'].items():
        image_manifest.relative(name)
        if not name.startswith(('unicorn/', 'dependencies/')):
            raise ValueError('Unreviewed module root in C executor')
        action_cache.valid_digest({'hash': item['sha256'], 'size_bytes': item['bytes']})
    return identity, record


@contextmanager
def addon():
    identity, record = engine_record()
    with _lock:
        cached = _addons.get(identity)
        if cached is None:
            temporary = tempfile.TemporaryDirectory(prefix='rea-c-engine-', dir='/tmp')
            root = Path(temporary.name) / 'modules'
            root.mkdir()
            try:
                original = CACHE / identity
                actual = {str(path.relative_to(original)) for path in original.rglob('*') if path.is_file() or path.is_symlink()}
                if actual != set(record['files']):
                    raise ValueError('C executor modules have extra/missing files')
                for name, item in record['files'].items():
                    source = original / name
                    if source.is_symlink() or source.stat().st_size != item['bytes']:
                        raise ValueError('Invalid C executor module binding')
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    if image_manifest.file_sha(target) != item['sha256']:
                        raise ValueError('C executor module bytes changed')
                    target.chmod(0o444)
                signature = base_runtime.image_signature(root)
                _addons[identity] = (temporary, root, signature)
            except BaseException:
                temporary.cleanup()
                raise
        else:
            temporary, root, signature = cached
        if base_runtime.image_signature(root) != signature:
            raise ValueError('C executor module image changed before execution')
    try:
        yield root, identity, record
    finally:
        if base_runtime.image_signature(root) != signature:
            raise ValueError('C executor module image changed during execution')


def compile_profile():
    value = policy()
    return {'compiler_flags': value['compiler_flags'], 'max_source_bytes': value['limits']['max_source_bytes']}


def execute_profile():
    value = policy()
    return {key: value[key] for key in ('id', 'limits', 'scope')} | {
        'engine': {key: value['engine'][key] for key in ('name', 'version')}}


def platform(execution=False):
    base = image.record()
    value = {'base_image': base['id'], **host()}
    if execution:
        value['engine'] = engine_record()[0]
    return value


def sources(stage):
    return {name: (ROOT / name).read_bytes() for name in action_cache.dependencies(stage)}


def _invoke(stage, request, expected_platform, profile, artifact, expected_sources):
    with image.materialize() as (root, base), tempfile.TemporaryDirectory(prefix='rea-c-worker-', dir='/tmp') as temporary:
        if base['id'] != expected_platform['base_image']:
            raise RuntimeError('C compiler/Python image changed after action preparation')
        temporary = Path(temporary)
        work, snapshot = temporary / 'work', temporary / 'source'
        work.mkdir()
        snapshot.mkdir()
        frozen = sources(stage)
        if frozen != expected_sources:
            raise RuntimeError('C executor source changed after action preparation')
        for name, raw in frozen.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (snapshot / 'profile.json').write_bytes(image_manifest.canonical(profile))
        worker = 'compile_worker.py' if stage == 'c_compile' else 'worker.py'
        args = base_runtime.sandbox(root, base['manifest'], work, source=snapshot)
        environment = {**base['manifest']['environment'], 'REA_C_EXECUTOR': '1'}

        def launch():
            command = args + ['/usr/bin/python3', '-I', '-S', '-B', '/environment/benchmarks/scripts/grading/c_executor/' + worker]
            token = base_runtime._authorized.set(tuple(command))
            try:
                return process.run(command, input=image_manifest.canonical(request), env=environment,
                                   timeout=150 if stage == 'c_compile' else profile['limits']['worker_wall_seconds'],
                                   output_limit=8388608 if stage == 'c_compile' else profile['limits']['worker_output_bytes'])
            finally:
                base_runtime._authorized.reset(token)

        if stage == 'c_execute':
            with addon() as (module_root, identity, record):
                if identity != expected_platform['engine']:
                    raise RuntimeError('C emulator image changed after action preparation')
                args += ['--ro-bind', str(module_root), '/addon']
                environment['LIBUNICORN_PATH'] = '/addon/unicorn/lib'
                environment['LD_LIBRARY_PATH'] = '/addon/dependencies'
                completed = launch()
        else:
            completed = launch()
        if completed.returncode:
            raise RuntimeError('C executor worker failed: ' + completed.stderr.decode(errors='replace')[-4000:])
        result = json.loads(completed.stdout)
        if result.get('host') != {key: expected_platform[key] for key in host()} or host() != result['host']:
            raise RuntimeError('C executor host changed after action preparation')
        if frozen != sources(stage):
            raise RuntimeError('C executor source changed during execution')
        result['runtime'] = {**expected_platform,
                             'profile_sha256': image_manifest.digest(profile),
                             'source_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in frozen.items()}}
        if artifact is not None:
            artifact = Path(artifact)
            artifact.mkdir(parents=True, exist_ok=True)
            for name in ('candidate.elf', 'compile.log', 'phase-times.json'):
                if (work / name).is_file():
                    shutil.copyfile(work / name, artifact / name)
        return result


def compile(source, exports=('reconstructed',), *, header='', cache=None):
    admit()
    values, environment = compile_profile(), platform()
    frozen = sources('c_compile')
    inputs = {**frozen, 'candidate/solution.c': source.encode('utf-8'), 'contract/header.h': header.encode('utf-8'),
              'configuration/profile.json': image_manifest.canonical(values),
              'configuration/exports.json': image_manifest.canonical(list(exports))}
    action = action_cache.Action('c_compile', inputs, arguments=['gcc-freestanding-c17'], platform=environment, timeout_seconds=120)
    cache = cache or action_cache.Cache()
    with tempfile.TemporaryDirectory(prefix='rea-c-compile-action-', dir='/tmp') as temporary:
        output = Path(temporary) / 'outputs'
        hit = cache.lookup(action)
        if hit:
            cache.restore(hit, output)
        else:
            output.mkdir()
            result = _invoke('c_compile', {'source': source, 'exports': list(exports), 'header': header}, environment, values, output, frozen)
            (output / 'result.json').write_bytes(image_manifest.canonical(result))
            cache.capture(action, output)
        result = json.loads((output / 'result.json').read_bytes())
        raw = (output / 'candidate.elf').read_bytes() if result['status'] == 'compiled' else None
        if raw is not None and hashlib.sha256(raw).hexdigest() != result['elf_sha256']:
            raise ValueError('Compiled C artifact digest mismatch')
        return {**result, 'elf': raw, 'action_id': action.key, 'cache_hit': bool(hit)}


def invoke(raw, invocation, *, entry='reconstructed', state=None, cache=None):
    admit()
    values, environment = execute_profile(), platform(True)
    frozen = sources('c_execute')
    request = {'elf_hex': raw.hex(), 'entry': entry, 'invocation': invocation, 'state': state}
    inputs = {**frozen, 'candidate/program.elf': raw,
              'invocation/request.json': image_manifest.canonical(request), 'configuration/profile.json': image_manifest.canonical(values)}
    action = action_cache.Action('c_execute', inputs, arguments=['unicorn-sysv-host-return'], platform=environment,
                                timeout_seconds=values['limits']['worker_wall_seconds'])
    cache = cache or action_cache.Cache()
    hit = cache.lookup(action)
    if hit:
        result = json.loads(cache.get(hit['output_files']['result.json']['digest']))
    else:
        result = _invoke('c_execute', request, environment, values, None, frozen)
        cache.commit(action, {'result.json': image_manifest.canonical(result)})
    return {**result, 'action_id': action.key, 'cache_hit': bool(hit)}
