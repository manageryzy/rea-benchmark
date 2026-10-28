"""Append-only grading records; raw inference and original grades stay immutable."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading

from . import native, returns
import action_cache

BENCH = Path(__file__).resolve().parents[2]
VERSION = 'exact-returns-v3'
_lock = threading.Lock()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def vectors(track):
    if track in ('basic', 'complex'):
        path = BENCH / ('suites/record-parser/grading/vectors.json' if track == 'basic' else 'suites/complex-native-v1/private/vectors.json')
        return load(path), sha(path)
    spec = importlib.util.spec_from_file_location('frozen_native_vectors', BENCH / 'suites/stl-native-v2/scripts/grade-framed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.frozen_cases(track)


def source(directory, language):
    directory = Path(directory)
    if load(directory / 'request-budget.json', {}).get('status') == 'deadline_exceeded':
        import request_contract
        receipt = load(directory / 'request-budget.json')
        if not request_contract.deadline_grade_allowed(receipt, receipt.get('contract', {})):
            raise ValueError('Invalid deadline receipt')
        return None, None
    extension = {'c': 'c', 'cpp': 'cpp', 'rust': 'rs', 'go': 'go'}[language]
    saved = directory / ('reconstructed.' + extension)
    original = load(directory / 'grade.json', {})
    if saved.exists():
        if original.get('candidate_source_sha256') and sha(saved) != original['candidate_source_sha256']:
            raise ValueError('Saved candidate differs from original grade')
        return saved.read_bytes().decode('utf-8'), saved.name
    answer = directory / 'answer.md'
    if not answer.exists():
        raise ValueError('Saved answer missing; offline grading cannot recover it by inference')
    blocks = re.findall(r'^```([^\n]*)\n(.*?)^```\s*$', answer.read_text(), re.M | re.S)
    fence = {'c': 'c', 'cpp': r'cpp|c\+\+', 'rust': 'rust|rs', 'go': 'go|golang'}[language]
    matching = [code for tag, code in blocks if re.fullmatch(fence, tag.strip(), re.I)]
    return (matching[0] if len(blocks) == len(matching) == 1 else None), None


def identity(directory, task):
    language = task.get('output_language') or task['language']
    track = task['track']
    code, name = source(directory, language)
    cases, vector_hash = vectors(track)
    original = load(Path(directory) / 'grade.json', {})
    if original.get('vectors_sha256') and original['vectors_sha256'] != vector_hash:
        raise ValueError('Frozen vectors differ from original grade; locate the original vectors before regrading')
    standard = task.get('standard') or ('c++20' if language == 'cpp' else '2021')
    standard = action_cache.grading_standard(language, standard)
    files = [BENCH.parent / name for name in action_cache.dependencies('grading', language=language)]
    trial = str(Path(directory).resolve())
    if Path(trial).is_relative_to(BENCH.parent):
        trial = str(Path(trial).relative_to(BENCH.parent))
    value = {'trial': trial, 'version': VERSION, 'protocol': returns.PROTOCOL, 'language': language, 'track': track,
             'candidate_source_sha256': hashlib.sha256(code.encode()).hexdigest() if code is not None else None,
             'answer_sha256': sha(Path(directory) / 'answer.md') if (Path(directory) / 'answer.md').exists() else None,
             'vectors_sha256': vector_hash, 'standard': standard, 'string_abi': task.get('string_abi'),
             'compiler': native.compiler_identity(language),
             'files_sha256': {str(path.relative_to(BENCH)): sha(path) for path in files},
             'limits': {'compile_seconds': 120, 'execute_seconds': 20, 'stdout_stderr_bytes_each': 4 * 1024**2}}
    if load(Path(directory) / 'request-budget.json', {}).get('status') == 'deadline_exceeded':
        value['deadline_receipt_sha256'] = sha(Path(directory) / 'request-budget.json')
        value['deadline_policy_sources'] = {p: sha(BENCH / p) for p in ('scripts/request_contract.py', 'scripts/grading/store.py')}
    return value, code, name, cases


def action(directory, task):
    value, code, _, cases = identity(directory, task)
    return action_cache.grading(value, code, cases)


def compatible_legacy(previous, current):
    """Import only byte-verified observations of the same semantic grading action."""
    keys = ['version', 'protocol', 'language', 'track', 'candidate_source_sha256',
            'vectors_sha256', 'string_abi', 'compiler', 'limits']
    standard_same = action_cache.grading_standard(previous['language'], previous['standard']) == action_cache.grading_standard(current['language'], current['standard'])
    return standard_same and all(previous.get(key) == current.get(key) for key in keys) and all(
        previous.get('files_sha256', {}).get(name) == value
        for name, value in current['files_sha256'].items())


def read_selected(directory, *, read=load, file_sha=sha):
    directory = Path(directory).resolve()
    selection = read(directory / 'selected-grade.json')
    if not selection:
        return None, None
    relative = selection['path']
    relative_path = action_cache.relative(relative)
    unresolved = directory.joinpath(*relative_path.parts)
    path = unresolved.parent.resolve() / unresolved.name
    root = directory / 'grades'
    if not path.is_relative_to(root) or path.is_symlink() or file_sha(path) != selection['sha256']:
        raise ValueError('Selected grading record missing or changed')
    manifest = read(path.parent / 'manifest.json')
    key = action_cache.digest(action_cache.canonical(manifest['action']))['hash'] if manifest.get('cache_schema') == 3 else digest(manifest['identity'])
    if manifest['grading_id'] != selection['grading_id'] or key != selection['grading_id']:
        raise ValueError('Invalid grading identity')
    parents = {path.parent: path.parent}
    for name, expected in manifest['files_sha256'].items():
        original = path.parent.joinpath(*action_cache.relative(name).parts)
        if original.parent not in parents:
            parents[original.parent] = original.parent.resolve()
        artifact = parents[original.parent] / original.name
        if not artifact.is_relative_to(path.parent) or artifact.is_symlink() or file_sha(artifact) != expected:
            raise ValueError('Grading artifact missing or changed')
    source_name = manifest.get('original_source')
    if source_name and (str(action_cache.relative(source_name)) != Path(source_name).name):
        raise ValueError('Invalid original source path')
    if source_name and file_sha(directory / source_name) != manifest['identity']['candidate_source_sha256']:
        raise ValueError('Saved candidate changed since regrading')
    answer = manifest['identity'].get('answer_sha256')
    if answer and file_sha(directory / 'answer.md') != answer:
        raise ValueError('Saved answer changed since regrading')
    return read(path), relative


def publish(work, destination, manifest):
    """Commit verified files without renaming an open/scanned directory on DrvFS.

    grade.json is the completion marker and is linked last. A partial publication
    can be resumed only when every existing file matches the same cached bytes.
    The caller holds the per-action lock. No existing artifact is overwritten.
    """
    work, destination = Path(work), Path(destination)
    # Read and verify before touching the destination; a prematurely deleted
    # staging directory stays an explicit FileNotFoundError, not a cache hit.
    root = work.resolve(strict=True)
    payloads = {}
    for name, expected in manifest['files_sha256'].items():
        path = work.joinpath(*action_cache.relative(name).parts)
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError('Grading publication source escaped staging directory')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Grading publication source changed')
        payloads[name] = (raw, bool(path.stat().st_mode & 0o111))
    if 'grade.json' not in payloads or 'manifest.json' in payloads:
        raise ValueError('Invalid grading publication file list')
    payloads['manifest.json'] = ((json.dumps(manifest, indent=2)+'\n').encode(), False)
    if destination.is_symlink():
        raise ValueError('Grading publication destination is a symlink')
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve(strict=True)
    original = destination.stat()
    directory_identity = (original.st_dev, original.st_ino)
    for path in destination.rglob('*'):
        if path.is_symlink() or (path.is_file() and str(path.relative_to(destination)) not in payloads):
            raise ValueError('Unexpected artifact in partial grading publication')
    # Preflight all existing files before publishing more, including the marker.
    for name, (raw, executable) in payloads.items():
        path = destination.joinpath(*action_cache.relative(name).parts)
        if path.is_symlink() or not path.resolve().is_relative_to(destination_root):
            raise ValueError('Grading publication destination escaped record directory')
        if path.exists() and (not path.is_file() or path.read_bytes() != raw
                              or bool(path.stat().st_mode & 0o111) != executable):
            raise ValueError('Conflicting partial grading publication')
    for name in [n for n in payloads if n != 'grade.json'] + ['grade.json']:
        current = destination.stat()
        if destination.is_symlink() or (current.st_dev, current.st_ino) != directory_identity:
            raise ValueError('Grading publication directory was replaced')
        raw, executable = payloads[name]
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == 'grade.json':
            for other, (data, mode) in payloads.items():
                if other == 'grade.json':
                    continue
                artifact = destination / other
                if (artifact.is_symlink() or not artifact.resolve(strict=True).is_relative_to(destination_root)
                        or artifact.read_bytes() != data or bool(artifact.stat().st_mode & 0o111) != mode):
                    raise ValueError('Grading publication artifact changed before commit')
        if target.exists():
            continue
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.publish-', dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o755 if executable else 0o644)
            # Atomic no-replace publication; works across staging filesystems
            # because this temporary file is on the destination filesystem.
            os.link(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def grade(directory, task, *, select=True):
    directory = Path(directory)
    if (directory / 'harness-failure.json').exists() or (directory / 'interrupted.json').exists():
        raise RuntimeError('Environment/interrupted attempt cannot receive a model correctness grade')
    value, code, name, cases = identity(directory, task)
    action = action_cache.grading(value, code, cases)
    grading_id = action.key
    base = directory / 'grades' / VERSION
    destination = base / grading_id
    # One process-level lock also avoids duplicate compiler work in threaded batches.
    with _lock:
        base.mkdir(parents=True, exist_ok=True)
        lock = base / (grading_id + '.lock')
        descriptor = None
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            if not (destination / 'grade.json').exists():
                with tempfile.TemporaryDirectory(prefix='rea-grade-publish-') as temporary:
                    work = Path(temporary)
                    cache = action_cache.Cache()
                    hit = cache.lookup(action)
                    if hit:
                        cache.restore(hit, work)
                    else:
                        previous, relative = read_selected(directory)
                        legacy = load((directory / relative).parent / 'manifest.json') if relative else None
                        if legacy and compatible_legacy(legacy['identity'], value):
                            old = (directory / relative).parent
                            for artifact in legacy['files_sha256']:
                                target = work / artifact
                                target.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copyfile(old / artifact, target)
                            result = dict(previous)
                        elif code is None:
                            result = {'compiled': False, 'passed': 0, 'total': len(cases), 'program_success': False,
                                      'failure': ('Deadline exceeded; no complete submission before the time limit' if value.get('deadline_receipt_sha256') else 'Expected single native code block'), 'points': {}, 'categories': {},
                                      'active_point_count': 1 if task['track'] == 'basic' else 6 if task['track'] != 'stl' else 21 if value['standard'] == 'c++11' else 22,
                                      'active_points_passed': 0, 'candidate_modified': False,
                                      'grading_protocol': returns.PROTOCOL, 'return_provenance': 'native_process_unauthenticated'}
                        else:
                            result = native.evaluate(code, value['language'], cases, value['track'], value['standard'], value['string_abi'], work / 'artifacts')
                            if result.get('compiler_identity') != value['compiler']:
                                raise RuntimeError('Grading runtime changed after action preparation')
                            observed = result.get('grading_runtime', {}).get('source_inputs_sha256', {})
                            if any(observed.get('benchmarks/' + name) != expected for name, expected in value['files_sha256'].items()):
                                raise RuntimeError('Executed grading sources differ from the action inputs')
                        result.update(grading_id=grading_id, grading_version=VERSION, vectors_sha256=value['vectors_sha256'])
                        (work / 'grade.json').write_text(json.dumps(result, indent=2) + '\n')
                        cache.capture(action, work)
                    manifest = {'cache_schema': 3, 'action': action.descriptor,
                                'grading_id': grading_id, 'identity': value, 'original_source': name,
                                'files_sha256': {str(path.relative_to(work)): sha(path) for path in work.rglob('*') if path.is_file()}}
                    publish(work, destination, manifest)
            manifest = load(destination / 'manifest.json')
            if manifest.get('action') != action.descriptor:
                raise ValueError('Grading cache identity mismatch')
            # Verify before selecting even when another process produced this cache.
            for artifact, expected in manifest['files_sha256'].items():
                if sha(destination / artifact) != expected:
                    raise ValueError('Grading cache artifact changed')
            if select:
                selection = {'grading_id': grading_id, 'path': str((destination / 'grade.json').relative_to(directory)),
                             'sha256': sha(destination / 'grade.json'), 'policy': VERSION}
                temporary_index = directory / ('selected-grade.tmp-' + str(os.getpid()))
                temporary_index.write_text(json.dumps(selection, indent=2) + '\n')
                temporary_index.replace(directory / 'selected-grade.json')
            return load(destination / 'grade.json')
        except FileExistsError as error:
            raise RuntimeError('Another process owns this grading identity; duplicate regrade refused') from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
                lock.unlink()


def grade_entry(directory, language=None, track='basic'):
    """Compatibility API for existing suite graders; originals are never replaced."""
    directory = Path(directory).resolve()
    receipt = load(directory / 'result.json', {})
    task = load(directory / 'task.json', {})
    language = language or receipt.get('output_language', 'c')
    task = {**task, 'track': track, 'language': language, 'output_language': language,
            'standard': task.get('standard') or receipt.get('standard') or ('c++20' if language == 'cpp' else '2021'),
            'string_abi': task.get('string_abi', receipt.get('string_abi'))}
    result = grade(directory, task)
    code, name = source(directory, language)
    if code is not None and name is None:
        extension = {'c': 'c', 'cpp': 'cpp', 'rust': 'rs', 'go': 'go'}[language]
        with (directory / ('reconstructed.' + extension)).open('x') as stream:
            stream.write(code)
    if not (directory / 'grade.json').exists():
        with (directory / 'grade.json').open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    return result
