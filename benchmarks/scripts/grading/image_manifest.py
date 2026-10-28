"""Semantic admission of pinned grading manifests, separate from image storage."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'benchmarks/config/grading-environments.json'
CACHE = ROOT / '.cache/benchmark-grading/v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def file_sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()

def relative(name):
    if not isinstance(name, str):
        raise ValueError('Invalid grading-image path')
    path = PurePosixPath(name)
    if not name or name == '.' or path.is_absolute() or '..' in path.parts or str(path) != name or '\\' in name:
        raise ValueError('Invalid grading-image path')
    return path

def policy(language):
    value = json.loads(CONFIG.read_bytes())
    if value['schema_version'] != 1 or value['platform'] != 'linux-x86_64' or platform.machine() != 'x86_64' or sys.platform != 'linux':
        raise ValueError('Unsupported grading toolchain platform')
    return {key: item for key, item in value.items() if key != 'languages'} | {'language': language, 'compiler': value['languages'][language]}

def load(language, *, cache=CACHE):
    cache = Path(cache)
    index = cache / 'active.json'
    if not index.exists():
        raise RuntimeError('Grading image is not prepared; run make setup-grading')
    active = json.loads(index.read_bytes()).get(language)
    if not isinstance(active, dict) or active.get('policy_sha256') != digest(policy(language)):
        raise RuntimeError('Grading image policy changed; run make setup-grading')
    if not isinstance(active.get('id'), str) or not re.fullmatch('[0-9a-f]{64}', active['id']):
        raise ValueError('Invalid active grading image identity')
    record = json.loads((cache / 'profiles' / (active['id'] + '.json')).read_bytes())
    if record['id'] != active['id'] or digest(record['manifest']) != record['id']:
        raise ValueError('Grading image manifest is corrupt')
    validate_record(record, language)
    return record

def validate_record(record, language):
    """Validate metadata before any cache-derived path reaches the executor."""
    if not isinstance(record.get('archive_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', record['archive_sha256']):
        raise ValueError('Invalid grading archive identity')
    manifest = record['manifest']
    if manifest.get('schema_version') != 1 or manifest.get('language') != language or manifest.get('policy_sha256') != digest(policy(language)):
        raise ValueError('Grading image does not match the selected language policy')
    files = manifest['files']
    if not isinstance(files, dict) or not files or record.get('files') != len(files):
        raise ValueError('Invalid grading image file inventory')
    total = 0
    for name, info in files.items():
        relative(name)
        if (not isinstance(info.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', info['sha256'])
                or type(info.get('bytes')) is not int or info['bytes'] < 0
                or info.get('mode') not in (0o444, 0o555) or type(info.get('elf')) is not bool):
            raise ValueError('Invalid grading image file descriptor')
        total += info['bytes']
    if record.get('bytes') != total:
        raise ValueError('Invalid grading image byte total')
    directories = manifest['directories']
    if not isinstance(directories, list) or len(set(directories)) != len(directories):
        raise ValueError('Invalid grading image directories')
    for name in directories:
        relative(name)
        if name in files:
            raise ValueError('Conflicting grading image directory')
    for name in files:
        if any(str(parent) in files for parent in PurePosixPath(name).parents):
            raise ValueError('Grading image file is a parent of another file')
    loader = str(relative(manifest['loader']))
    if loader not in files or not files[loader]['elf'] or not re.fullmatch('ld-linux[^/]*\\.so(?:\\.[0-9]+)*', PurePosixPath(loader).name):
        raise ValueError('Invalid pinned ELF loader')
    for name, target in manifest.get('root_aliases', {}).items():
        target = relative(target)
        if name not in ('lib', 'lib64') or target.parts[0] != 'usr':
            raise ValueError('Invalid grading root alias')
    compiler = str(relative(manifest['compiler']['command'].removeprefix('/')))
    if compiler not in files or files[compiler]['sha256'] != manifest['compiler']['executable_sha256']:
        raise ValueError('Grading compiler does not match its image')
    if policy(language)['compiler']['version'] not in manifest['compiler']['version'].splitlines()[0]:
        raise ValueError('Grading compiler version differs from policy')
