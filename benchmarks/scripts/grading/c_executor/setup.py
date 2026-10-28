"""Fetch one hash-pinned wheel and seal its module bytes; no model access."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import re
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[4]
CONFIG = ROOT / 'benchmarks/config/c-executor.json'
CACHE = ROOT / '.cache/benchmark-c-executor/v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def prepare(refresh=False):
    from grading.image_manifest import relative, load
    from grading.c_executor.runtime import engine_record
    spec = json.loads(CONFIG.read_bytes())['engine']
    CACHE.mkdir(parents=True, exist_ok=True)
    if (CACHE / 'active.json').exists() and not refresh:
        try:
            identity, record = engine_record(check_policy=False)
        except (ValueError, RuntimeError):
            pass
        else:
            for name, item in record['files'].items():
                path = CACHE / identity / name
                if path.is_symlink() or sha(path) != item['sha256']:
                    raise ValueError('Pinned C executor module changed; refusing automatic recapture')
            (CACHE / 'active.json').write_bytes(canonical({'id': identity, 'policy_sha256': sha(CONFIG)}) + b'\n')
            print('C EXECUTOR CACHED:', identity[:16], flush=True)
            return
    wheel = CACHE / spec['wheel']
    if not wheel.exists():
        print('Fetching pinned Unicorn wheel:', spec['version'], flush=True)
        temporary = wheel.with_suffix('.download')
        try:
            with urllib.request.urlopen(spec['url'], timeout=60) as source, temporary.open('wb') as output:
                size = 0
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > 64 * 1024**2:
                        raise ValueError('Executor wheel exceeds download limit')
                    output.write(block)
            if sha(temporary) != spec['sha256']:
                raise ValueError('Executor wheel digest mismatch')
            temporary.replace(wheel)
        finally:
            temporary.unlink(missing_ok=True)
    if sha(wheel) != spec['sha256']:
        raise ValueError('Cached executor wheel is corrupt')
    with tempfile.TemporaryDirectory(prefix='seal-', dir=CACHE) as directory:
        directory = Path(directory)
        addon = directory / 'modules'
        addon.mkdir()
        files = {}
        with zipfile.ZipFile(wheel) as archive:
            for info in archive.infolist():
                name = str(relative(info.filename.rstrip('/')))
                if info.is_dir() or not name.startswith('unicorn/'):
                    continue
                if stat.S_ISLNK(info.external_attr >> 16) or name in files:
                    raise ValueError('Invalid executor wheel member')
                target = addon / name
                target.parent.mkdir(parents=True, exist_ok=True)
                raw = archive.read(info)
                target.write_bytes(raw)
                target.chmod(0o444)
                os.utime(target, (0, 0))
                files[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        base = load('c')
        existing = {Path(name).name for name, value in base['manifest']['files'].items() if value['elf']}
        linked = subprocess.run(['/usr/bin/ldd', str(addon / 'unicorn/lib/libunicorn.so.2')],
                                env={'PATH': '/usr/bin:/bin', 'LANG': 'C'}, capture_output=True, text=True, timeout=30)
        if linked.returncode or 'not found' in linked.stdout:
            raise ValueError('Pinned emulator has unresolved native dependencies')
        for line in linked.stdout.splitlines():
            match = re.search(r'(?:=>\s*)?(/[^\n]+?)\s+\(0x[0-9a-fA-F]+\)', line)
            if not match:
                continue
            dependency = Path(match[1].strip()).resolve()
            if dependency.name in existing:
                continue
            if not dependency.is_relative_to('/usr/lib'):
                raise ValueError('Emulator dependency escapes reviewed system roots')
            name = 'dependencies/' + dependency.name
            target = addon / name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(dependency, target)
            target.chmod(0o444)
            os.utime(target, (0, 0))
            files[name] = {'sha256': sha(target), 'bytes': target.stat().st_size}
        manifest = {'schema_version': 3, 'base_image': base['id'],
                    'engine': {key: spec[key] for key in ('name', 'version')}, 'files': files}
        identity = hashlib.sha256(canonical(manifest)).hexdigest()
        destination = CACHE / identity
        metadata = CACHE / (identity + '.json')
        if not destination.exists():
            addon.rename(destination)
        if metadata.exists() and json.loads(metadata.read_bytes()) != manifest:
            raise ValueError('Executor module manifest changed')
        metadata.write_bytes(canonical(manifest) + b'\n')
        provenance = CACHE / 'provenance' / identity / (spec['sha256'] + '.json')
        provenance.parent.mkdir(parents=True, exist_ok=True)
        provenance.write_bytes(canonical({'engine_id': identity, 'wheel_sha256': spec['sha256']}) + b'\n')
        active = {'id': identity, 'policy_sha256': hashlib.sha256(CONFIG.read_bytes()).hexdigest()}
        (CACHE / 'active.json').write_bytes(canonical(active) + b'\n')
    print('PINNED C EXECUTOR:', identity[:16], len(files), 'module files; no model calls', flush=True)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT / 'benchmarks/scripts'))
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true')
    prepare(parser.parse_args().refresh)
    from grading.c_executor.evidence import save_pins
    save_pins()
