"""Rootless, pinned tools: SquashFUSE when available, reusable extraction otherwise.

This is a tool environment, not the candidate security boundary. The host kernel,
network, certificates, credentials and workspace remain external. Candidate
execution retains its existing separate closed grading sandbox.
"""
import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time

import runtime_storage as storage

ROOT = storage.ROOT
CONFIG = ROOT / 'benchmarks/config/runtime-image.json'
DIGEST = re.compile(r'[0-9a-f]{64}')


def policy():
    value = json.loads(CONFIG.read_bytes())
    if (value['schema_version'] != 1 or value['backend'] not in ('auto', 'fuse', 'extract')
            or value['compression'] not in ('lz4', 'zstd', 'gzip')
            or not 1 <= value['processors'] <= 16):
        raise ValueError('Invalid runtime image policy')
    return value


def cache_root(root=ROOT, spec=None):
    spec = spec or policy()
    base = Path(spec['cache_root']).expanduser()
    if not base.is_absolute() or storage.windows_mount(base):
        raise ValueError('Runtime image cache must be on an absolute Linux filesystem path')
    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]
    path = base / key
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError('Unsafe runtime image cache owner')
    return path


@contextmanager
def locked(path, *, shared=False, nonblocking=False):
    with Path(path).open('a+b') as stream:
        fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) |
                    (fcntl.LOCK_NB if nonblocking else 0))
        yield


def facts(path):
    s = Path(path).lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('Image must be a regular file, not a symlink')
    return [s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def verified_image(path, identity, receipt):
    """No atime key: a metadata change forces a full hash, once per image change."""
    if not DIGEST.fullmatch(identity):
        raise ValueError('Invalid image digest')
    before = facts(path)
    expected = {'sha256': identity, 'stat': before}
    if receipt.exists() and json.loads(receipt.read_bytes()) == expected:
        return
    if storage.sha_file(path) != identity or facts(path) != before:
        raise ValueError('Runtime image is corrupt or changed while verifying')
    storage.atomic(receipt, storage.canonical(expected))


def load(cache):
    record = json.loads((cache / 'active.json').read_bytes())
    identity = record.get('image_sha256', '')
    if record.get('schema_version') != 1 or not DIGEST.fullmatch(identity):
        raise ValueError('Invalid runtime image record')
    if hashlib.sha256(storage.canonical(record['manifest'])).hexdigest() != record['manifest_sha256']:
        raise ValueError('Invalid runtime image manifest')
    verified_image(cache / (identity + '.squashfs'), identity, cache / (identity + '.verified.json'))
    return record


def native_libraries(builder, search):
    """Only inspect trusted installed tools, never candidates or targets."""
    pending, visited = list(builder.native), set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        result = subprocess.run(['/usr/bin/ldd', str(path)], capture_output=True, text=True,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LD_LIBRARY_PATH': ':'.join(map(str, search))}, timeout=30)
        output = result.stdout + result.stderr
        if 'not found' in output or (result.returncode and not any(
                text in output for text in ('not a dynamic executable', 'statically linked'))):
            raise ValueError('Unresolved tool library: ' + str(path))
        for name in re.findall(r'(?:=>\s*)?(/[^\n]+?)\s+\(0x[0-9a-fA-F]+\)', output):
            library = Path(name.strip())
            builder.add(library)
            pending.append(library.resolve())


def attestation_roots(root=ROOT):
    """One source of required system roots, shared by packaging and admission."""
    policy = json.loads((Path(root)/'benchmarks/config/runtime-attestation.json').read_bytes())
    paths = sorted({name for component in policy['components'].values()
                    for name in component['paths'] if Path(name).is_absolute()})
    reviewed = tuple(Path(name).resolve() for name in (
        '/usr', '/lib', '/lib64', '/bin', '/sbin', '/etc/ld.so.cache',
        '/etc/java-21-openjdk', '/etc/ssl/certs', '/etc/python3.12'))
    if any(not any(Path(name).resolve().is_relative_to(prefix) for prefix in reviewed) for name in paths):
        raise ValueError('Attestation packaging path outside reviewed public system roots')
    missing = [name for name in paths if not Path(name).exists()]
    if missing:
        raise RuntimeError('Missing required attestation paths: ' + ', '.join(missing))
    return paths


def copy_system_files(builder, destination):
    files = builder.files()
    def copy(item):
        name, info = item
        target = destination/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(builder.paths[name][0], target)
        target.chmod(info['mode'])
        if storage.sha_file(target) != info['sha256']:
            raise ValueError('Tool changed during image capture: ' + name)
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(copy, files.items()))
    return files


def capture_workspace_libraries(destination, workspace, root, captured):
    """Resolve the pinned Node/Ghidra/Harness ELFs before image publication."""
    from grading.toolchains import Builder
    import runtime_attestation as attestation
    inventory = attestation.Inventory(workspace, attestation.config(root), fresh=True)
    try:
        for directory, _, names in os.walk(workspace):
            for name in names:
                path = Path(directory)/name
                if not ('.so' in name or path.suffix == '.node' or
                        path.suffix in ('', '.bin') and path.stat().st_mode & 0o111):
                    continue
                with path.open('rb') as stream:
                    header = stream.read(20)
                if header[:6] == b'\x7fELF\x02\x01' and header[18:20] == b'\x3e\x00':
                    inventory.native.add(path)
        closure = inventory.shared_libraries()
    finally:
        inventory.close()
    builder = Builder(); builder.allow('/usr', '/lib', '/lib64')
    for name, entry in closure['entries'].items():
        if name.startswith('system/') and 'sha256' in entry:
            relative = name[len('system/'):]
            if relative not in captured['files']:
                builder.add('/'+relative)
    extra = copy_system_files(builder, destination)
    captured['files'].update(extra)
    captured['workspace_loader_files'] = len(closure['entries'])
    print('Pinned workspace native closure: %d paths; %d added system files' %
          (len(closure['entries']), len(extra)), flush=True)


def preserve_loader_aliases(destination, files, aliases):
    """Retain merged-/usr loader links instead of moving their canonical identity."""
    for name, info in list(files.items()):
        if not name.startswith('usr/lib64/') or 'source_link' not in info:
            continue
        resolved = info.get('resolved_link', '')
        if not resolved.startswith('system/usr/lib/'):
            raise ValueError('Unexpected loader alias target: ' + name)
        target = resolved[len('system/'):]
        if target not in files or files[target]['sha256'] != info['sha256']:
            raise ValueError('Missing canonical loader dependency: ' + target)
        (destination/name).unlink()
        del files[name]
        aliases[name] = '/'+target


def capture_system(destination, spec, root):
    from grading.toolchains import Builder
    builder = Builder()
    rust = Path(subprocess.check_output(['rustc', '--print', 'sysroot'], text=True).strip()).resolve()
    java = Path('/usr/lib/jvm/java-21-openjdk-amd64')
    builder.allow('/usr', '/lib', '/lib64', '/etc/java-21-openjdk', '/etc/ssl/certs/java/cacerts', rust)
    required = attestation_roots(root)
    builder.allow(*required)
    aliases = {}
    for name in spec['commands']:
        source = shutil.which(name, path='/usr/bin:/bin')
        if not source:
            raise RuntimeError('Missing runtime command: ' + name)
        actual = Path(source).resolve()
        command_destination = 'usr/bin/' + name
        builder.add(actual)
        if str(actual).lstrip('/') != command_destination:
            aliases[command_destination] = str(actual)
    for name in spec['system_directories']:
        builder.add(name, exclude=('__pycache__', 'test', 'tests', 'testdata', 'src.zip', 'rust-objcopy', 'sitecustomize.py'))
    covered = [Path(name).resolve() for name in spec['system_directories']]
    for name in required:
        if not any(Path(name).resolve().is_relative_to(directory) for directory in covered):
            source = Path(name).parent.resolve()/Path(name).name
            if source.is_file():
                actual = source.resolve()
                builder.add(actual)
                if source != actual:
                    aliases[str(source).lstrip('/')] = str(actual)
            else:
                builder.add(name, exclude=('__pycache__', 'test', 'tests', 'sitecustomize.py'))
    aliases['usr/bin/rustc'] = str(rust / 'bin/rustc')
    aliases['usr/bin/cargo'] = str(rust / 'bin/cargo')
    builder.add(rust / 'lib', exclude=('src', 'rust-objcopy'))
    # Preserve rustc's sysroot layout without depending on a mutable rustup selection.
    builder.add(rust / 'bin/rustc')
    builder.add(rust / 'bin/cargo')
    for name in ('libc.so', 'libc_nonshared.a', 'libm.so', 'libm.a', 'libpthread.a', 'libdl.a', 'librt.a', 'libutil.a', 'Scrt1.o', 'crt1.o', 'crti.o', 'crtn.o'):
        value = subprocess.check_output(['gcc', '-print-file-name=' + name], text=True).strip()
        builder.linker_file(value)
    native_libraries(builder, [java / 'lib', java / 'lib/server', rust / 'lib'])
    # ldd may list only /lib64's alias. Include the real loader location too,
    # before replacing that flattened alias with the original canonical link.
    for name, (actual, metadata) in list(builder.paths.items()):
        if name.startswith('usr/lib64/') and 'source_link' in metadata:
            builder.add(actual)
    files = copy_system_files(builder, destination)
    preserve_loader_aliases(destination, files, aliases)
    for name, actual in aliases.items():
        target = destination/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(actual)
    # ELF interpreters and conventional merged-/usr aliases.
    for name in ('lib', 'lib64', 'bin', 'sbin'):
        link = destination / name
        if not link.exists():
            link.symlink_to('usr/' + name)
    versions = {}
    for name, command in {'gcc': ['gcc','--version'], 'clang': ['clang++-20','--version'],
            'rust': [str(rust/'bin/rustc'),'--version'], 'go': [str(root/'.tools/go1.23.2/bin/go'),'version'],
            'java': ['java','-version'], 'python': ['python3','--version']}.items():
        versions[name] = subprocess.check_output(command, stderr=subprocess.STDOUT, text=True).splitlines()[0]
    return {'files': files, 'aliases': aliases, 'rust_sysroot': str(rust), 'versions': versions,
            'attestation_roots': required,
            'system_mounts': sorted(name for name in set(files) | set(aliases) if name.startswith('etc/'))}


def copy_public_tree(source, destination):
    """Bound Windows metadata/copy latency during explicit image capture."""
    pending = []
    def walk(source, target):
        target.mkdir(parents=True, exist_ok=True)
        with os.scandir(source) as entries:
            for entry in entries:
                src, dst = Path(entry.path), target / entry.name
                if entry.is_symlink():
                    resolved = src.resolve()
                    if not resolved.is_relative_to(source_root):
                        raise ValueError('Public tool link escapes captured tree: ' + str(src))
                    dst.symlink_to(os.readlink(src))
                elif entry.is_dir(follow_symlinks=False): walk(src, dst)
                elif entry.is_file(follow_symlinks=False): pending.append((src, dst))
                else: raise ValueError('Special file in public tool')
    source_root = Path(source).resolve()
    walk(Path(source), Path(destination))
    def copy(pair):
        src, dst = pair
        before = facts(src)
        shutil.copyfile(src, dst)
        dst.chmod(stat.S_IMODE(before[2]))
        if facts(src) != before: raise ValueError('Public tool changed during capture')
    with ThreadPoolExecutor(max_workers=16) as workers:
        list(workers.map(copy, pending))


def build(root=ROOT, *, refresh=False, reuse_runtime=False):
    spec = policy()
    cache = cache_root(root, spec)
    with locked(cache / 'lease.lock'), locked(cache / 'build.lock'):
        if (cache / 'active.json').exists() and not refresh:
            return load(cache), cache
        for command in ('mksquashfs', 'unsquashfs', 'bwrap'):
            if not shutil.which(command):
                raise RuntimeError('Install runtime dependency: ' + command)
        print('Capturing pinned public runtime and compiler environment (one-time)', flush=True)
        previous = load(cache) if reuse_runtime and (cache/'active.json').exists() else None
        if previous:
            value = {'sha256': previous['manifest']['runtime_archive_sha256'],
                     'paths': [name for name in storage.PATHS if name in previous['manifest']['workspace_paths']]}
            archive = None  # The verified image retains these bytes after tar retirement.
        else:
            value, archive = storage.package(root, verify=False, refresh=refresh and not reuse_runtime)
        with tempfile.TemporaryDirectory(prefix='build-', dir=cache) as temporary:
            base = Path(temporary)
            tree = base / 'tree'; tree.mkdir()
            system = tree / 'system'; system.mkdir()
            print('Capturing compiler, Java, Python and native library closure', flush=True)
            captured = capture_system(system, spec, root)
            print('Offline compiler closure smoke before runtime packing', flush=True)
            smoke_record = {'manifest': {'toolchain': captured, 'workspace_paths': []}}
            with tempfile.TemporaryDirectory(prefix='compiler-smoke-', dir=cache) as smoke_tmp:
                cmd = container_command(tree, smoke_record, Path(smoke_tmp), root,
                    ['/usr/bin/python3', str(ROOT/'benchmarks/scripts/runtime_image_smoke.py')])
                cmd.insert(1, '--unshare-net')
                result = storage.guarded_process(cmd, grace=10)
                if result: raise subprocess.CalledProcessError(result, cmd)
            workspace = tree / 'workspace'; workspace.mkdir()
            print('Importing pinned public runtime archive', flush=True)
            if previous and previous['manifest']['runtime_archive_sha256'] == value['sha256']:
                print('Reusing verified runtime contents from previous image', flush=True)
                with image_root(previous, cache, base, spec['backend']) as (prior, _):
                    for name in value['paths']:
                        copy_public_tree(prior/'workspace'/name, workspace/name)
            else:
                with storage.temporary_runtime(value, archive, temporary_root=cache) as extracted:
                    for name in value['paths']:
                        target = workspace / name; target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(extracted/name), target)
            paths = list(value['paths'])
            for name in spec['workspace_extra']:
                if name not in ('.tools/go1.23.2', '.tools/benchmark-format'):
                    raise ValueError('Unreviewed workspace runtime path')
                if (root/name).is_dir():
                    print('Capturing public workspace tool: ' + name, flush=True)
                    copy_public_tree(root/name, workspace/name)
                    paths.append(name)
            capture_workspace_libraries(system, workspace, root, captured)
            print('Offline full runtime acceptance before image publication', flush=True)
            acceptance = base/'acceptance.json'
            with tempfile.TemporaryDirectory(prefix='runtime-accept-', dir=cache) as check_tmp:
                cmd = container_command(tree, {'manifest': {'toolchain': captured, 'workspace_paths': paths}},
                    Path(check_tmp), root, ['/usr/bin/python3', str(ROOT/'benchmarks/scripts/runtime_image_acceptance.py'),
                                           '--output', str(acceptance)])
                cmd.insert(1, '--unshare-net')
                result = storage.guarded_process(cmd, grace=10)
                if result: raise subprocess.CalledProcessError(result, cmd)
            proof = json.loads(acceptance.read_bytes())
            if proof.get('passed') is not True or proof.get('model_api_calls') != 0:
                raise ValueError('Incomplete runtime image acceptance')
            manifest = {'schema_version': 1, 'runtime_archive_sha256': value['sha256'],
                'workspace_paths': paths, 'toolchain': captured, 'policy': spec,
                'offline_acceptance': proof,
                'acceptance_source_sha256': storage.sha_file(ROOT/'benchmarks/scripts/runtime_image_acceptance.py'),
                'attestation_policy_sha256': storage.sha_file(root/'benchmarks/config/runtime-attestation.json'),
                'build_source_sha256': storage.sha_file(Path(__file__)),
                'grading_builder_sha256': storage.sha_file(ROOT/'benchmarks/scripts/grading/toolchains.py')}
            (tree/'manifest.json').write_bytes(storage.canonical(manifest))
            image = base/'image.squashfs'
            print('Building read-only SquashFS image', flush=True)
            subprocess.run(['mksquashfs', str(tree), str(image), '-noappend', '-no-progress',
                '-comp', spec['compression'], '-processors', str(spec['processors']), '-all-root',
                '-no-xattrs', '-mkfs-time', '0', '-all-time', '0'], check=True, stdout=subprocess.DEVNULL)
            identity = storage.sha_file(image)
            target = cache/(identity+'.squashfs')
            image.chmod(0o444)
            image.replace(target)
            record = {'schema_version': 1, 'image_sha256': identity, 'bytes': target.stat().st_size,
                'manifest_sha256': hashlib.sha256(storage.canonical(manifest)).hexdigest(), 'manifest': manifest}
            storage.atomic(cache/(identity+'.json'), storage.canonical(record))
            verified_image(target, identity, cache/(identity+'.verified.json'))
            storage.atomic(cache/'active.json', storage.canonical(record))
            print('Pinned image: ' + identity + '; bytes=' + str(record['bytes']), flush=True)
            return record, cache


def fuse_available():
    return bool(shutil.which('squashfuse') and shutil.which('fusermount3') and
                os.access('/dev/fuse', os.R_OK | os.W_OK))


def unmount(path):
    if os.path.ismount(path):
        subprocess.run(['fusermount3', '-u', str(path)], check=True, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


@contextmanager
def fuse_mount(image, temporary):
    mount = temporary/'mount'; mount.mkdir()
    process = None
    try:
        process = subprocess.Popen(['squashfuse', '-f', '-o', 'ro', str(image), str(mount)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 5
        while not os.path.ismount(mount):
            if process.poll() is not None or time.monotonic() >= deadline:
                raise OSError('FUSE mount unavailable in this namespace')
            time.sleep(0.02)
        yield mount
    finally:
        try:
            unmount(mount)
        finally:
            if process is not None:
                if process.poll() is None: process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            mount.rmdir()


def extracted_image(record, cache):
    identity = record['image_sha256']
    target = cache/(identity+'.tree')
    marker = cache/(identity+'.tree.json')
    with locked(cache/'extract.lock'):
        if target.is_symlink(): raise ValueError('Symlink runtime cache')
        if target.is_dir() and marker.exists():
            value = json.loads(marker.read_bytes())
            if value == {'sha256': identity, 'inode': target.stat().st_ino, 'device': target.stat().st_dev}:
                return target
            raise ValueError('Extracted runtime cache identity changed')
        if target.exists():
            raise ValueError('Unsealed extracted runtime cache; remove it explicitly')
        with tempfile.TemporaryDirectory(prefix='extract-', dir=cache) as temporary:
            stage = Path(temporary)/'tree'
            subprocess.run(['unsquashfs', '-no-progress', '-processors', '4', '-d', str(stage),
                str(cache/(identity+'.squashfs'))], check=True, stdout=subprocess.DEVNULL)
            # No task has writable mounts to this cache. Existing attestation
            # remains responsible for detecting runtime changes before/after use.
            stage.rename(target)
        storage.atomic(marker, storage.canonical({'sha256': identity,
            'inode': target.stat().st_ino, 'device': target.stat().st_dev}))
        return target


def check_manifest(tree, record):
    raw = (tree/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['manifest_sha256']:
        raise ValueError('Image metadata is not bound to the mounted contents')


@contextmanager
def image_root(record, cache, temporary, backend='auto'):
    if backend != 'extract' and fuse_available():
        manager = fuse_mount(cache/(record['image_sha256']+'.squashfs'), temporary)
        try:
            mount = manager.__enter__()
        except OSError:
            if backend == 'fuse': raise
            print('FUSE mount unavailable; using reusable extraction cache', flush=True)
        else:
            try:
                check_manifest(mount, record)
                yield mount, 'fuse'
            finally: manager.__exit__(None, None, None)
            return
    elif backend == 'fuse':
        raise RuntimeError('FUSE requested but device or helper unavailable')
    tree = extracted_image(record, cache)
    check_manifest(tree, record)
    yield tree, 'extract'


def container_command(tree, record, work, root, arguments, *, cache=None):
    manifest = record['manifest']
    command = ['/usr/bin/bwrap', '--die-with-parent', '--unshare-user', '--unshare-pid',
               '--bind', '/', '/', '--dev', '/dev', '--proc', '/proc']
    if cache is not None:
        command += ['--ro-bind', str(cache), str(cache)]
    # Pin system tools and loader as a whole tree, not thousands of mount calls.
    for name in ('usr', 'lib', 'lib64'):
        command += ['--ro-bind', str(tree/'system'/name), '/'+name]
    for name in manifest['toolchain'].get('system_mounts', []):
        if not name.startswith('etc/') or '..' in Path(name).parts:
            raise ValueError('Unexpected system configuration mount')
        command += ['--ro-bind', str(tree/'system'/name), '/'+name]
    rust = manifest['toolchain']['rust_sysroot']
    command += ['--ro-bind', str(tree/'system'/rust.lstrip('/')), rust]
    for name in manifest['workspace_paths']:
        if name not in (*storage.PATHS, '.tools/go1.23.2', '.tools/benchmark-format'):
            raise ValueError('Unexpected runtime workspace mount')
        command += ['--ro-bind', str(tree/'workspace'/name), str(root/name)]
    for name in ('scratch', 'attestation'): (work/name).mkdir()
    persistent = root/'.cache/benchmark-runtime/attestation'; persistent.mkdir(exist_ok=True, parents=True)
    command += ['--bind', str(work/'attestation'), str(persistent)]
    if not root.resolve().is_relative_to('/tmp'):
        command += ['--bind', str(work/'scratch'), '/tmp']
    # Select captured rustc directly, bypassing rustup and its mutable account config.
    path = ':'.join([str(root/'bin'), str(root/'.tools/node-v24.22.0-linux-x64/bin'),
        str(root/'.tools/go1.23.2/bin'), rust+'/bin', '/usr/bin', '/bin'])
    command += ['--setenv', 'PATH', path, '--setenv', 'JAVA_HOME', '/usr/lib/jvm/java-21-openjdk-amd64',
        '--setenv', 'RUSTUP_TOOLCHAIN', rust, '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
        '--setenv', 'TMPDIR', '/tmp', '--setenv', 'TMP', '/tmp', '--setenv', 'TEMP', '/tmp',
        '--chdir', str(root), '--', *arguments]
    if 'image_sha256' in record and 'manifest_sha256' in record:
        command[1:1] = ['--setenv', 'REA_PINNED_IMAGE_ID', record['image_sha256'],
                        '--setenv', 'REA_PINNED_IMAGE_MANIFEST', record['manifest_sha256']]
    return command


def execute(arguments, *, root=ROOT, configuration=None, backend=None):
    root = Path(root); spec = policy()
    configuration = configuration or storage.settings()
    lock = root/'.cache/benchmark-runtime/runtime-process.lock'; lock.parent.mkdir(parents=True, exist_ok=True)
    with locked(lock, nonblocking=True):
        if (root/'.cache/benchmark-runtime/live-batch.lock').exists():
            raise RuntimeError('Another benchmark batch is active')
        cache = cache_root(root, spec)
        if not (cache/'active.json').exists(): build(root)
        with locked(cache/'lease.lock', shared=True):
            record = load(cache)
            storage.reap_stale(configuration['temporary_root'])
            with tempfile.TemporaryDirectory(prefix='rea-runtime-', dir=configuration['temporary_root']) as tmp:
                work = Path(tmp)
                with locked(work/'.owner.lock'):
                    with image_root(record, cache, work, backend or spec['backend']) as (tree, selected_backend):
                        print('Pinned runtime: '+selected_backend+'; image='+record['image_sha256'], flush=True)
                        command = container_command(tree, record, work, root, arguments, cache=cache)
                        try: return storage.guarded_process(command, grace=configuration['cleanup_grace_seconds'])
                        finally: storage.preserve_inventories(work/'attestation', root/'.cache/benchmark-runtime/attestation')


def prune_legacy(root=ROOT):
    """Only retire the exact replaced staging tar, never grading archives/CAS."""
    cache = cache_root(root)
    with locked(root/'.cache/benchmark-runtime/runtime-process.lock', nonblocking=True), locked(cache/'lease.lock'):
        record = load(cache)
        smoke = cache/(record['image_sha256']+'.smoke.json')
        expected = {'image_sha256': record['image_sha256'], 'model_api_calls': 0, 'passed': True}
        if not smoke.exists() or json.loads(smoke.read_bytes()) != expected:
            raise RuntimeError('Run make runtime-smoke before pruning the replaced archive')
        identity = record['manifest']['runtime_archive_sha256']
        path = root/'.cache/benchmark-runtime/installed-runtime'/(identity+'.tar')
        if not path.exists(): return 0
        if path.is_symlink() or storage.sha_file(path) != identity:
            raise ValueError('Legacy archive differs; refusing deletion')
        size = path.stat().st_size
        storage.atomic(cache/(identity+'.retired.json'), storage.canonical({
            'archive_sha256': identity, 'replacement_image': record['image_sha256'], 'bytes': size}))
        path.unlink()
        print('Removed superseded runtime tar: '+str(size)+' bytes', flush=True)
        return size


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('setup','prune','smoke'))
    parser.add_argument('--backend', choices=('auto','fuse','extract'), help='Override mount backend for offline smoke only')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--reuse-runtime', action='store_true', help='Refresh system tools while retaining the selected public runtime archive')
    args = parser.parse_args()
    if args.action == 'setup': build(refresh=args.refresh, reuse_runtime=args.reuse_runtime)
    elif args.action == 'prune': prune_legacy()
    else:
        result = execute(['/usr/bin/python3', str(ROOT/'benchmarks/scripts/runtime_image_smoke.py')], backend=args.backend)
        if result: raise SystemExit(result)
        cache = cache_root(); record = load(cache)
        storage.atomic(cache/(record['image_sha256']+'.smoke.json'), storage.canonical({
            'image_sha256': record['image_sha256'], 'model_api_calls': 0, 'passed': True}))


def main():
    def stop(number, frame): raise KeyboardInterrupt
    previous = {number: signal.signal(number, stop) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        return cli()
    except KeyboardInterrupt:
        raise SystemExit(130)
    finally:
        for number, handler in previous.items(): signal.signal(number, handler)


if __name__ == '__main__': main()
