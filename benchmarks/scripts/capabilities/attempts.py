"""Immutable physical attempts with a stable, backward-compatible logical trial."""
from pathlib import Path
import json
import hashlib

import action_cache
import cache


def authorized_retry(value, cell=None, *, root=None):
    """Bind an explicit post-top-up retry to an immutable HTTP 402 attempt.

    This is a distinct, declared measurement attempt, not a changed prompt or
    environment condition. A stable authorization enters the inference action
    so rerunning the same configuration cannot silently retry it again.
    """
    root = action_cache.ROOT if root is None else Path(root)
    fields = {'policy', 'authorization', 'cell', 'previous_directory', 'previous_fingerprint',
              'files_sha256'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Invalid explicit retry authorization')
    if (value['policy'] != 'user-authorized-http402-retry-v1'
            or not isinstance(value['authorization'], str) or not value['authorization'].strip()
            or len(value['authorization']) > 500
            or not isinstance(value['cell'], list) or len(value['cell']) != 3
            or any(not isinstance(part, str) or not part for part in value['cell'])
            or value['cell'][1] != 'capabilities-v1'
            or cell is not None and list(cell) != value['cell']):
        raise ValueError('Retry authorization differs from selected cell')
    relative = action_cache.relative(value['previous_directory'])
    directory = root / relative
    if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError('Retry predecessor escapes the evidence root')
    hashes = value['files_sha256']
    required = {'task.json', 'result.json', 'request-budget.json', 'harness-failure.json'}
    if not isinstance(hashes, dict) or set(hashes) != required:
        raise ValueError('Retry authorization needs all predecessor receipts')
    documents = {}
    for name, digest in hashes.items():
        path = directory / name
        raw = path.read_bytes()
        if path.is_symlink() or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Authorized predecessor receipt changed: ' + name)
        documents[name] = json.loads(raw)
    task = _task(directory, root)
    budget, result = documents['request-budget.json'], documents['result.json']
    forwarded = [r for r in budget.get('requests', []) if r.get('forwarded')]
    last = forwarded[-1] if forwarded else {}
    if (task.get('model') != value['cell'][0] or task.get('id') != value['cell'][2]
            or task.get('fingerprint') != value['previous_fingerprint']
            or task.get('inference_identity', {}).get('authorized_retry')
            or result.get('model') != value['cell'][0] or result.get('case_id') != value['cell'][2]
            or result.get('exit_code') == 0
            or last.get('http_status') != 402
            or budget.get('budget_exceeded') or budget.get('status') == 'budget_exhausted'
            or any((directory / name).exists() for name in
                   ('completed.json', 'selected-grade.json', 'budget-exhausted.json', 'deadline-exceeded.json'))):
        raise ValueError('Only the bound, ungraded HTTP 402 failure can be retried')
    return json.loads(json.dumps(value))


def _task(directory, root):
    path = directory / 'task.json'
    if path.is_symlink():
        raise ValueError('Attempt task must not be a symlink')
    if not path.exists():
        return {}
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get('directory') != str(directory.relative_to(root))
            or not isinstance(value.get('fingerprint'), str) or len(value['fingerprint']) != 64
            or any(c not in '0123456789abcdef' for c in value['fingerprint'])):
        raise ValueError('Attempt task has an inconsistent directory')
    return value


def status(directory):
    if (directory / 'completed.json').exists() and (directory / 'result.json').exists():
        return 'cached'
    if (directory / 'result.json').exists():
        return 'saved_inference; offline_grading_pending'
    if any((directory / name).exists() for name in (
            'task.json', 'events.jsonl', 'zcode-events.jsonl', 'harness-failure.json', 'interrupted.json')):
        return 'interrupted; duplicate_inference_refused'
    return 'not_started'


def route(entry, fingerprint, *, root=None):
    """Keep a matching legacy attempt; otherwise use a sibling named by action.

    Never infer reuse from a status file, copy/rename an old observation, or put
    a random staging path into the action. The normal cache validator still
    authenticates every saved output before admitting a hit.
    """
    if 'inference_trial' not in entry:
        return entry['directory']  # Historical/manual offline callers.
    root = action_cache.ROOT if root is None else Path(root)
    trial = str(action_cache.relative(entry['inference_trial']))
    if len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise ValueError('Invalid inference action identity')
    legacy = root / trial
    addressed = legacy.parent / fingerprint
    if 'authorized_retry' in entry:
        # The same explicit authorization is consumed even if a later dynamic
        # budget baseline or runtime revision would otherwise make a new key.
        for path in legacy.parent.glob('*/task.json'):
            previous = _task(path.parent, root)
            if previous.get('inference_identity', {}).get('authorized_retry') == entry['authorized_retry']:
                if previous['fingerprint'] != fingerprint:
                    raise ValueError('Explicit retry was already attempted; preserve without another launch')
    for directory in dict.fromkeys((legacy, addressed)):
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError('Attempt directory must not be a symlink')
    saved = _task(legacy, root)
    if saved.get('fingerprint') == fingerprint:
        # A legacy task must prove that this was its logical trial, too.
        if cache.inference_trial(saved) != trial:
            raise ValueError('Legacy inference trial differs from its recorded action')
        return trial
    if status(legacy) != 'not_started' and not saved:
        raise ValueError('Legacy attempt lacks task identity; preserve without duplicate inference')
    saved = _task(addressed, root)
    if saved and (saved.get('fingerprint') != fingerprint or cache.inference_trial(saved) != trial):
        raise ValueError('Action-addressed attempt identity differs from its directory')
    return str(addressed.relative_to(root))
