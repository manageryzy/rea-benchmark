"""Action-addressed native attempts; old observations are never overwritten."""
import json
from pathlib import Path

import action_cache
import cache
from capabilities.attempts import _task, status


def route(entry, fingerprint, *, root=None):
    if 'inference_trial' not in entry:
        return entry['directory']
    root = Path(root or action_cache.ROOT)
    trial = str(action_cache.relative(entry['inference_trial']))
    action_cache.valid_digest({'hash': fingerprint, 'size_bytes': 0})
    legacy = root / trial
    addressed = legacy / 'actions' / fingerprint
    for directory in (legacy, addressed):
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError('Native attempt escapes its workspace')
    saved = _task(legacy, root)
    if saved.get('fingerprint') == fingerprint:
        if cache.inference_trial(saved) != trial:
            raise ValueError('Native logical trial changed')
        return trial
    if status(legacy) != 'not_started' and not saved:
        raise ValueError('Native legacy attempt lacks identity; preserve without retry')
    saved = _task(addressed, root)
    if saved and (saved['fingerprint'] != fingerprint or cache.inference_trial(saved) != trial):
        raise ValueError('Native addressed attempt identity changed')
    return str(addressed.relative_to(root))


def select(entry, task, *, root=None):
    """Only a selection pointer changes; task/result files remain immutable."""
    root = Path(root or action_cache.ROOT)
    if route(entry, task['fingerprint'], root=root) != task['directory']:
        raise ValueError('Native selection differs from resolved action')
    import run
    run.save(root / entry['inference_trial'] / 'selected-attempt.json', {
        'schema_version': 1, 'inference_trial': entry['inference_trial'],
        'directory': task['directory'], 'fingerprint': task['fingerprint']})


def report_directories(legacy, *, root=None, read=None):
    """One selected row plus real historical attempts, without duplicate scores."""
    root = Path(root or action_cache.ROOT)
    read = read or (lambda p, default=None: json.loads(p.read_bytes()) if p.exists() else default)
    trial = str(legacy.relative_to(root))
    pointer = read(legacy / 'selected-attempt.json', {})
    selected = legacy
    if pointer:
        key = pointer.get('fingerprint')
        action_cache.valid_digest({'hash': key, 'size_bytes': 0})
        if pointer.get('schema_version') != 1 or pointer.get('inference_trial') != trial:
            raise ValueError('Invalid native selected-attempt pointer')
        selected = root / str(action_cache.relative(pointer['directory']))
        if selected not in (legacy, legacy / 'actions' / key):
            raise ValueError('Native selected-attempt pointer escapes its cell')
    historical = [legacy] if (legacy / 'task.json').is_file() else []
    historical += [p.parent for p in sorted((legacy / 'actions').glob('*/task.json'))]
    rows = []
    for directory in dict.fromkeys([selected, *historical]):
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError('Native report attempt escapes workspace')
        task = read(directory / 'task.json', {})
        if task and (task.get('directory') != str(directory.relative_to(root))
                     or cache.inference_trial(task) != trial):
            raise ValueError('Native report attempt identity differs')
        if directory != legacy and task and task.get('fingerprint') != directory.name:
            raise ValueError('Native report action directory differs')
        if directory == selected and task and pointer and task.get('fingerprint') != pointer['fingerprint']:
            raise ValueError('Native selection fingerprint differs')
        rows.append((directory, directory == selected, task.get('fingerprint')))
    return rows
