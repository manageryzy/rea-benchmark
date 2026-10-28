"""Batch-local startup admission. No persistent receipts or inference keys.

The explicit startup_once policy trusts admitted inputs during serial planning.
All memoized state is discarded before dispatch, on exceptions, and on scope exit.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import json

from read_session import startup_preparation, mutable_preparation, semantic_context

_BATCH = ContextVar('benchmark_preparation_batch', default=None)


def key(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


@contextmanager
def scope(enabled=False):
    if not enabled or _BATCH.get() is not None:
        yield
        return
    token = _BATCH.set({'values': {}, 'tasks': {}, 'context_ids': set()})
    try:
        with startup_preparation(True):
            yield
    finally:
        _BATCH.reset(token)


def once(namespace, identity, produce):
    batch = _BATCH.get()
    if batch is None:
        return produce()
    cache = batch['values']
    try:
        selected = (namespace, key([identity, semantic_context()]))
    except (TypeError, ValueError):
        # Unknown control contexts must never inherit another context's pass.
        return produce()
    if selected not in cache:
        cache[selected] = copy.deepcopy(produce())  # Exceptions are never passes.
    return copy.deepcopy(cache[selected])


@contextmanager
def input_writes():
    """A cold build is a new input boundary, not a trusted negative cache hit."""
    batch = _BATCH.get()
    if batch is not None:
        batch['values'].clear()
        batch['tasks'].clear()
    try:
        with mutable_preparation():
            yield
    finally:
        if batch is not None:
            batch['values'].clear()
            batch['tasks'].clear()


def task_key(entry, model):
    return key([entry['case'], model, entry.get('configuration_sha256'),
                entry.get('evidence_access', 'binary'),
                entry.get('capability_case', {}).get('public_package_sha256')])


def remember_task(entry, model, task):
    batch = _BATCH.get()
    if batch is not None:
        batch['tasks'][task_key(entry, model)] = copy.deepcopy(task)


def resolved_task(entry, model):
    batch = _BATCH.get()
    task = batch['tasks'].get(task_key(entry, model)) if batch is not None else None
    if task is None:
        return None
    if (entry.get('resolved_fingerprint') != task['fingerprint']
            or entry['directory'] != task['directory']):
        raise ValueError('Prepared task route or action changed before binding')
    return copy.deepcopy(task)


def declare_context(task_ids):
    batch = _BATCH.get()
    if batch is not None:
        batch['context_ids'].update(task_ids)


def require_context(runtime=None, *, task_ids=None):
    from capabilities import analysis_context
    batch = _BATCH.get()
    if batch is None or not task_ids or not set(task_ids) <= batch['context_ids']:
        return analysis_context.require_validation(runtime, task_ids=task_ids)
    # One real catalogue admission, then one real closure comparison per harness.
    ids = sorted(batch['context_ids'])
    return once('context', [runtime, ids],
                lambda: analysis_context.require_validation(runtime, task_ids=set(ids)))


def scoring(task, produce):
    # Complete input contract consumed by capabilities.store.fixed_inputs/spec.
    # Model, prompt and request limits do not affect candidate-independent grading.
    fields = ('track', 'artifact_contract_version', 'grading_spec', 'grading_spec_sha256',
              'kind', 'binary_sha256', 'artifact_schema', 'capability_ids',
              'family_id', 'build_id', 'output_language', 'original_compiler')
    return once('scoring', {name: task.get(name) for name in fields}, produce)


def inference_files(identity):
    """Exact verified adapter bytes shared by tasks with the same profile."""
    if _BATCH.get() is None:
        return None
    import cache
    return once('inference-files', [identity['environment'], identity['condition']],
                lambda: cache.profile_bytes(identity))
