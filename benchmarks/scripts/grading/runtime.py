"""Run trusted grading in a verified image, isolated from host toolchains."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import fcntl

from . import image_manifest as toolchains, process

ROOT = Path(__file__).resolve().parents[3]
_images = {}
_lock = threading.Lock()
_authorized = ContextVar('closed_grading_command', default=None)


def permitted_command(command):
    return not isinstance(command, (str, bytes)) and _authorized.get() == tuple(str(value) for value in command)


def identity(language):
    from .native import host_identity
    try:
        record = toolchains.load(language)
    except RuntimeError:
        # Planning/cache inspection must not capture a host toolchain or run a
        # compiler. Actual evaluation refuses an unprepared image below.
        return {'scope': 'grading_runtime_not_prepared', 'language': language, **host_identity()}
    return {**record['manifest']['compiler'], 'image_sha256': record['id'],
            'python_version': record['manifest']['python_version'], 'scope': 'closed_toolchain_python_and_executor_v1',
            **host_identity()}


def verify_image(root, manifest):
    root = Path(root)
    expected = set(manifest['files'])
    actual = {str(path.relative_to(root)) for path in root.rglob('*') if path.is_file() or path.is_symlink()}
    if actual != expected:
        raise ValueError('Grading image has extra or missing files')
    directories = set(manifest['directories'])
    for name in expected | set(directories):
        directories.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != '.')
    actual_directories = {str(path.relative_to(root)) for path in root.rglob('*') if path.is_dir() and not path.is_symlink()}
    if actual_directories != directories:
        raise ValueError('Grading image directory entries differ')
    for name, item in manifest['files'].items():
        path = root.joinpath(*toolchains.relative(name).parts)
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item['bytes'] or toolchains.file_sha(path) != item['sha256']:
            raise ValueError('Grading image bytes differ: ' + name)
        if path.stat().st_mode & 0o777 != item['mode']:
            raise ValueError('Grading image mode differs: ' + name)
        if path.stat().st_mtime_ns != 0:
            raise ValueError('Grading image timestamp is not normalized: ' + name)


def extract(record, destination, *, cache=toolchains.CACHE):
    if record['id'] != toolchains.digest(record['manifest']):
        raise ValueError('Grading manifest identity mismatch')
    toolchains.validate_record(record, record['manifest']['language'])
    archive = Path(cache) / 'archives' / (record['archive_sha256'] + '.tar.gz')
    if toolchains.file_sha(archive) != record['archive_sha256']:
        raise ValueError('Corrupt grading toolchain archive')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = record['manifest']
    blobs = {}
    for name, item in manifest['files'].items():
        toolchains.relative(name)
        if not re.fullmatch('[0-9a-f]{64}', item['sha256']) or type(item['bytes']) is not int or item['bytes'] < 0 or item['mode'] not in (0o444, 0o555):
            raise ValueError('Invalid grading image file descriptor')
        blobs.setdefault(item['sha256'], []).append(name)
    seen = set()
    with tarfile.open(archive, 'r|gz') as stream:
        for member in stream:
            path = toolchains.relative(member.name)
            if len(path.parts) != 2 or path.parts[0] != 'blobs' or not member.isfile() or path.parts[1] not in blobs or path.parts[1] in seen:
                raise ValueError('Unexpected grading image archive member')
            sha = path.parts[1]
            seen.add(sha)
            raw = stream.extractfile(member)
            if member.size != manifest['files'][blobs[sha][0]]['bytes']:
                raise ValueError('Grading image archive size mismatch')
            # Read each blob once and copy aliases; never trust archive paths or
            # preserve an archive symlink/hardlink into a mutable input.
            first = destination / blobs[sha][0]
            first.parent.mkdir(parents=True, exist_ok=True)
            with first.open('xb') as output:
                shutil.copyfileobj(raw, output, length=1024 * 1024)
            if toolchains.file_sha(first) != sha:
                raise ValueError('Grading image archive payload mismatch')
            for name in blobs[sha][1:]:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(first, target)
            for name in blobs[sha]:
                target = destination / name
                target.chmod(manifest['files'][name]['mode'])
                os.utime(target, (0, 0))
    if seen != set(blobs):
        raise ValueError('Missing grading image archive payload')
    for name in manifest['directories']:
        destination.joinpath(*toolchains.relative(name).parts).mkdir(parents=True, exist_ok=True)
    verify_image(destination, manifest)


def image_signature(root):
    """Detect drift after initial full hashing, including same-size/timestamp edits."""
    result = {}
    for path in Path(root).rglob('*'):
        item = path.lstat()
        result[str(path.relative_to(root))] = (item.st_dev, item.st_ino, item.st_mode,
                                             item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    return result


def prepared_image(record, *, cache=None):
    """One verified extraction per image, retained in Linux storage.

Only trusted_local uses this fast path. Candidate workers receive read-only
mounts; subsequent host mutation is outside the explicit startup trust policy.
This is environment preparation, not a validation-test receipt.
"""
    cache = Path(cache) if cache is not None else Path.home() / '.cache/rea/grading-images'
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / record['id']
    with (cache / (record['id'] + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        expected = {'image_id': record['id'], 'archive_sha256': record['archive_sha256']}
        if target.exists():
            root = target / 'root'
            saved = json.loads((target / 'prepared.json').read_bytes())
            facts = root.stat()
            if target.is_symlink() or root.is_symlink() or saved != {
                    **expected, 'device': facts.st_dev, 'inode': facts.st_ino}:
                raise ValueError('Prepared grading image identity changed')
            return root
        with tempfile.TemporaryDirectory(prefix='prepare-', dir=cache) as temporary:
            stage = Path(temporary) / 'image'
            root = stage / 'root'
            extract(record, root)
            facts = root.stat()
            (stage / 'prepared.json').write_bytes(toolchains.canonical({
                **expected, 'device': facts.st_dev, 'inode': facts.st_ino}))
            stage.rename(target)
        return target / 'root'


@contextmanager
def image(language):
    record = toolchains.load(language)
    import startup_policy
    if startup_policy.trusted():
        yield prepared_image(record), record
        return
    with _lock:
        cached = _images.get(record['id'])
        if cached is None:
            temporary = tempfile.TemporaryDirectory(prefix='rea-grading-image-', dir='/tmp')
            root = Path(temporary.name) / 'root'
            try:
                extract(record, root)
            except BaseException:
                temporary.cleanup()
                raise
            signature = image_signature(root)
            _images[record['id']] = (temporary, root, signature)
        else:
            temporary, root, signature = cached
        if image_signature(root) != signature:
            raise ValueError('Pinned grading image changed before execution')
    try:
        yield root, record
    finally:
        if image_signature(root) != signature:
            raise ValueError('Pinned grading image changed during execution')


def sandbox(root, manifest, work, *, source=None, state=None):
    root = Path(root)
    library_dirs = sorted({str((root / name).parent) for name, item in manifest['files'].items() if item['elf']})
    args = [str(root / manifest['loader']), '--library-path', ':'.join(library_dirs), str(root / 'usr/bin/bwrap'),
        '--unshare-all', '--die-with-parent', '--new-session']
    for name in ('usr', 'lib', 'lib64', 'opt'):
        if name in manifest.get('root_aliases', {}):
            args += ['--symlink', manifest['root_aliases'][name], '/' + name]
        else:
            args += ['--ro-bind', str(root / name), '/' + name]
    args += ['--symlink', 'usr/bin', '/bin', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
             '--dir', '/etc', '--ro-bind', str(root / 'etc/ld.so.cache'), '/etc/ld.so.cache',
             '--bind', str(work), '/work', '--chdir', '/work']
    if source is not None:
        args += ['--ro-bind', str(source), '/environment']
    if state is not None:
        args += ['--bind', str(state), '/environment/.cache']
    return args


def invoke(operation, language, request, artifact=None, *, trusted_control=False):
    if not trusted_control:
        require_validation(language)
    with image(language) as (root, record), tempfile.TemporaryDirectory(prefix='rea-grading-worker-', dir='/tmp') as temporary:
        temporary = Path(temporary)
        work, source = temporary / 'work', temporary / 'source'
        work.mkdir()
        source.mkdir()
        # These copied source bytes, rather than host .pyc files, are imported
        # by the isolated Python interpreter. Their hashes enter the action.
        import action_cache
        names = action_cache.dependencies('grading', language=language)
        names += ['benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/worker.py']
        sources = {}
        for name in names:
            raw = (ROOT / name).read_bytes()
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            sources[name] = hashlib.sha256(raw).hexdigest()
        (source / 'manifest.json').write_bytes(toolchains.canonical(record))
        (source / '.cache').mkdir()
        state = toolchains.CACHE / 'worker-state' / record['id']
        state.mkdir(parents=True, exist_ok=True)
        environment = {**record['manifest']['environment'], 'REA_GRADING_WORKER': '1'}
        args = sandbox(root, record['manifest'], work, source=source, state=state) + [
            '/usr/bin/python3', '-I', '-S', '-B', '/environment/benchmarks/scripts/grading/worker.py']
        raw = toolchains.canonical({'operation': operation, 'language': language, **request})
        token = _authorized.set(tuple(args))
        try:
            result = process.run(args, env=environment, input=raw, timeout=420, output_limit=4 * 1024**2)
        finally:
            _authorized.reset(token)
        if result.returncode:
            raise RuntimeError('Closed grading worker failed: ' + result.stderr.decode(errors='replace')[-4000:])
        value = json.loads(result.stdout)
        if value.get('environment_id') != record['id']:
            raise ValueError('Grading worker environment identity mismatch')
        if artifact is not None:
            artifact = Path(artifact)
            artifact.mkdir(parents=True, exist_ok=True)
            if (work / 'artifacts').exists():
                shutil.copytree(work / 'artifacts', artifact, dirs_exist_ok=True)
        grade = value['result']
        grade['grading_runtime'] = {'id': record['id'], 'files': record['files'], 'bytes': record['bytes'],
                                    'scope': 'closed_toolchain_python_and_executor_v1', 'source_inputs_sha256': sources,
                                    'integrity': ('verified_prepared_image; trusted_local; readonly_candidate_mounts'
                                        if __import__('startup_policy').trusted() else
                                        'full_content_hash_on_extraction_and_pre_post_stat_checks')}
        return grade


def validation_sources(language):
    import action_cache
    names = action_cache.dependencies('grading', language=language)
    return {name: toolchains.file_sha(ROOT / name) for name in names}


from startup_policy import offline_gate

@offline_gate
def require_validation(language):
    from .native import host_identity
    record = toolchains.load(language)
    path = toolchains.CACHE / 'validated' / (record['id'] + '.json')
    if not path.exists():
        raise RuntimeError('Grading runtime has not passed its controls; run make setup-grading')
    receipt = json.loads(path.read_bytes())
    if (not receipt.get('passed') or receipt.get('id') != record['id']
            or receipt.get('sources') != validation_sources(language) or receipt.get('host') != host_identity()):
        raise RuntimeError('Grading runtime controls are stale; run make setup-grading')


def validate_profile(language):
    from .native import host_identity
    record = toolchains.load(language)
    try:
        require_validation(language)
        print('GRADING RUNTIME CONTROLS CACHED:', language, record['id'][:16], flush=True)
        return
    except RuntimeError:
        pass
    cases = [{'hex': '00', 'point': 'control', 'category': 'directed', 'expected': 0 if language == 'c' else '0'},
             {'hex': '01', 'point': 'control', 'category': 'directed', 'expected': 1 if language == 'c' else '1'}]
    code = {
        'c': 'int64_t reconstructed(const uint8_t *data, size_t size) { return size ? data[0] : -1; }',
        'cpp': '#include <string>\n#include <vector>\n#include <map>\n#include <cstdint>\nstd::string reconstructed(std::span<const std::uint8_t> data) { std::map<int,int> values; values[0]=data[0]; return std::to_string(values[0]); }',
        'rust': 'pub fn reconstructed(data: &[u8]) -> String { let mut v = vec![data[0]]; v.sort(); v[0].to_string() }',
        'go': 'package main\nimport "strconv"\nfunc reconstructed(data []byte) string { return strconv.Itoa(int(data[0])) }',
    }[language]
    source_before = validation_sources(language)
    grade = invoke('evaluate', language, {'code': code, 'cases': cases, 'track': 'basic' if language == 'c' else 'complex',
                                        'standard': 'c++20', 'abi': 1}, trusted_control=True)
    if not grade.get('program_success'):
        raise RuntimeError('Grading runtime positive control failed: ' + json.dumps(grade)[:4000])
    if validation_sources(language) != source_before:
        raise RuntimeError('Grading runtime source changed during controls')
    receipt = {'id': record['id'], 'passed': True, 'model_api_calls': 0, 'checks': 2,
               'sources': source_before, 'compiler': record['manifest']['compiler'], 'host': host_identity()}
    path = toolchains.CACHE / 'validated' / (record['id'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp-' + str(os.getpid()))
    temporary.write_bytes(toolchains.canonical(receipt) + b'\n')
    temporary.replace(path)
    print('GRADING RUNTIME CONTROLS PASS:', language, record['id'][:16], flush=True)
