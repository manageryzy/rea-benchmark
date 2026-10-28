"""Automatic WSL execution in temporary Linux storage, with stable action paths.

Only public installed runtimes are packed. Private bind mounts expose temporary
copies at their original paths. Results and CAS remain in the workspace; no
credentials or reasoning are copied. The runtime archive is a pinned dependency,
like a compiler image: explicitly refresh it after changing installed tools.
"""
import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'benchmarks/config/runtime-storage.json'
PATHS = ('node_modules', '.tools/node-v24.22.0-linux-x64',
         '.tools/node-v24.14.0-linux-x64', '.tools/ghidra_12.1.4_PUBLIC',
         '.tools/harness-0.2.1-rc.2/node_modules', '.tools/zcode-3.16.1/agent')
STORE = ROOT / '.cache/benchmark-runtime/installed-runtime'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def settings():
    value = json.loads(CONFIG.read_bytes())
    if (set(value) != {'schema_version', 'mode', 'temporary_root', 'cleanup_grace_seconds'}
            or value['schema_version'] not in (1, 2) or value['mode'] not in ('auto', 'off', 'linux_temp')
            or not Path(value['temporary_root']).is_absolute()
            or type(value['cleanup_grace_seconds']) is not int
            or not 1 <= value['cleanup_grace_seconds'] <= 60):
        raise ValueError('Invalid temporary runtime configuration')
    return value


def windows_mount(path, *, mountinfo=None):
    text = Path('/proc/self/mountinfo').read_text() if mountinfo is None else mountinfo
    path = Path(path).absolute()
    selected = (0, '')
    for line in text.splitlines():
        left, separator, right = line.partition(' - ')
        fields = left.split()
        if not separator or len(fields) < 5:
            continue
        mount = fields[4].replace('\\040', ' ').replace('\\134', '\\')
        if path.is_relative_to(mount) and len(mount) > selected[0]:
            selected = (len(mount), right.split()[0])
    return selected[1] in ('9p', 'drvfs', 'fuseblk')


def is_wsl(*, release=None):
    release = Path('/proc/sys/kernel/osrelease').read_text() if release is None else release
    return 'microsoft' in release.lower() or 'wsl' in release.lower()


def automatic(root=ROOT, configuration=None):
    configuration = configuration or settings()
    return configuration['mode'] == 'linux_temp' or (configuration['mode'] == 'auto'
            and is_wsl() and windows_mount(root))


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(raw)
        pending = Path(stream.name)
    try:
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def pack_installed(stream, root, paths):
    """Overlap Windows small-file reads; bound buffered contents to 16 files."""
    def names(path, name):
        yield path, name
        if path.is_dir() and not path.is_symlink():
            with os.scandir(path) as entries:
                children = sorted(entries, key=lambda row: row.name)
            for child in children:
                # DirEntry caches type information; don't serialize a stat per
                # regular file while the worker pool is reading file contents.
                child_path, child_name = path / child.name, name + '/' + child.name
                if child.is_dir(follow_symlinks=False):
                    yield from names(child_path, child_name)
                else:
                    yield child_path, child_name
    def read(item):
        path, name = item
        before = path.lstat()
        member = tarfile.TarInfo(name)
        member.mode = stat.S_IMODE(before.st_mode)
        # Ownership and timestamps do not describe runtime semantics. Normalize
        # archive bookkeeping while preserving every byte, link and mode.
        member.uid = member.gid = 0
        member.mtime = 0
        content = None
        if stat.S_ISDIR(before.st_mode):
            member.type = tarfile.DIRTYPE
        elif stat.S_ISLNK(before.st_mode):
            member.type = tarfile.SYMTYPE
            member.linkname = os.readlink(path)
        elif stat.S_ISREG(before.st_mode):
            member.size = before.st_size
            content = path.read_bytes()
        else:
            raise ValueError('Special file in installed runtime')
        after = path.lstat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino,
                before.st_mode) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns,
                                   after.st_ino, after.st_mode):
            raise ValueError('Installed runtime changed during packing')
        return member, content
    with ThreadPoolExecutor(max_workers=8) as workers:
        for name in paths:
            source = root / name
            if source.is_symlink() or not source.is_dir():
                raise ValueError('Installed runtime must be a regular directory before packing')
            print('Packing installed public runtime: ' + name, flush=True)
            pending = deque()
            for item in names(source, name):
                pending.append(workers.submit(read, item))
                if len(pending) >= 16:
                    member, content = pending.popleft().result()
                    stream.addfile(member, io.BytesIO(content) if content is not None else None)
            while pending:
                member, content = pending.popleft().result()
                stream.addfile(member, io.BytesIO(content) if content is not None else None)


def package(root=ROOT, store=STORE, *, refresh=False, paths=PATHS, verify=True):
    """Pin public runtime bytes once, never credential or result trees."""
    import fcntl
    root, store = Path(root), Path(store)
    if not paths or any(name not in PATHS for name in paths):
        raise ValueError('Only installed public runtime paths may be packed')
    store.mkdir(parents=True, exist_ok=True)
    index = store / 'active.json'
    with (store / 'package.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if index.exists() and not refresh:
            value = json.loads(index.read_bytes())
            if (value.get('schema_version') != 1 or value.get('paths') != list(paths)
                    or not isinstance(value.get('sha256'), str)
                    or not re.fullmatch('[0-9a-f]{64}', value['sha256'])):
                raise ValueError('Pinned runtime package has a different contract; refresh explicitly')
            archive = store / (value['sha256'] + '.tar')
            if archive.is_symlink() or not archive.is_file() or (verify and sha_file(archive) != value['sha256']):
                raise ValueError('Pinned runtime archive is corrupt')
            return value, archive
        # The exclusive package lock proves these leftovers have no live packer.
        for leftover in store.glob('pack-*'):
            if not leftover.is_symlink() and leftover.is_dir():
                shutil.rmtree(leftover)
        with tempfile.TemporaryDirectory(prefix='pack-', dir=store) as temporary:
            archive = Path(temporary) / 'runtime.tar'
            with tarfile.open(archive, 'w', dereference=False) as stream:
                pack_installed(stream, root, paths)
            identity = sha_file(archive)
            destination = store / (identity + '.tar')
            if destination.exists():
                if sha_file(destination) != identity:
                    raise ValueError('Existing runtime archive changed')
            else:
                archive.rename(destination)
            value = {'schema_version': 1, 'paths': list(paths), 'sha256': identity,
                     'bytes': destination.stat().st_size, 'model_api_calls': 0}
            atomic(index, canonical(value) + b'\n')
            return value, destination


@contextmanager
def temporary_runtime(value, archive, *, temporary_root='/tmp'):
    """Delete extracted trees on success, error, interruption and failed launch."""
    reap_stale(temporary_root)
    with tempfile.TemporaryDirectory(prefix='rea-runtime-', dir=temporary_root) as temporary:
        directory = Path(temporary)
        import fcntl
        with (directory / '.owner.lock').open('x+b') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            # Read the Windows file once. Extraction and the second integrity
            # check operate on this private verified Linux snapshot, avoiding
            # repeated multi-gigabyte reads across the mount.
            snapshot = directory / 'runtime.tar'
            digest = hashlib.sha256()
            with Path(archive).open('rb') as source, snapshot.open('xb') as destination:
                while block := source.read(1024 * 1024):
                    digest.update(block)
                    destination.write(block)
            if digest.hexdigest() != value['sha256']:
                raise ValueError('Runtime archive changed before extraction')
            snapshot.chmod(0o400)
            with tarfile.open(snapshot, 'r') as stream:
                for member in stream:
                    name = PurePosixPath(member.name)
                    if (name.is_absolute() or '..' in name.parts or member.isdev()
                            or not any(name == PurePosixPath(root) or name.is_relative_to(root) for root in value['paths'])):
                        raise ValueError('Runtime archive contains an unapproved member')
                    stream.extract(member, directory, filter='data')
            if sha_file(snapshot) != value['sha256']:
                raise ValueError('Runtime archive changed during extraction')
            snapshot.unlink()
            yield directory


def reap_stale(temporary_root):
    """Clean SIGKILL leftovers; an advisory owner lock protects every live run."""
    import fcntl
    for directory in Path(temporary_root).glob('rea-runtime-*'):
        if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != os.getuid():
            continue
        owner = directory / '.owner.lock'
        if not owner.is_file() or owner.is_symlink():
            continue
        try:
            with owner.open('r+b') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                mount = directory / 'mount'
                if os.path.ismount(mount):
                    from runtime_image import unmount
                    unmount(mount)
                shutil.rmtree(directory)
        except (BlockingIOError, FileNotFoundError):
            continue


def guarded_process(arguments, *, grace=10):
    """Forward stop signals and reap the namespace before cleaning its tree."""
    child = subprocess.Popen(arguments, start_new_session=True)
    previous = {}
    def stop(number, frame):
        raise KeyboardInterrupt
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, stop)
        try:
            return child.wait()
        except BaseException:
            for number in previous:
                signal.signal(number, signal.SIG_IGN)
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def preserve_inventories(source, destination):
    """Persist immutable inventory receipts, never the disposable SQLite memo."""
    for path in Path(source).glob('*.json'):
        if path.is_symlink() or not re.fullmatch('[0-9a-f]{64}\\.json', path.name):
            raise ValueError('Unexpected temporary inventory record')
        raw = path.read_bytes()
        value = json.loads(raw)
        if (set(value) != {'identity', 'manifest'} or value['identity'] != path.stem
                or hashlib.sha256(canonical(value['manifest'])).hexdigest() != path.stem):
            raise ValueError('Temporary inventory identity mismatch')
        target = Path(destination) / path.name
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('Conflicting immutable inventory record')
        if not target.exists():
            atomic(target, raw)


def execute(arguments, *, root=ROOT, configuration=None):
    configuration = configuration or settings()
    if configuration.get('schema_version', 1) == 2 and configuration['mode'] != 'off':
        from runtime_image import execute as execute_image
        return execute_image(arguments, root=root, configuration=configuration)
    if not automatic(root, configuration):
        return guarded_process(arguments, grace=configuration['cleanup_grace_seconds'])
    import fcntl
    lock = Path(root) / '.cache/benchmark-runtime/runtime-process.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a+b') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another benchmark owns temporary runtime staging') from None
        if (Path(root) / '.cache/benchmark-runtime/live-batch.lock').exists():
            raise RuntimeError('Another benchmark batch is active; no temporary runtime started')
        value, archive = package(root, verify=False)
        print('Verifying and extracting the pinned public runtime on Linux storage', flush=True)
        with temporary_runtime(value, archive, temporary_root=configuration['temporary_root']) as directory:
            # Ordinary bind mounts carry nodev. Reusing the host /dev through
            # the root bind makes /dev/null fail with EACCES during nested
            # libuv/MCP child setup, before execve is attempted. Supply private
            # standard devices instead of exposing arbitrary host devices.
            command = ['/usr/bin/bwrap', '--die-with-parent', '--unshare-pid', '--bind', '/', '/',
                       '--dev', '/dev', '--proc', '/proc']
            for name in value['paths']:
                # Preserve the installation's access semantics. Copies are
                # disposable; strict runtime attestation still detects changes.
                command.extend(['--bind', str(directory / name), str(root / name)])
            # SQLite's random page I/O must not remain on the Windows mount.
            # This cache is only a hashing optimization, not an action input.
            memo = directory / 'attestation'; memo.mkdir()
            persistent = Path(root) / '.cache/benchmark-runtime/attestation'
            persistent.mkdir(parents=True, exist_ok=True)
            command.extend(['--bind', str(memo), str(persistent)])
            if not Path(root).resolve().is_relative_to('/tmp'):
                # Keep descendant tools' own /tmp files inside the same cleanup
                # guard. Workspaces already inside /tmp must stay visible.
                scratch = directory / 'scratch'; scratch.mkdir()
                command.extend(['--bind', str(scratch), '/tmp',
                    '--setenv', 'TMPDIR', '/tmp', '--setenv', 'TMP', '/tmp', '--setenv', 'TEMP', '/tmp'])
            command.extend(['--chdir', str(root), '--', *arguments])
            print('WSL runtime: temporary Linux storage; stable paths; automatic cleanup', flush=True)
            try:
                return guarded_process(command, grace=configuration['cleanup_grace_seconds'])
            finally:
                preserve_inventories(memo, persistent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='action', required=True)
    setup = subparsers.add_parser('setup')
    setup.add_argument('--refresh', action='store_true')
    run = subparsers.add_parser('run')
    run.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = getattr(args, 'command', [])
    command = command[1:] if command[:1] == ['--'] else command
    if args.action == 'run' and not command:
        parser.error('run needs a command after --')
    def stop(number, frame):
        raise KeyboardInterrupt
    previous = {number: signal.signal(number, stop) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        if args.action == 'setup':
            value, archive = package(refresh=args.refresh)
            print('PINNED PUBLIC RUNTIME: ' + value['sha256'] + '; zero model calls')
            return 0
        return execute(command)
    except KeyboardInterrupt:
        return 130
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == '__main__':
    raise SystemExit(main())
