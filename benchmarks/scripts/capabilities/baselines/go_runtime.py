"""Restricted Go source compiler; candidate code is never run by the OS."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest, process, runtime
from grading.c_executor import runtime as executor
from grading.c_executor.context import host
from capabilities import build as family_build

ROOT = action_cache.ROOT
MODULE = 'benchmarks/scripts/capabilities/baselines/'
POLICY = ROOT / 'benchmarks/config/go-buffer.json'
HOST_SOURCE = MODULE + 'go_runtime.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()
FLAGS = ['-trimpath', '-buildvcs=false', '-gcflags=-l', '-ldflags=-buildid=']
IMPORTS = ['unsafe', 'encoding/binary', 'math', 'math/bits', 'sort', 'slices', 'cmp']
SOURCES = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/process.py',
    'benchmarks/scripts/grading/c_executor/__init__.py', 'benchmarks/scripts/grading/c_executor/context.py',
    MODULE + 'go_policy.go', MODULE + 'go_compile_worker.py', HOST_SOURCE)


def policy():
    value = json.loads(POLICY.read_bytes())
    if (set(value) != {'schema_version', 'id', 'source_language', 'output_abi', 'max_source_bytes',
                       'imports', 'compiler_flags', 'runtime_startup', 'image_writes'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['id'] != 'bounded-go-buffer-abiinternal-v1' or value['source_language'] != 'go'
            or value['output_abi'] != 'go_abi_internal_amd64' or value['runtime_startup'] != 'not_executed'
            or value['image_writes'] != 'forbidden' or value['compiler_flags'] != FLAGS
            or value['imports'] != IMPORTS or type(value['max_source_bytes']) is not int
            or value['max_source_bytes'] != 262144):
        raise ValueError('Unsupported restricted Go candidate profile')
    return value


def platform():
    return {'compiler_image': image_manifest.load('go')['id'], 'binutils_image': family_build.tooling()[0], **host()}


def compile(source, *, cache=None):
    executor.admit()
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded Go candidate compiler changed')
    if not isinstance(source, str) or not 1 <= len(source.encode()) <= 262144 or '\0' in source:
        raise ValueError('Go candidate source exceeds its byte/encoding contract')
    profile, environment = policy(), platform()
    sources = {name: (ROOT / name).read_bytes() for name in SOURCES}
    action = action_cache.Action('capability_go_buffer_compile_v1',
        {**sources, 'solution.go': source.encode(), 'profile.json': action_cache.canonical(profile)},
        arguments=['closed-go-buffer-compiler'], platform=environment, timeout_seconds=210)
    local = cache or action_cache.Cache()
    hit = local.lookup(action)
    if hit:
        files = {name: local.get(item['digest']) for name, item in hit['output_files'].items()}
    else:
        files = _invoke(source, profile, environment, sources)
        _guard(profile, environment, sources)
        local.commit(action, files)
    _guard(profile, environment, sources)
    result = json.loads(files['result.json'])
    binary = files.get('candidate.elf') if result['status'] == 'compiled' else None
    if (result.get('source_sha256') != hashlib.sha256(source.encode()).hexdigest()
            or result.get('runtime') != environment
            or binary is not None and hashlib.sha256(binary).hexdigest() != result.get('elf_sha256')):
        raise ValueError('Restricted Go cached compiler binding differs')
    if binary is not None:
        from .go_image import load
        try:
            load(binary, executor.execute_profile()['limits'])
        except ValueError:
            return {**result, 'status': 'unsupported_elf', 'elf': None, 'action_id': action.key, 'cache_hit': bool(hit)}
    return {**result, 'elf': binary, 'log': files.get('compile.log', b'').decode(errors='replace'),
            'action_id': action.key, 'cache_hit': bool(hit)}


def _guard(profile, environment, sources):
    if (Path(__file__).read_bytes() != LOADED or sources != {name: (ROOT / name).read_bytes() for name in SOURCES}
            or profile != policy() or environment != platform()):
        raise RuntimeError('Go candidate compiler sources/runtime changed')


def _invoke(source, profile, environment, sources):
    _guard(profile, environment, sources)
    with runtime.image('go') as (base, pinned), family_build.tools_image() as (tools, identity, manifest), \
         tempfile.TemporaryDirectory(prefix='rea-go-buffer-', dir='/tmp') as temporary:
        if pinned['id'] != environment['compiler_image'] or identity != environment['binutils_image']:
            raise RuntimeError('Go candidate toolchain changed after resolution')
        work, snapshot = Path(temporary) / 'work', Path(temporary) / 'source'
        work.mkdir()
        snapshot.mkdir()
        for name, raw in {**sources, 'profile.json': action_cache.canonical(profile)}.items():
            path = snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        command = runtime.sandbox(base, pinned['manifest'], work, source=snapshot)
        command += ['--ro-bind', str(tools), '/tools', '/usr/bin/python3', '-I', '-S', '-B',
                    '/environment/' + MODULE + 'go_compile_worker.py']
        token = runtime._authorized.set(tuple(command))
        try:
            reply = process.run(command, input=action_cache.canonical({'source': source}), timeout=210,
                output_limit=262144, env={**pinned['manifest']['environment'], 'REA_GO_BUFFER_COMPILER': '1',
                    'LD_LIBRARY_PATH': ':'.join(sorted({'/tools/' + str(Path(name).parent)
                        for name, item in manifest['files'].items() if item['elf']}))})
        finally:
            runtime._authorized.reset(token)
        if reply.returncode:
            raise RuntimeError('Closed Go candidate worker failed: ' + reply.stderr.decode(errors='replace')[-4000:])
        result = json.loads(reply.stdout)
        if result.get('host') != host():
            raise ValueError('Go compiler worker host differs')
        result['runtime'] = environment
        files = {'result.json': action_cache.canonical(result), 'compile.log': (work / 'compile.log').read_bytes()}
        if result['status'] == 'compiled': files['candidate.elf'] = (work / 'candidate.elf').read_bytes()
        return files
