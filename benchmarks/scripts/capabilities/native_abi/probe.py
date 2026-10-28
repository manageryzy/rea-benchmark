"""Description compilation excludes private gold bindings and hidden vectors."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import process, runtime
from grading.c_executor import runtime as executor
from grading.c_executor import elf as candidate_elf
from grading.c_executor.context import host
from . import artifacts, build, images

ROOT = action_cache.ROOT
HOST_SOURCE = build.MODULE + 'probe.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()
SOURCES = (
    'benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/grading/artifacts.py', 'benchmarks/scripts/grading/c_executor/__init__.py',
    'benchmarks/scripts/grading/c_executor/context.py', 'benchmarks/scripts/grading/c_executor/helpers.c',
    'benchmarks/scripts/grading/c_executor/link.ld', 'benchmarks/scripts/capabilities/__init__.py',
    build.MODULE + '__init__.py', build.MODULE + 'artifacts.py', build.MODULE + 'codegen.py',
    build.MODULE + 'probe_worker.py', HOST_SOURCE)


def compile(language, raw, *, cache=None):
    if ((ROOT / HOST_SOURCE).read_bytes() != LOADED
            or (ROOT / build.MODULE / 'artifacts.py').read_bytes() != artifacts.LOADED_SOURCE):
        raise RuntimeError('Loaded native ABI probe sources changed')
    from .evidence import require_validation
    require_validation()
    executor.admit()
    value = artifacts.admit(raw)
    target = build.build(language)
    public_raw = bytes.fromhex(target['public_hex'])
    binary_sha = hashlib.sha256(public_raw).hexdigest()
    if value['binary_sha256'] != binary_sha:
        return {'status': 'wrong_binary', 'cache_hit': False, 'elf': None}
    original, _ = images.validate(bytes.fromhex(target['private_hex']), public_raw, language)
    name = 'native_transform' if language == 'rust' else 'main.nativeTransform'
    callee = images.selected_symbols(original, (name,))[name]
    if callee['type'] != 2:
        raise ValueError('Native ABI original callee is not executable code')
    request = {'artifact_hex': raw.hex(), 'binary_sha256': binary_sha, 'callee_address': callee['address']}
    profile, platform = executor.compile_profile(), executor.platform()
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    selected = action_cache.Action('capability_native_language_abi_probe_v1',
        {**sources, 'request.json': action_cache.canonical(request), 'profile.json': action_cache.canonical(profile)},
        arguments=['closed-native-abi-description-compiler'], platform=platform, timeout_seconds=150)
    local = cache or action_cache.Cache()
    hit = local.lookup(selected)
    if hit:
        result = json.loads(local.get(hit['output_files']['result.json']['digest']))
    else:
        with executor.image.materialize() as (base, pinned), tempfile.TemporaryDirectory(prefix='rea-native-abi-probe-', dir='/tmp') as temporary:
            if pinned['id'] != platform['base_image']:
                raise RuntimeError('Native ABI probe compiler changed after action construction')
            work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
            work.mkdir()
            snapshot.mkdir()
            for name, data in {**sources, 'profile.json': action_cache.canonical(profile)}.items():
                path = snapshot / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            command = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
            command += ['/usr/bin/python3', '-I', '-S', '-B', '/environment/' + build.MODULE + 'probe_worker.py']
            token = runtime._authorized.set(tuple(command))
            try:
                reply = process.run(command, input=action_cache.canonical(request), timeout=150,
                    output_limit=12 * 1024**2, env={**pinned['manifest']['environment'], 'REA_NATIVE_ABI_WORKER': '1'})
            finally:
                runtime._authorized.reset(token)
            if reply.returncode:
                raise RuntimeError('Closed native ABI compiler failed: ' + reply.stderr.decode(errors='replace')[-4000:])
            result = {**json.loads(reply.stdout), 'runtime': platform}
        _guard(sources, platform)
        _validate(result, platform)
        local.commit(selected, {'result.json': action_cache.canonical(result)})
    _guard(sources, platform)
    program = _validate(result, platform)
    return {**result, 'elf': program, 'action_id': selected.key, 'cache_hit': bool(hit)}


def _guard(sources, platform):
    if (sources != {name: (ROOT / name).read_bytes() for name in SOURCES} or executor.platform() != platform
            or (ROOT / HOST_SOURCE).read_bytes() != LOADED):
        raise RuntimeError('Native ABI probe sources/runtime changed before result return')


def _validate(result, platform):
    if (result.get('runtime') != platform or result.get('host') != host()
            or result.get('status') not in ('compiled', 'compile_error', 'invalid_artifact', 'wrong_binary')):
        raise ValueError('Native ABI probe cache binding differs')
    program = bytes.fromhex(result['elf_hex']) if result['status'] == 'compiled' else None
    if program is not None:
        if hashlib.sha256(program).hexdigest() != result['elf_sha256']:
            raise ValueError('Native ABI probe ELF digest mismatch')
        for entry in ('__rea_native_replacement', '__rea_native_bridge'):
            candidate_elf.load(program, entry, executor.policy()['limits'])
    return program
