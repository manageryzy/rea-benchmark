"""Build content-addressed native toolchain images; no models or candidates.

The active image is pinned. Installing unrelated host software does not change
grading inputs; explicitly refreshing this profile selects newly captured bytes.
Archive/compression bookkeeping is outside the semantic grading action.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grading.image_manifest import (ROOT, CONFIG, CACHE, canonical, digest, file_sha, relative, policy, load, validate_record)


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value) + b'\n'
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Immutable grading image metadata differs')
        return
    with path.open('xb') as stream:
        stream.write(raw)


def command(arguments):
    env = {'PATH': '/usr/bin:/bin:' + str(Path.home() / '.cargo/bin'), 'LANG': 'C',
           'HOME': str(Path.home()), 'RUSTUP_AUTO_INSTALL': '0'}
    return subprocess.check_output(arguments, env=env, stderr=subprocess.STDOUT, timeout=30).decode().strip()


def elf(path):
    with Path(path).open('rb') as stream:
        return stream.read(4) == b'\x7fELF'


class Builder:
    def __init__(self):
        self.paths, self.directories, self.allowed, self.native, self.aliases = {}, set(), [], set(), []

    def allow(self, *roots):
        self.allowed.extend(Path(root).resolve() for root in roots)

    def add(self, source, destination=None, *, optional=False, exclude=(), ancestors=()):
        source = Path(source)
        if not source.exists():
            if optional:
                return
            raise ValueError('Missing required toolchain component: ' + str(source))
        source = source.parent.resolve() / source.name
        actual = source.resolve()
        if destination is None:
            destination = next((str(PurePosixPath(name) / actual.relative_to(root)) for root, name in self.aliases if actual.is_relative_to(root)), str(source).lstrip('/'))
        destination = str(relative(destination))
        if not any(actual.is_relative_to(root) for root in self.allowed):
            raise ValueError('Toolchain dependency escapes reviewed roots')
        if actual.is_dir():
            if actual in ancestors:
                raise ValueError('Cyclic toolchain directory symlink')
            self.directories.add(destination)
            for child in sorted(source.iterdir()):
                if child.name not in exclude:
                    self.add(child, destination + '/' + child.name, exclude=exclude, ancestors=(*ancestors, actual))
        elif actual.is_file():
            # Flatten aliases into the sealed image, while retaining their link
            # selection in the content manifest. No image link can escape it.
            facts = {}
            if source.is_symlink():
                link = os.readlink(source)
                facts['source_link'] = link if not Path(link).is_absolute() else 'absolute:' + actual.name
                facts['resolved_link'] = next((str(PurePosixPath(name) / actual.relative_to(root)) for root, name in self.aliases if actual.is_relative_to(root)), 'system/' + str(actual).lstrip('/'))
            previous = self.paths.get(destination)
            if previous and previous[0] != actual:
                raise ValueError('Conflicting grading-image bindings')
            self.paths[destination] = (actual, facts)
            if ('.so' in actual.name or actual.stat().st_mode & 0o111 and actual.suffix not in ('.py', '.sh')) and elf(actual):
                self.native.add(actual)
        else:
            raise ValueError('Special file in grading toolchain')

    def libraries(self):
        # Only trusted installed compiler/executor ELFs are inspected. Never
        # run ldd on a benchmark target or candidate executable.
        pending, visited = list(self.native), set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            result = subprocess.run(['/usr/bin/ldd', str(path)], env={'PATH': '/usr/bin:/bin', 'LANG': 'C'},
                                    capture_output=True, timeout=30)
            text = result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
            if 'not found' in text:
                raise ValueError('Unresolved installed toolchain shared library: ' + path.name)
            if result.returncode and not any(marker in text for marker in ('not a dynamic executable', 'statically linked')):
                raise ValueError('Cannot inspect installed toolchain ELF: ' + path.name)
            for line in text.splitlines():
                match = re.search(r'(?:=>\s*)?(/[^\n]+?)\s+\(0x[0-9a-fA-F]+\)', line)
                if match:
                    library = Path(match[1].strip())
                    self.add(library)
                    pending.append(library.resolve())

    def linker_file(self, path):
        path = Path(path)
        self.add(path)
        with path.open('rb') as stream:
            header = stream.read(128)
        if not elf(path) and (path.suffix in ('.so', '.ld') or header.lstrip().startswith(b'/* GNU ld script')):
            text = path.read_text()
            for name in re.findall(r'/[A-Za-z0-9_+.,/=-]+', re.sub(r'/\*.*?\*/', '', text, flags=re.S)):
                if Path(name).is_file():
                    self.add(Path(name))

    def files(self):
        def inspect(item):
            name, (path, facts) = item
            before = path.stat()
            result = {**facts, 'sha256': file_sha(path), 'bytes': before.st_size,
                      'mode': 0o555 if before.st_mode & 0o111 else 0o444, 'elf': path in self.native}
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                raise ValueError('Toolchain component changed during capture')
            return name, result
        with ThreadPoolExecutor(max_workers=16) as workers:
            return dict(workers.map(inspect, sorted(self.paths.items())))


def build(language, *, refresh=False, cache=CACHE):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / 'build.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _build(language, refresh=refresh, cache=cache)


def _build(language, *, refresh=False, cache=CACHE):
    cache = Path(cache)
    spec = policy(language)
    index_path = cache / 'active.json'
    active = json.loads(index_path.read_bytes()) if index_path.exists() else {}
    entry = active.get(language)
    if entry and entry['policy_sha256'] == digest(spec) and not refresh:
        return load(language, cache=cache)
    print('CAPTURE GRADING RUNTIME:', language, flush=True)
    builder = Builder()
    builder.allow('/usr', '/lib', '/lib64', '/etc/ld.so.cache')
    builder.add('/usr/bin/bwrap')
    builder.add('/usr/bin/python3')
    stdlib = command([spec['python'], '-I', '-S', '-c', 'import sysconfig; print(sysconfig.get_path("stdlib"))'])
    builder.add(stdlib, exclude=('__pycache__', 'test', 'tests', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'sitecustomize.py'))
    builder.add('/etc/ld.so.cache')
    builder.add('/usr/lib/locale/C.utf8', optional=True)
    for root in ('usr', 'lib', 'lib64', 'opt', 'usr/bin'):
        builder.directories.add(root)
    if language in ('c', 'cpp', 'rust'):
        compiler = spec['compiler'].get('compiler', '/usr/bin/gcc') if language != 'rust' else '/usr/bin/gcc'
        compiler = Path(shutil.which(compiler) or compiler)
        builder.add(compiler)
        if language in ('c', 'cpp'):
            executable = compiler
            logical = str(compiler)
        for tool in ('cc1plus' if language == 'cpp' else 'cc1', 'collect2', 'lto-wrapper', 'as', 'ld'):
            name = command([str(compiler), '-print-prog-name=' + tool])
            resolved = Path(shutil.which(name) or name)
            builder.add(resolved)
            if tool in ('as', 'ld'):
                builder.add(resolved, 'usr/bin/' + tool)
        for include in ('include', 'include-fixed'):
            name = command([str(compiler), '-print-file-name=' + include])
            if Path(name).is_dir():
                builder.add(name)
        if language in ('c', 'cpp'):
            builder.add('/usr/include', exclude=() if language == 'cpp' else ('c++',))
            builder.add('/usr/local/include', optional=True)
        libraries = ['libgcc.a', 'libgcc_eh.a', 'libgcc_s.so', 'libgcc_s.so.1', 'liblto_plugin.so',
                     'libc.so', 'libc_nonshared.a', 'libm.so', 'libm.a', 'libpthread.a', 'libdl.a', 'librt.a', 'libutil.a',
                     'Scrt1.o', 'crt1.o', 'crti.o', 'crtn.o', 'crtbegin.o', 'crtbeginS.o', 'crtbeginT.o', 'crtend.o', 'crtendS.o']
        if language == 'cpp':
            libraries += ['libstdc++.so', 'libstdc++.so.6', 'libstdc++.a']
        for name in libraries:
            path = Path(command([str(compiler), '-print-file-name=' + name]))
            if not path.is_absolute() or not path.is_file():
                raise ValueError('Unresolved compiler link input: ' + name)
            builder.linker_file(path)
    if language == 'rust':
        root = Path(command(['rustc', '--print', 'sysroot'])).resolve()
        builder.allow(root)
        builder.aliases.append((root, 'opt/rust'))
        builder.add(root / 'bin/rustc', 'opt/rust/bin/rustc')
        # The installed rust-objcopy has an unresolved private LLVM RUNPATH.
        # This compiler contract links through GCC and never invokes objcopy;
        # omit that optional post-link utility rather than using host libraries.
        builder.add(root / 'lib', 'opt/rust/lib', exclude=('src', 'rust-objcopy'))
        executable, logical = root / 'bin/rustc', '/opt/rust/bin/rustc'
    if language == 'go':
        root = Path(spec['compiler']['root'].replace('@workspace', str(ROOT))).resolve()
        builder.allow(root)
        builder.aliases.append((root, 'opt/go'))
        for name in ('bin/go', 'pkg/tool/linux_amd64', 'pkg/include', 'src', 'lib', 'go.env', 'VERSION'):
            builder.add(root / name, 'opt/go/' + name, exclude=('testdata',))
        executable, logical = root / 'bin/go', '/opt/go/bin/go'
    version = command([str(executable), 'version' if language == 'go' else '--version'])
    if spec['compiler']['version'] not in version.splitlines()[0]:
        raise ValueError('Installed compiler differs from configured version')
    builder.libraries()
    files = builder.files()
    loader = next((name for name in files if re.search(r'/ld-linux.*\.so', name)), None)
    if loader is None:
        raise ValueError('Missing pinned ELF loader')
    manifest = {'schema_version': 1, 'language': language, 'policy_sha256': digest(spec),
        'compiler': {'command': logical, 'version': version, 'executable_sha256': file_sha(executable)},
        'python_version': command([spec['python'], '-I', '-S', '--version']), 'files': files,
        'directories': sorted(builder.directories), 'loader': loader,
        'root_aliases': {name: str(Path('/' + name).resolve()).lstrip('/') for name in ('lib', 'lib64') if Path('/' + name).is_symlink()},
        'environment': {'PATH': '/opt/rust/bin:/opt/go/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
            'HOME': '/tmp', 'TMPDIR': '/tmp', 'GOROOT': '/opt/go', 'GOCACHE': '/work/go-cache',
            'GO111MODULE': 'off', 'GOPROXY': 'off', 'GOSUMDB': 'off', 'CGO_ENABLED': '0',
            'GOTOOLCHAIN': 'local', 'GOMEMLIMIT': '128MiB', 'SOURCE_DATE_EPOCH': spec['source_date_epoch']},
        'permissions': {'network': 'unshared', 'filesystem': 'declared_image_and_private_work_only',
                        'devices': 'bubblewrap_minimal', 'proc': 'private_pid_namespace'}}
    identity = digest(manifest)
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='capture-', dir=cache) as temporary:
        archive = Path(temporary) / 'image.tar.gz'
        unique = {}
        for name, info in files.items():
            unique.setdefault(info['sha256'], (builder.paths[name][0], info['bytes']))
        with archive.open('wb') as output, gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0, compresslevel=1) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|') as tar:
                for sha, (path, size) in sorted(unique.items()):
                    info = tarfile.TarInfo('blobs/' + sha)
                    info.size, info.mode, info.mtime = size, 0o444, 0
                    with path.open('rb') as source:
                        raw = source.read()
                    if hashlib.sha256(raw).hexdigest() != sha:
                        raise ValueError('Toolchain changed while packaging')
                    tar.addfile(info, io.BytesIO(raw))
        archive_sha = file_sha(archive)
        target = cache / 'archives' / (archive_sha + '.tar.gz')
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if file_sha(target) != archive_sha:
                raise ValueError('Corrupt existing grading archive')
        else:
            archive.rename(target)
        record = {'id': identity, 'manifest': manifest, 'archive_sha256': archive_sha,
                  'files': len(files), 'bytes': sum(item['bytes'] for item in files.values())}
        atomic(cache / 'profiles' / (identity + '.json'), record)
        if digest(policy(language)) != digest(spec):
            raise ValueError('Grading policy changed during capture')
        active[language] = {'id': identity, 'policy_sha256': digest(spec)}
        replacement = Path(temporary) / 'active.json'
        replacement.write_bytes(canonical(active) + b'\n')
        replacement.replace(index_path)
    print('PINNED GRADING RUNTIME:', language, identity[:16], len(files), 'files', flush=True)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=['c', 'cpp', 'rust', 'go'], action='append')
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    for language in args.language or ['c', 'cpp', 'rust', 'go']:
        build(language, refresh=args.refresh)
        sys.path.insert(0, str(ROOT / 'benchmarks/scripts'))
        from grading.runtime import validate_profile
        validate_profile(language)
    from grading.evidence import save_pins
    save_pins()
