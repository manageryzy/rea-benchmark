#!/usr/bin/python3
"""One coordinator for existing suites and interchangeable harness adapters."""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import threading
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import copy_context

import records
import environment_profile
import benchmark_config
import cache
import action_cache
import runtime_attestation
import startup_policy
from grading.metadata_admission import metadata_validation_cache

ROOT, BENCH = records.ROOT, records.BENCH
_UNSET = object()


class PreservedAttempt(RuntimeError):
    """A prior terminal attempt is retained without retry or correctness grading."""


class CaseTransportTimeout(RuntimeError):
    """A saved, ungraded stream-timeout attempt; never retry the same case."""


def preserved_stream_timeout(directory, task, model):
    """Recognize one audited ZCode timeout pattern without relaxing API gates.

    Unknown failures, quota/auth errors and ambiguous or incomplete evidence
    keep the queue stopped. This changes scheduling only, not model inputs.
    """
    if model.get('harness') != 'zcode':
        return False
    try:
        def read(name):
            return json.loads((directory / name).read_bytes())
        result, budget, failure = (read(name) for name in
            ('result.json', 'request-budget.json', 'harness-failure.json'))
        import request_contract
        if (failure.get('classification') != 'harness_or_provider_failure'
                or failure.get('model_correctness_grade_allowed') is not False
                or result.get('model') != model['id']
                or result.get('case_id') != task['id']
                or result.get('exit_code') in (None, 0)
                or result.get('timed_out') is not False
                or result.get('upstream_error') is not None
                or result.get('budget_exceeded') is not False
                or result.get('usage_complete') is not False
                or budget.get('contract') != request_contract.from_task(task)
                or budget.get('contract_sha256') != request_contract.digest(budget['contract'])
                or result.get('request_budget') != budget
                or budget.get('status') != 'rejected'
                or budget.get('transport_complete') is not False):
            return False
        requests = budget['requests']
        forwarded = [row for row in requests if row.get('forwarded') is True]
        rejected = [row for row in requests if row.get('forwarded') is not True]
        if (not forwarded or budget.get('provider_forwards') != len(forwarded)
                or budget.get('rejected_requests') != 1 or len(rejected) != 1
                or requests[:-1] != forwarded or requests[-1] != rejected[0]
                or rejected[0].get('forwarded') is not False
                or rejected[0].get('rejected') != 'concurrent_provider_requests_denied'
                or any(row.get('http_status') != 200 for row in forwarded)
                or any(row.get('response_complete') is not True for row in forwarded[:-1])
                or forwarded[-1].get('response_complete') is True):
            return False
        timeout_seen, terminal_seen = False, False
        with (directory / 'zcode-events.jsonl').open() as events:
            for line in events:
                event = json.loads(line)
                payload = event.get('payload', {})
                if (event.get('type') == 'streamRecovery.updated'
                        and payload.get('failureKind') == 'provider_timeout'
                        and payload.get('retryNumber') == 1):
                    timeout_seen = True
                if event.get('type') == 'turn.failed':
                    terminal_seen = (timeout_seen and payload.get('error', {}).get(
                        'underlyingErrorMessage') == 'concurrent_provider_requests_denied')
        return timeout_seen and terminal_seen
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


class BatchTimings:
    """Operational wall times, deliberately outside inference and cache inputs."""
    def __init__(self):
        self.started = time.perf_counter()
        self.stages = []

    @contextmanager
    def stage(self, name):
        started = time.perf_counter()
        row = {'stage': name, 'status': 'running', 'elapsed_seconds': None}
        self.stages.append(row)
        print('STAGE:', name, 'started', flush=True)
        try:
            yield
        except BaseException as error:
            row['status'] = 'interrupted' if isinstance(error, (KeyboardInterrupt, InterruptedError)) else 'failed'
            raise
        else:
            row['status'] = 'complete'
        finally:
            row['elapsed_seconds'] = round(time.perf_counter() - started, 6)
            print('STAGE:', name, row['status'], '|', row['elapsed_seconds'], 'seconds', flush=True)

    def skip(self, name, reason):
        self.stages.append({'stage': name, 'status': 'skipped', 'elapsed_seconds': 0, 'reason': reason})
        print('STAGE:', name, 'skipped |', reason, flush=True)

    def snapshot(self):
        return {'schema_version': 1, 'total_seconds': round(time.perf_counter() - self.started, 6),
                'stages': [dict(row) for row in self.stages]}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def save(path, value, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open('x') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
    else:
        temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()) + '-' + str(threading.get_ident()))
        temporary.write_text(json.dumps(value, indent=2) + '\n')
        temporary.replace(path)


def _execution_state():
    return records.read(BENCH / 'config/execution.json', {})


def set_execution_stopped(stopped, *, expected_stop_at=_UNSET):
    """Serialize official stop/resume writes and refuse a stale resume epoch."""
    path = BENCH / 'config/execution.json'
    lock = ROOT / '.cache/benchmark-runtime/execution-state.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            state = dict(_execution_state())
            if (not stopped and expected_stop_at is not _UNSET
                    and state.get('last_stop_at') != expected_stop_at):
                raise InterruptedError('Batch stopped before resume; no provider launch')
            state['experiments_stopped'] = stopped
            state['reason'] = 'Explicit batch running' if not stopped else 'Batch ended or interrupted; no automatic restart'
            if stopped:
                state['last_stop_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            save(path, state)
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _execution_allowed(cancel_event):
    """Synchronous dispatch check; a quick stop/resume cannot erase cancellation."""
    if cancel_event is None:
        return
    if cancel_event.is_set():
        raise InterruptedError('Batch cancelled; no new task or provider launch')
    epoch = getattr(cancel_event, 'stop_epoch', _UNSET)
    if epoch is not _UNSET:
        state = _execution_state()
        if state.get('experiments_stopped', True) or state.get('last_stop_at') != epoch:
            cancel_event.set()
            raise InterruptedError('Batch stopped after scheduling; no provider launch')


def plan(suite='all', model=None, experiment_id=None, condition='legacy-recorded', *, deferred=False, inventory=None):
    selected = []
    inventory = {} if inventory is None else inventory
    if 'registry' not in inventory:
        inventory['registry'] = records.registry()
        inventory['conditions'] = records.condition_catalog()
    for experiment in inventory['registry']['experiments']:
        if experiment['role'] != 'primary' or not experiment.get('enabled', True) or (suite != 'all' and experiment['suite'] != suite):
            continue
        if experiment_id and experiment['id'] != experiment_id:
            continue
        effective = inventory['conditions']['active_case_conditions'].get(experiment['suite']) if condition == 'active' else condition
        registered = next(c for c in inventory['conditions']['conditions'] if c['id'] == effective)
        if effective != 'legacy-recorded' and experiment['suite'] not in registered.get('suites', []):
            continue
        if experiment['suite'] in registered.get('retired_suites', []):
            continue
        if experiment['id'] not in inventory:
            inventory[experiment['id']] = records.frozen_cases(experiment)
        for case in inventory[experiment['id']]:
            if not case.get('enabled', True):
                continue
            directory = records.attempt_directory(experiment['id'], case['id'], model, effective) if model else None
            entry = {'experiment': experiment['id'], 'suite': experiment['suite'],
                     'case': case['id'], 'language': case['language'], 'model': model, 'condition': effective,
                     **({'native_case_origin': case['native_case_origin']} if case.get('native_case_origin') else {}),
                     'status': 'unchecked', 'directory': str(directory.relative_to(ROOT)) if directory else None,
                     'cached_environment_profile': None}
            selected.append(entry if deferred else inspect_native_entry(entry))
    return selected


def inspect_native_entry(entry):
    """Read only this queue cell's attempt metadata, then use normal cache admission."""
    if entry.get('status') != 'unchecked' or entry.get('suite') == 'capabilities-v1':
        return entry
    directory = ROOT / entry['directory'] if entry.get('directory') else None
    if directory and (directory / 'grade.json').exists() and (directory / 'result.json').exists():
        status = 'cached'
    elif directory and (directory / 'result.json').exists():
        status = 'saved_inference; offline_grading_pending'
    elif directory and any((directory / name).exists() for name in
                           ('events.jsonl', 'zcode-events.jsonl', 'task.json', 'harness-failure.json', 'interrupted.json')):
        status = 'interrupted; duplicate_inference_refused'
    else:
        status = 'not_started'
    return {**entry, 'status': status, 'cached_environment_profile':
        records.read(directory / 'result.json', {}).get('environment_profile', {}).get('id', 'legacy-recorded') if directory else None}


def paged_tools(raw):
    import preparation_session
    return preparation_session.once('paged-tools', hashlib.sha256(raw).hexdigest(), lambda: _paged_tools(raw))


def _paged_tools(raw):
    """The shared lossless public tool projection for every paged task kind."""
    catalog = json.loads(raw)
    for tool in catalog['tools']:
        tool['outputSchema'] = {'type': 'object', 'additionalProperties': True,
            'description': 'Complete REA Evidence or an explicit lossless paging envelope with a reachable read_evidence reference.'}
    descriptor = subprocess.check_output([str(ROOT/'.tools/node-v24.22.0-linux-x64/bin/node'), '--input-type=module', '-e',
        "import {evidenceTool} from './benchmarks/scripts/evidence-paging.mjs'; process.stdout.write(JSON.stringify(evidenceTool));"], cwd=ROOT)
    catalog['tools'].append(json.loads(descriptor))
    return json.dumps(catalog, sort_keys=True, indent=2).encode()


def prepare_task(entry, model, materialize=True, *, offline_fixture=False, attestor=None):
    if entry.get('suite') == 'capabilities-v1':
        from capabilities.coordinator import prepare
        if attestor is None and entry.get('measurement_registration'):
            from capabilities.registration import Registry
            attestor = Registry().attestor(entry['measurement_registration'])
        elif attestor is None and entry.get('_resolved_attestation'):
            # Resolution already captured this harness for the batch. prepare()
            # still checks the proof against the exact effective configuration;
            # real dispatch retains fresh pre/post runtime verification.
            attestor = lambda model, configuration: entry['_resolved_attestation']
        return prepare(entry, model, materialize=materialize, offline_fixture=offline_fixture, attestor=attestor)
    if attestor is not None:
        raise ValueError('Capability registration cannot override a native inference route')
    experiment = next(x for x in records.registry()['experiments'] if x['id'] == entry['experiment'])
    case = next(x for x in records.frozen_cases(experiment) if x['id'] == entry['case'])
    manifest = records.read(BENCH / 'runs' / experiment['id'] / 'run-manifest.json')
    config = manifest['configuration']
    imported = records.imported_attempt(experiment['id'], case['id'], model['id'])
    if imported:
        source = records.read(ROOT / imported['manifest'])
        frozen = next(x for x in source['cases'] if x['id'] == imported['manifest_case'])
        public = (ROOT / imported['manifest']).parent / 'inputs' / frozen['id']
    else:
        public = records.public_directory(experiment, case)
    prompt_path = next((BENCH / 'runs' / experiment['id'] / m['id'] / case['id'] / 'prompt.txt'
                        for m in config['models']
                        if (BENCH / 'runs' / experiment['id'] / m['id'] / case['id'] / 'prompt.txt').exists()), public / 'prompt.txt')
    prompt, binary = records.read_bytes(prompt_path), records.read_bytes(public / 'target')
    tools_path = ROOT / config.get('analysis_tools_file', 'benchmarks/config/rea-tools.json')
    tools = records.read_bytes(public / 'tools.json') if (public / 'tools.json').exists() else records.read_bytes(tools_path)
    condition = entry.get('condition', 'legacy-recorded')
    paging_path = environment_profile.paging_policy_path(condition)
    paging = records.read(paging_path) if paging_path is not None else None
    if paging:
        tools = paged_tools(tools)
    profile = environment_profile.current(model['harness'], condition)
    manifest_bytes = records.read_bytes(public / 'manifest.json')
    identity = cache.inference_identity(entry, model, case, profile, prompt, binary, tools, manifest_bytes, config)
    effective_gateway = {'max_tool_calls': case['max_tool_calls'], 'tool_timeout_ms': config['tool_timeout_ms'],
        'analysis_tools_file': '@task/tools.json', 'data_limits': config.get('data_limits', {}),
        **({'evidence_paging': paging} if paging else {})}
    effective_harness = {key: config[key] for key in ('schema_version', 'harness_version', 'tool_timeout_ms')}
    effective_harness.update(max_output_tokens=case['max_output_tokens'], analysis_tools_file='@task/tools.json', timeout_seconds=case['timeout_seconds'])
    effective_harness['allowed_tool_names'] = sorted('mcp__rea__' + tool['name'] for tool in json.loads(tools)['tools'])
    if paging:
        effective_harness['evidence_paging'] = paging
    import adaptive_budget
    baseline = None if offline_fixture else adaptive_budget.context(model, experiment['suite'], case, condition, completed=entry.get('_budget_completed', ()))
    if baseline is not None:
        effective_harness['budget_context'] = baseline
    saved_task = records.read(ROOT / entry['directory'] / 'task.json', {})
    if not offline_fixture and (materialize or saved_task.get('runtime_attestation') or 'inference_trial' in entry):
        identity['attestation'] = runtime_attestation.prepare(model, configuration={'gateway': effective_gateway, 'harness': effective_harness})
        effective_harness = identity['attestation'].get('configuration', {}).get('harness', effective_harness)
    trial = cache.inference_trial(entry)
    if 'inference_trial' in entry and trial != str(records.attempt_directory(
            entry['experiment'], entry['case'], model['id'], condition).relative_to(ROOT)):
        raise ValueError('Native logical trial differs from configured cell')
    action = action_cache.inference(identity, {'prompt.txt': prompt, 'target': binary, 'tools.json': tools, 'manifest.json': manifest_bytes}, trial=trial)
    fingerprint = action.key
    import native_attempts
    directory = native_attempts.route(entry, fingerprint, root=ROOT)
    if entry.get('resolved_fingerprint', fingerprint) != fingerprint:
        raise ValueError('Native action changed after resolution')
    if entry.get('resolved_fingerprint') and directory != entry['directory']:
        raise ValueError('Native attempt route changed after resolution')
    workspace = ROOT / '.cache/benchmark-runtime/tasks' / fingerprint
    if materialize:
        action_cache.Cache().publish(action)
        workspace.mkdir(parents=True, exist_ok=True)
        for name, data in [('target', binary), ('prompt.txt', prompt), ('tools.json', tools), ('manifest.json', manifest_bytes)]:
            destination = workspace / name
            if destination.exists():
                assert destination.read_bytes() == data, 'Frozen runtime input changed'
            else:
                destination.write_bytes(data)
        gateway = {**effective_gateway, 'analysis_tools_file': str(workspace / 'tools.json')}
        if identity.get('attestation'):
            gateway['runtime_environment'] = runtime_attestation.environment(identity['attestation'], input_directory=workspace)
        save(workspace / 'gateway.json', gateway)
        save(workspace / 'harness-config.json', {**effective_harness, 'analysis_tools_file': str(workspace / 'tools.json')})
    if materialize and not offline_fixture:
        adaptive_budget.track(ROOT / directory)
    details = records.read(public / 'manifest.json')
    track = 'basic' if experiment['suite'] == 'record-parser' else 'complex' if experiment['suite'] == 'complex-native-v1' else 'stl' if case['language'] == 'cpp' else 'core'
    return {**case, 'tool_count': case['tool_count'] + int(bool(paging)), 'id': case['id'], 'model': model['id'], 'harness': model['harness'],
            'label': model['label'], 'reasoning': model['reasoning'], 'track': track,
            'input_directory': str(workspace), 'directory': directory,
            **({'inference_trial': trial} if 'inference_trial' in entry else {}),
            'cache_schema': 3, 'action_descriptor': action.descriptor, 'inference_identity': identity, 'fingerprint': fingerprint, 'condition': entry.get('condition', 'legacy-recorded'), 'environment_profile': profile, 'prompt_sha256': hashlib.sha256(prompt).hexdigest(),
            **({'runtime_attestation': identity['attestation']} if 'attestation' in identity else {}),
            **({'request_contract': effective_harness['request_contract']} if 'request_contract' in effective_harness else {}),
            **({'offline_fixture': True} if offline_fixture else {}),
            'binary_sha256': hashlib.sha256(binary).hexdigest(),
            'standard': details.get('standard', 'c++20' if case['language'] == 'cpp' else '2021'),
            'string_abi': details.get('string_abi')}


def grade_saved(directory, task):
    if (directory / 'budget-exhausted.json').exists():
        from request_contract import BudgetExhausted
        raise BudgetExhausted('Task budget exhausted; preserved without retry or automatic grading')
    if (directory / 'harness-failure.json').exists() or (directory / 'interrupted.json').exists():
        raise RuntimeError('Harness/environment failure cannot receive a model correctness grade')
    if task.get('runtime_attestation'):
        for phase in ('before', 'after'):
            proof = records.read(directory / ('runtime-verification-' + phase + '.json'), {})
            if proof.get('identity') != task['runtime_attestation']['identity'] or proof.get('status') != 'verified_local_content_and_route' or proof.get('provider') != task['runtime_attestation']['provider']:
                raise RuntimeError('Runtime verification missing; correctness grading refused')
        if task.get('request_contract') or task['runtime_attestation'].get('configuration', {}).get('harness', {}).get('request_contract'):
            import request_contract
            budget = records.read(directory / 'request-budget.json', {})
            if budget.get('contract') != request_contract.from_task(task) or budget.get('contract_sha256') != request_contract.digest(task['request_contract']) or not request_contract.grading_allowed(budget, task['request_contract']) or not budget.get('provider_forwards'):
                raise RuntimeError('Request-budget verification missing; correctness grading refused')
    if task.get('track') == 'capability':
        from capabilities.store import grade
        return grade(directory, task)
    from grading.store import grade_entry
    return grade_entry(directory, task.get('output_language', task['language']), task['track'])


def launch_model(directory, task, model, cancel_event=None):
    dispatcher = load_module(BENCH / 'harnesses/dispatch.py', 'benchmark_dispatch')
    return dispatcher.launch(directory, task, model, cancel_event, records=records)


def account_saved(entry, directory):
    import usage
    model = records.models()[entry['model']]
    if entry['suite'] == 'capabilities-v1':
        from capabilities.config import load
        experiment = {'pricing_file': load()[0]['pricing_file']}
    else:
        experiment = next(item for item in records.registry()['experiments'] if item['id'] == entry['experiment'])
    prices = records.read(ROOT / (model.get('pricing_file') or experiment['pricing_file']))
    receipt = records.read(directory / 'result.json')
    cost = records.glm_cost(receipt, prices) if model['id'] == 'glm-5.3' else records.measured_cost(receipt, prices)
    return usage.save(directory, prices, cost)


def budget_outcome(directory):
    """Keep a graded final's spending stop on both fresh execution and cache replay."""
    import request_contract
    budget = records.read(directory / 'request-budget.json', {})
    task = records.read(directory / 'task.json', {})
    contract = task.get('request_contract')
    if not contract or not request_contract.grading_allowed(budget, contract):
        return {}
    return {'budget_status': budget['status'],
            'budget_exceeded': budget.get('budget_exceeded', False),
            'queue_stop_required': budget.get('queue_stop_required', False),
            'queue_stop_reason': budget.get('queue_stop_reason')}


def execute(entry, model, cancel_event=None, on_launch=None):
    _execution_allowed(cancel_event)
    entry = inspect_native_entry(entry)
    if entry.get('_offline_missing'):
        raise RuntimeError('Offline benchmark: missing inference cache; provider launch refused')
    if entry['status'].startswith('preparation_'):
        raise RuntimeError('Capability preparation is incomplete; use the configured batch entrypoint')
    directory = ROOT / entry['directory']
    if any((directory / name).exists() for name in ('harness-failure.json', 'interrupted.json')):
        raise PreservedAttempt('Saved harness failure or interruption preserved; no automatic retry or correctness grade')
    if (directory / 'budget-exhausted.json').exists():
        from request_contract import BudgetExhausted
        raise BudgetExhausted('Saved task budget exhausted; preserved without retry or automatic grading')
    if entry['status'] == 'cached':
        expected = prepare_task(entry, model, materialize=False)
        hit = cache.validate(entry, model, expected)
        if hit.get('grading_pending'):
            saved = records.read(directory / 'task.json')
            if saved.get('track') == 'capability':
                saved = {**saved, 'grading_spec': expected['grading_spec'],
                         'grading_spec_sha256': expected['grading_spec_sha256']}
            grade = grade_saved(directory, saved)
            hit.update(grading_pending=False, grading_id=grade['grading_id'], offline_regraded=True)
        print('CACHE HIT:', entry['experiment'], entry['case'], model['id'], '· original receipt retained', flush=True)
        return {**hit, 'accounting_key': account_saved(entry, directory), 'provider_launches': 0,
                **budget_outcome(directory)}
    if entry['status'].startswith('interrupted'):
        raise PreservedAttempt('Interrupted attempt preserved; no automatic retry')
    if entry['status'] == 'not_started' and os.environ.get('REA_BENCHMARK_OFFLINE') == '1':
        raise RuntimeError('Offline benchmark: missing inference cache; provider launch refused')
    if entry.get('suite') == 'capabilities-v1' and entry['status'] == 'not_started' and not entry.get('measurement_registration'):
        raise RuntimeError('New capability inference requires a complete pre-run measurement registration')
    launched = 0
    task = prepare_task(entry, model, materialize=not (directory / 'result.json').exists())
    if task['directory'] != entry['directory']:
        raise ValueError('Attempt was not resolved before dispatch; no provider launch')
    if entry.get('measurement_registration'):
        task = {**task, 'measurement_registration': entry['measurement_registration']}
    _execution_allowed(cancel_event)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'result.json').exists():
        saved = records.read(directory / 'task.json')
        for key in ['model', 'harness', 'reasoning', 'condition', 'binary_sha256', 'prompt_sha256', 'max_tool_calls', 'timeout_seconds', 'max_output_tokens']:
            if not saved or saved.get(key) != task.get(key):
                raise ValueError('Saved inference differs from selected inputs; preserve without retry')
        task = saved
    if not (directory / 'result.json').exists():
        if task.get('track') == 'capability' and not task.get('offline_fixture') and not task.get('measurement_registration'):
            raise RuntimeError('New capability inference requires a complete pre-run measurement registration')
        inputs = cache.inference_files(task)
        action = action_cache.inference(task['inference_identity'], inputs, trial=cache.inference_trial(task))
        if action_cache.Cache().lookup(action):
            raise RuntimeError('Inference action already cached, but original provenance is missing; restore the saved attempt instead of launching a duplicate')
        claim = ROOT / '.cache/benchmark-attempt-registry' / ('shared-' + task['fingerprint'] + '.json')
        save(claim, {'fingerprint': task['fingerprint'], 'directory': str(directory)}, True)
        save(directory / 'task.json', task, True)
        if task.get('track') == 'capability' and not task.get('offline_fixture'):
            from capabilities.registration import Registry
            Registry().claim(task)
        _execution_allowed(cancel_event)
        print('START:', entry['experiment'], entry['case'], model['id'], flush=True)
        if on_launch:
            on_launch()
        try:
            launch_model(directory, task, model, cancel_event)
        except RuntimeError as error:
            if preserved_stream_timeout(directory, task, model):
                raise CaseTransportTimeout('Saved ZCode stream timeout; no retry or correctness grade') from error
            raise
        launched = 1
    if cancel_event and cancel_event.is_set():
        save(directory / 'interrupted.json', {'reason': 'Batch cancelled; no automatic inference retry',
            'model_correctness_grade_allowed': False, 'stopped_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        raise InterruptedError('Batch cancelled before grading')
    if task.get('measurement_registration'):
        from capabilities.registration import Registry
        Registry().verify_task(task)
    grade = grade_saved(directory, task)
    print('GRADED:', entry['case'], grade['passed'], '/', grade['total'], flush=True)
    complete = {'fingerprint': task['fingerprint'], 'environment_profile': task['environment_profile'],
                'grading_key': cache.grading_key(task, directory), 'files_sha256': {
        str(path.relative_to(directory)): records.sha(path) for path in sorted(directory.rglob('*'))
        if path.is_file() and path.relative_to(directory).parts[0] not in ('grades', 'accounting')
        and path.name not in ('selected-grade.json', 'selected-accounting.json')}}
    save(directory / 'completed.json', complete, True)
    cache.register_inference(task, directory)
    account_saved(entry, directory)
    return {'status': 'inferred' if launched else 'graded_saved_inference', 'provider_launches': launched,
            'fingerprint': task['fingerprint'], **budget_outcome(directory)}


@metadata_validation_cache()
def selected_entries(selection, *, defer_preparation=True):
    model_ids = selection['models']
    if model_ids == 'all':
        model_ids = [m for m, spec in records.models().items() if spec.get('enabled', True)]
    # Filter before plan() inspects native attempts on a potentially slow mount.
    native = [experiment for experiment in records.registry()['experiments']
              if experiment['role'] == 'primary' and experiment.get('enabled', True)
              and (selection['suites'] == 'all' or experiment['suite'] in selection['suites'])
              and (selection['experiments'] == 'primary' or experiment['id'] in selection['experiments'])]
    native_inventory = {}
    selected = [entry for mid in model_ids for experiment in native
                for entry in plan(model=mid, experiment_id=experiment['id'], condition=selection['condition'],
                                  deferred=defer_preparation, inventory=native_inventory)
                if benchmark_config.accepts(selection, entry)]
    if selection.get('capabilities', True):
        from capabilities.coordinator import entries
        inventory = {}
        selected += [entry for mid in model_ids for entry in entries(records.models()[mid], selection=selection, inventory=inventory, deferred=defer_preparation)
                     if benchmark_config.accepts(selection, entry)]
    return selected


def needs_installed_runtime(selected, offline):
    """Cache misses and legacy caches need no installed SDK in offline mode."""
    from capabilities.coordinator import runtime_controls_current
    for entry in selected:
        if offline:
            entry = inspect_native_entry(entry)
        if entry.get('_offline_missing'):
            continue
        if entry.get('preparation_status') == 'unchecked':
            return True
        if not offline and entry['status'] in ('not_started', 'preparation_pending', 'preparation_unchecked', 'unchecked'):
            return True
        if not entry.get('directory'):
            continue
        saved = records.read(ROOT / entry['directory'] / 'task.json', {})
        if offline and saved.get('runtime_attestation') and not runtime_controls_current(saved):
            entry['_offline_missing'] = True
            continue
        if entry.get('suite') == 'capabilities-v1' or saved.get('runtime_attestation'):
            return True
    return False


def scheduled_counts(selected):
    """A planning denominator includes unprepared and invalid cells, not samples."""
    from collections import Counter
    return {'scheduled_cells': len(selected), 'by_status': dict(sorted(Counter(row['status'] for row in selected).items())),
            'by_suite': dict(sorted(Counter(row['suite'] for row in selected).items())),
            'preparation_pending': sum(row['status'] == 'preparation_pending' for row in selected),
            'preparation_invalid': sum(row['status'] == 'preparation_invalid' for row in selected)}


def scheduled_cells(selected):
    return [{key: row.get(key) for key in ('experiment', 'suite', 'case', 'model', 'condition', 'status', 'preparation_status')}
            for row in selected]


def prepare_next_cell(entry, model, registrations, measurement, *, offline=False, publish_selection=True):
    if entry.get('suite') != 'capabilities-v1':
        import native_attempts
        entry = {**entry, 'inference_trial': cache.inference_trial(entry)}
        task = prepare_task(entry, model, materialize=False)
        if not offline and publish_selection:
            native_attempts.select(entry, task, root=ROOT)
        return inspect_native_entry({**entry, 'directory': task['directory'],
            'status': 'unchecked', 'resolved_fingerprint': task['fingerprint']})
    from capabilities.coordinator import inspect_selected, prepare_selected, resolve_selected
    selected = inspect_selected([entry])
    selected = prepare_selected(selected)
    entry = resolve_selected(selected, offline=offline)[0]
    if entry['status'] == 'not_started' and not offline:
        if measurement is None:
            raise ValueError('New typed cell lacks its frozen queue schedule')
        entry = registrations.complete_cell(measurement, entry, model)
    return entry


@metadata_validation_cache()
@startup_policy.execution
def run_batch(selected, selection, offline=False, report_every=0, finalize=None, timings=None):
    """One batch lock, one sequential queue per model, no retry or cache bypass."""
    from request_contract import BudgetExhausted
    budget_stop_scope = selection['execution'].get('budget_stop_scope', 'model')
    timeout_stop_scope = selection['execution'].get('transport_timeout_scope', 'model')
    if timeout_stop_scope not in ('model', 'case'):
        raise ValueError('transport_timeout_scope must be model or case')
    if budget_stop_scope not in ('model', 'case'):
        raise ValueError('budget_stop_scope must be model or case')
    skip_saved_failures = selection['execution'].get('skip_saved_failures', False)
    if type(skip_saved_failures) is not bool:
        raise ValueError('skip_saved_failures must be a boolean')
    timings = timings or BatchTimings()
    offline = offline or os.environ.get('REA_BENCHMARK_OFFLINE') == '1'
    if offline:
        os.environ['REA_BENCHMARK_OFFLINE'] = '1'
    # A stop during lock acquisition or local action resolution remains newer
    # than this explicit batch request and must never be cleared by its resume.
    initial_stop_epoch = _execution_state().get('last_stop_at')
    lock = ROOT / '.cache/benchmark-runtime/live-batch.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError('Another batch owns live-batch.lock; duplicate execution refused')
    model_ids = list(dict.fromkeys(e['model'] for e in selected))
    os.write(descriptor, json.dumps({'pid': os.getpid(), 'models': model_ids}).encode())
    os.close(descriptor)
    per_cell = startup_policy.trusted()
    if per_cell:
        import multiprocessing
        process_context = multiprocessing.get_context('spawn')
        cancel = process_context.Event()
        shared_launches = process_context.Value('q', 0)
    else:
        cancel = threading.Event()
        shared_launches = None
    def interrupted(signum, frame):
        cancel.set()
        raise KeyboardInterrupt
    previous = {number: signal.signal(number, interrupted) for number in (signal.SIGTERM, signal.SIGINT)}
    scheduled = scheduled_cells(selected)
    live = any(e['status'] in ('not_started', 'preparation_pending', 'preparation_unchecked', 'unchecked') for e in selected) and not offline
    measurement = None
    def cleanup():
        cancel.set()
        lock.unlink(missing_ok=True)
        for number, handler in previous.items():
            signal.signal(number, handler)
    def check_stop(phase, *, before_resume=False):
        state = _execution_state()
        changed_epoch = not offline and state.get('last_stop_at') != initial_stop_epoch
        stopped = live and not before_resume and state.get('experiments_stopped', True)
        if cancel.is_set() or changed_epoch or stopped:
            cancel.set()
            raise InterruptedError('Batch stopped during ' + phase + '; no provider launch')
    def observe_stop_state():
        while not cancel.wait(1):
            try:
                _execution_allowed(cancel)
            except InterruptedError:
                return
    try:
        import preparation_session
        with preparation_session.scope(runtime_attestation.config().get('verification_mode') == 'startup_once'):
            if per_cell:
                from capabilities.registration import Registry
                registrations = Registry()
                with timings.stage('schedule'):
                    if live:
                        cancel.stop_epoch = initial_stop_epoch
                        set_execution_stopped(False, expected_stop_at=initial_stop_epoch)
                        threading.Thread(target=observe_stop_state, daemon=True).start()
                    check_stop('resume')
                    measurement = registrations.begin(selected, selection, offline=offline)
                for stage in ('preparation_inspection', 'action_resolution', 'preparation',
                              'prepared_action_resolution', 'binding'):
                    timings.skip(stage, 'Prepared per cell; no full-catalogue startup barrier')
                save(BENCH / 'config/benchmark-status.json', {
                    'schema_version': 1, 'selection': selection, 'selected_tasks': len(selected),
                    'scheduled_cells': scheduled, 'measurement_registration': measurement,
                    'running': True, 'outcomes': [], 'failures': [], 'provider_launches': 0,
                    'cache_hits': 0, 'model_api_calls': 0, 'unprocessed_tasks': len(selected),
                    'timings': timings.snapshot()})
            else:
                with timings.stage('preparation_inspection'):
                    from capabilities.coordinator import inspect_selected
                    selected = inspect_selected(selected)
                    scheduled = scheduled_cells(selected)
                    check_stop('preparation inspection', before_resume=True)
                with timings.stage('action_resolution'):
                    from capabilities.coordinator import resolve_selected
                    selected = resolve_selected(selected, offline=offline)
                    scheduled = scheduled_cells(selected)
                    live = any(e['status'] in ('not_started', 'preparation_pending', 'preparation_unchecked', 'unchecked') for e in selected) and not offline
                    check_stop('initial action resolution', before_resume=True)
                with timings.stage('schedule'):
                    from capabilities.registration import Registry
                    registrations = Registry()
                    if live:
                        cancel.stop_epoch = initial_stop_epoch
                        set_execution_stopped(False, expected_stop_at=initial_stop_epoch)
                        threading.Thread(target=observe_stop_state, daemon=True).start()
                    check_stop('resume')
                    measurement = registrations.begin(selected, selection, offline=offline)
                    check_stop('registration schedule')
                with timings.stage('preparation'):
                    from capabilities.coordinator import prepare_selected
                    selected = prepare_selected(selected)
                    check_stop('preparation')
                with timings.stage('prepared_action_resolution'):
                    selected = resolve_selected(selected, offline=offline)
                    check_stop('prepared action resolution')
                with timings.stage('binding'):
                    selected, measurement = registrations.complete(measurement, selected, selection, prepare_task)
                    check_stop('registration')
    except BaseException as error:
        timings.skip('finalization', 'Preparation failed or was cancelled; saved status only')
        try:
            if live:
                set_execution_stopped(True)
        finally:
            try:
                save(BENCH / 'config/benchmark-status.json', {'schema_version': 1, 'selection': selection,
                    'selected_tasks': len(scheduled), 'scheduled_cells': scheduled,
                    'measurement_registration': measurement,
                    'cache_hits': 0, 'provider_launches': 0, 'model_api_calls': 0, 'offline': offline,
                    'outcomes': [], 'failures': [{'model': None, 'case': None, 'error': str(error)}],
                    'unprocessed_tasks': len(scheduled), 'preparation_failed': True,
                    'timings': timings.snapshot()})
            finally:
                cleanup()
        raise
    outcomes, failures, queue_holds = [], [], []
    launches = 0
    launch_lock = threading.Lock()
    def on_launch():
        nonlocal launches
        with launch_lock:
            launches += 1
    def status(**extra):
        return {'schema_version': 1, 'selection': selection, 'selected_tasks': len(selected),
                'scheduled_cells': scheduled, 'measurement_registration': measurement,
                'cache_hits': sum(item['status'] == 'cache_hit' for item in outcomes),
                'provider_launches': shared_launches.value if shared_launches is not None else launches,
                'model_api_calls': 0 if (shared_launches.value if shared_launches is not None else launches) == 0 else None,
                'offline': offline,
                'outcomes': sorted(outcomes, key=lambda x: (x['experiment'], x['case'], x['model'])),
                'failures': failures, 'queue_holds': queue_holds,
                'unprocessed_tasks': len(selected) - len(outcomes) - len(failures),
                'timings': timings.snapshot(), **extra}
    successful = False
    receipt = None
    try:
        with timings.stage('execute'):
            queues = {mid: iter([e for e in selected if e['model'] == mid]) for mid in model_ids}
            model_limit = min(len(model_ids), selection['execution']['max_parallel_models'])
            per_model_limit = selection['execution'].get('max_parallel_cases_per_model', 1)
            worker_limit = min(len(selected), model_limit * per_model_limit)
            if per_cell:
                import parallel_cells
                trace_directory = ROOT / '.cache/benchmark-runtime/profiling' / (
                    'cells-' + str(os.getpid()) + '-' + str(time.time_ns()))
                trace_directory.mkdir(parents=True, exist_ok=True)
                pool = ProcessPoolExecutor(max_workers=worker_limit, mp_context=process_context,
                    initializer=parallel_cells.initialize, initargs=(selection['execution']['startup_mode'],
                    cancel, initial_stop_epoch, shared_launches, str(trace_directory), time.perf_counter_ns()))
                print('PARALLEL CELLS:', worker_limit, 'workers;', per_model_limit,
                      'active cases per model;', trace_directory, flush=True)
            else:
                pool = ThreadPoolExecutor(max_workers=worker_limit)
            with pool as workers:
                pending = {}
                active_counts = {mid: 0 for mid in model_ids}
                blocked_models, exhausted_models = set(), set()
                def submit(mid):
                    if mid in blocked_models or mid in exhausted_models or active_counts[mid] >= per_model_limit:
                        return False
                    # Limit distinct active models independently from the number
                    # of case slots, including when more models are selected.
                    if not active_counts[mid] and sum(n > 0 for n in active_counts.values()) >= model_limit:
                        return False
                    entry = next(queues[mid], None) if not cancel.is_set() else None
                    if entry:
                        if per_cell:
                            # A process-pool worker may change between cases. Pass
                            # settled directories so its rolling mean sees all
                            # earlier answers from this model, without rescanning.
                            entry = {**entry, '_budget_completed': [o['directory'] for o in outcomes
                                if o['model'] == mid and o.get('directory')]}
                        if per_cell:
                            future = workers.submit(parallel_cells.execute, entry, records.models()[mid], measurement, offline)
                        else:
                            future = workers.submit(copy_context().run, execute, entry, records.models()[mid], cancel, on_launch)
                        pending[future] = (mid, entry)
                        active_counts[mid] += 1
                        return True
                    exhausted_models.add(mid)
                    return False
                def fill_slots():
                    # Round robin fills one slot/model before the second. Only
                    # settled outcomes contribute to each newly frozen budget.
                    while len(pending) < worker_limit and not cancel.is_set():
                        if live:
                            try:
                                _execution_allowed(cancel)
                            except InterruptedError:
                                return
                        submitted = False
                        for mid in model_ids:
                            if len(pending) >= worker_limit:
                                break
                            submitted = submit(mid) or submitted
                        if not submitted:
                            break
                fill_slots()
                while pending:
                    future = next(as_completed(pending))
                    mid, entry = pending.pop(future)
                    active_counts[mid] -= 1
                    try:
                        result = future.result()
                        outcomes.append({'experiment': entry['experiment'], 'case': entry['case'], 'model': mid,
                                         'condition': entry['condition'], **result})
                    except Exception as error:
                        budget_stop = isinstance(error, BudgetExhausted)
                        transport_timeout = isinstance(error, CaseTransportTimeout)
                        preserved = isinstance(error, PreservedAttempt)
                        stale = isinstance(error, cache.StaleInference)
                        failures.append({'model': mid, 'case': entry['case'], 'error': str(error),
                                         **({'classification': 'case_transport_timeout'} if transport_timeout else
                                            {'classification': 'task_budget_exhausted'} if budget_stop else
                                            {'classification': 'preserved_terminal_attempt'} if preserved else
                                            {'classification': 'stale_inference_not_reused'} if stale else {})})
                        if (offline or budget_stop and budget_stop_scope == 'case'
                                or transport_timeout and timeout_stop_scope == 'case'
                                or (preserved or stale) and skip_saved_failures):
                            # Cache inspection covers the entire configured matrix.
                            # A missing/invalid result cannot hide later valid hits.
                            pass
                        else:
                            blocked_models.add(mid)
                            print('MODEL QUEUE STOPPED:', mid, entry['case'], str(error), flush=True)
                    else:
                        if result.get('queue_stop_required') and not offline and budget_stop_scope == 'model':
                            hold = {'model': mid, 'experiment': entry['experiment'],
                                    'condition': entry['condition'], 'case': entry['case'],
                                    'reason': result.get('queue_stop_reason') or 'task_budget_reached'}
                            queue_holds.append(hold)
                            blocked_models.add(mid)
                            print('MODEL QUEUE HELD:', mid, entry['case'], hold['reason'],
                                  '· completed answer graded; no further provider launch', flush=True)
                    fill_slots()
                    processed = len(outcomes) + len(failures)
                    if report_every and processed % report_every == 0:
                        # Keep progress durable without an all-suite report scan
                        # between cells. The successful batch builds HTML once.
                        save(BENCH / 'config/benchmark-status.json', status(running=True))
                        print('PROGRESS:', processed, '/', len(selected), '| benchmark-status.json saved', flush=True)
        receipt = status()
        successful = not failures and not queue_holds and not receipt['unprocessed_tasks'] and not cancel.is_set()
        if cancel.is_set():
            receipt['interrupted'] = True
        save(BENCH / 'config/benchmark-status.json', receipt)
        print('BENCHMARK:', len(outcomes), '/', len(selected), '| cache hits:', receipt['cache_hits'],
              '| provider launches:', receipt['provider_launches'], '| failures:', len(failures), flush=True)
    except BaseException:
        # Preserve the frozen denominator even on an interrupted run.
        receipt = status(interrupted=True)
        raise
    finally:
        cancel.set()
        try:
            if live:
                set_execution_stopped(True)
            if (successful or receipt is not None and not receipt.get('interrupted')
                    and (budget_stop_scope == 'case'
                         or offline and receipt.get('unprocessed_tasks') == 0)) and finalize:
                # Make the completed batch visible to report generation first.
                with timings.stage('finalization'):
                    finalize()
            else:
                timings.skip('finalization', 'No finalizer configured' if successful else
                             'Batch incomplete or cancelled; saved status only')
        except BaseException as error:
            if receipt is not None:
                receipt['finalization_error'] = str(error)
            raise
        finally:
            try:
                if receipt is not None:
                    receipt['timings'] = timings.snapshot()
                    save(BENCH / 'config/benchmark-status.json', receipt)
            finally:
                cleanup()
    if not successful:
        raise RuntimeError('Benchmark incomplete; saved attempts preserved; see benchmark-status.json.')
    return receipt


def finalize():
    import archive_profiles
    import audit_condition
    import report
    # Historical output audits are unchanged by cache hits. Deep auditing is
    # still available as an explicit target; new inference gets it once.
    status = records.read(BENCH / 'config/benchmark-status.json', {})
    try:
        if any(row.get('status') in ('inferred', 'graded_saved_inference') for row in status.get('outcomes', [])):
            archive_profiles.main()
            audit_condition.main()
    finally:
        # Preserve an archival/audit error while still publishing current scores
        # from saved records. Missing historical source bytes are never invented.
        report.build()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'plan', 'run', 'verify', 'regrade', 'prepare'], nargs='?', default='list')
    parser.add_argument('--config', type=Path, default=benchmark_config.DEFAULT)
    parser.add_argument('--suite', choices=['all', *[s['id'] for s in records.registry()['suites']]])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--model', choices=[*records.models(), 'all'])
    group.add_argument('--models', nargs='+', choices=list(records.models()))
    parser.add_argument('--harness', choices=['deepseek-harness', 'zcode'])
    parser.add_argument('--experiment')
    parser.add_argument('--evidence-access', choices=['binary', 'premises_only'],
                        help='capability recovery or separately registered premises-only control')
    parser.add_argument('--condition', choices=['active', *[c['id'] for c in records.condition_catalog()['conditions']]])
    parser.add_argument('--dry-run', '--cached-only', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Refuse every missing inference cache before credentials or provider launch')
    parser.add_argument('--runtime-ready', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--resume-experiments', action='store_true')
    parser.add_argument('--report-every', type=int)
    parser.add_argument('--all-saved', action='store_true', help='Offline regrade across saved historical and active conditions')
    parser.add_argument('--startup-check', metavar='CASE', help='Prepare the full selection and compile/run one local control; zero model calls')
    parser.add_argument('--startup-output', type=Path, help='Private output directory for --startup-check')
    args = parser.parse_args()
    offline = args.offline or os.environ.get('REA_BENCHMARK_OFFLINE') == '1'
    if offline:
        os.environ['REA_BENCHMARK_OFFLINE'] = '1'
    try:
        selection = benchmark_config.load(args.config)
        if args.models or args.model:
            selection['models'] = list(dict.fromkeys(args.models)) if args.models else 'all' if args.model == 'all' else [args.model]
        if args.suite:
            selection['suites'] = 'all' if args.suite == 'all' else [args.suite]
        if args.experiment:
            selection['experiments'] = [args.experiment]
        if args.condition:
            selection['condition'] = args.condition
        if args.evidence_access is not None:
            selection['evidence_access'] = args.evidence_access
        if 'evidence_access' in selection:
            from capabilities.evidence_access import validate_selection
            from capabilities.tasks import declared
            validate_selection(selection, declared())
        if args.report_every is not None:
            if args.report_every < 0:
                raise ValueError('--report-every must be nonnegative')
            selection['execution']['report_every'] = args.report_every
        if args.action == 'prepare':
            if args.suite != 'capabilities-v1':
                raise ValueError('Prepare requires the registered capability suite')
            from capabilities.tasks import freeze_all, preview
            if args.dry_run:
                print(json.dumps({'capability_preparation': preview(), 'model_calls': 0,
                                  'build_executions': 0}, indent=2))
                return
            with metadata_validation_cache():
                freeze_all()
            return
        if args.action == 'regrade':
            if args.dry_run:
                print(json.dumps({'action': 'regrade', 'selection': selection, 'all_saved': args.all_saved,
                                  'offline': True, 'model_calls': 0, 'grading_executions': 0}, indent=2))
                return
            regrade(selection, all_saved=args.all_saved)
            return
        timings = BatchTimings() if args.action == 'run' and not args.dry_run else None
        if timings is None:
            selected = selected_entries(selection)
        else:
            with timings.stage('selection'):
                selected = selected_entries(selection)
        if args.harness and any(records.models()[e['model']]['harness'] != args.harness for e in selected):
            raise ValueError('Selected model is registered to a different harness')
        if args.action in ['list', 'plan'] or args.dry_run:
            preparation_snapshot = [{'case': row['case'], 'status': row['preparation_status']}
                for row in selected if row.get('suite') == 'capabilities-v1']
            print(json.dumps({'experiments_stopped': records.read(BENCH / 'config/execution.json')['experiments_stopped'],
                              'selection': selection, 'tasks': selected, 'model_calls': 0,
                              'scheduled': scheduled_counts(selected),
                              'capability_preparation': preparation_snapshot,
                              'note': 'Cache status is preliminary; execution validates all committed artifacts and effective inputs.'}, indent=2))
            return
        if not selected:
            raise ValueError('Selection contains no registered active cases')
        if args.startup_check:
            if args.action != 'run' or not args.runtime_ready or args.startup_output is None:
                raise ValueError('Use make benchmark with --startup-check CASE --startup-output DIRECTORY')
            import profile_startup
            directory = args.startup_output.resolve()
            directory.mkdir(parents=True, exist_ok=True)
            return profile_startup.measure(argparse.Namespace(output=directory, case=args.startup_check,
                model=next(row['model'] for row in selected if row['case'] == args.startup_check),
                origin=int(os.environ.get('REA_BENCHMARK_STARTED_NS', time.monotonic_ns()))),
                selection=selection, selected=selected)
        if args.action == 'run' and records.read(BENCH / 'config/execution.json')['experiments_stopped'] and not args.resume_experiments and not offline:
            parser.error('Experiments remain stopped. Explicit --resume-experiments is required for execution; --offline forbids provider launches.')
        if args.action == 'run' and not args.runtime_ready and needs_installed_runtime(selected, offline):
            import runtime_storage
            import sys
            raise SystemExit(runtime_storage.execute([sys.executable, str(Path(__file__).resolve()),
                *sys.argv[1:], '--runtime-ready']))
        if args.action == 'verify':
            from capabilities.coordinator import inspect_selected
            verify_zcode(inspect_selected(selected))
            return
        if records.read(BENCH / 'config/execution.json')['experiments_stopped'] and not args.resume_experiments and not args.offline:
            parser.error('Experiments remain stopped. Explicit --resume-experiments is required for execution; --offline forbids provider launches.')
        run_batch(selected, selection, offline=args.offline, report_every=selection['execution']['report_every'], finalize=finalize, timings=timings)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, str(error) + '\n')


def regrade(selection, *, all_saved=False):
    from grading.offline import guard
    with guard():
        return regrade_offline(selection, all_saved=all_saved)


def regrade_offline(selection, *, all_saved=False):
    """Pure offline migration: never prepare credentials or enter launch_model."""
    import usage
    from grading import store
    os.environ['REA_BENCHMARK_OFFLINE'] = '1'
    data = records.collect()
    rows = data['records'] + data['condition_records'] if all_saved else records.active_condition_rows(data)
    chosen = []
    for row in rows:
        entry = {'model': row['model'], 'suite': row['case']['suite'], 'experiment': row['experiment'], 'case': row['case']['id']}
        if not benchmark_config.accepts(selection, entry):
            continue
        if not row.get('receipt') or not row.get('grade') or row.get('environment_failure'):
            continue
        chosen.append(row)
    lock = ROOT / '.cache/benchmark-runtime/live-batch.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    failures, outcomes = [], []
    try:
        os.write(descriptor, json.dumps({'pid': os.getpid(), 'operation': 'offline-regrade'}).encode())
        preserved = {}
        for row in chosen:
            directory = ROOT / row['directory']
            for path in directory.rglob('*'):
                if path.is_file() and path.relative_to(directory).parts[0] not in ('grades', 'accounting') and path.name != 'selected-grade.json':
                    preserved[str(path.relative_to(ROOT))] = records.sha(path)
        for row in chosen:
            directory = ROOT / row['directory']
            old = records.read(directory / 'grade.json')
            receipt = records.read(directory / 'result.json')
            language = receipt.get('output_language', row.get('output_language') or row['case']['language'])
            track = 'basic' if row['case']['suite'] == 'record-parser' else 'complex' if row['case']['suite'] == 'complex-native-v1' else 'stl' if language == 'cpp' else 'core'
            task = {**row['case'], **records.read(directory / 'task.json', {}), 'track': track,
                    'language': language, 'output_language': language,
                    'standard': receipt.get('standard') or row['case'].get('standard') or ('c++20' if language == 'cpp' else '2021'),
                    'string_abi': receipt.get('string_abi', row['case'].get('string_abi'))}
            try:
                result = store.grade(directory, task)
                pricing_path = records.models()[row['model']].get('pricing_file') or next(e['pricing_file'] for e in data['experiments'] if e['id'] == row['experiment'])
                prices = records.read(ROOT / pricing_path)
                cost = records.glm_cost(receipt, prices) if row['model'] == 'glm-5.3' else records.measured_cost(receipt, prices)
                usage.save(directory, prices, cost)
                outcomes.append({'directory': row['directory'], 'grading_id': result['grading_id'],
                                 'original_passed': old['passed'], 'selected_passed': result['passed'],
                                 'selected_total': result['total']})
                print('OFFLINE GRADE:', row['model'], row['case']['id'], result['passed'], '/', result['total'], flush=True)
            except (ValueError, RuntimeError, OSError) as error:
                failures.append({'directory': row['directory'], 'error': str(error)})
                print('OFFLINE GRADE BLOCKED:', row['model'], row['case']['id'], str(error), flush=True)
        if selection.get('capabilities', True):
            from capabilities.coordinator import regrade_saved
            typed_outcomes, typed_failures, typed_preserved = regrade_saved(selection, grade_saved)
            outcomes.extend(typed_outcomes)
            failures.extend(typed_failures)
            preserved.update(typed_preserved)
        if any(records.sha(ROOT / name) != digest for name, digest in preserved.items()):
            raise RuntimeError('Offline grading changed an original artifact')
        save(BENCH / 'config/regrading-status.json', {'version': store.VERSION, 'model_api_calls': 0,
             'provider_launches': 0, 'original_files_preserved': len(preserved),
             'preserved_files_sha256': preserved, 'outcomes': outcomes, 'failures': failures})
        import report
        report.build()
    finally:
        os.close(descriptor)
        lock.unlink()
    if failures:
        raise RuntimeError('Offline regrading incomplete; originals preserved; see regrading-status.json')


def verify_zcode(selected):
    backend = load_module(BENCH / 'harnesses/zcode.py', 'zcode_backend')
    model = records.models()['glm-5.3']
    checks = []
    validation_root = ROOT / '.cache/benchmark-runtime/adapter-validation'
    for experiment_id, case_id in [('native-pilot-20261019', 'cpp'), ('stl-rust-data-v2-20261019', 'rust-data-v2')]:
        entry = next(x for x in plan(model='glm-5.3') if x['experiment'] == experiment_id and x['case'] == case_id)
        task = prepare_task(entry, model, offline_fixture=True)
        directory = validation_root / (str(time.time_ns()) + '-' + case_id)
        result = backend.launch(directory, task, fixture=True)
        trace = records.read_trace(directory / 'tool-calls.jsonl')
        answer, _ = backend.collect_wire(directory)
        passed = result['exit_code'] == 0 and not result['policy_violations'] and len(trace) == 3
        passed = passed and trace[0]['is_error'] and not trace[1]['is_error'] and trace[-1]['tool'] == 'close_binary'
        passed = passed and answer == 'Fixture complete; no model inference was used.'
        checks.append({'experiment': experiment_id, 'case': case_id, 'passed': passed})
    save(validation_root / 'validation.json', {'passed': all(x['passed'] for x in checks), 'checks': checks, 'paid_model_calls': 0})
    assert all(x['passed'] for x in checks), 'Adapter local fixture failed'
    print('PASS: migrated ZCode adapter; eight/twelve tools; no paid model calls')


if __name__ == '__main__':
    import sys
    command_started = time.perf_counter()
    try:
        main()
    finally:
        # The outer invocation also includes lazy runtime extraction/cleanup.
        if len(sys.argv) > 1 and sys.argv[1] == 'run' and not any(
                flag in sys.argv for flag in ('--dry-run', '--cached-only')):
            elapsed = time.perf_counter() - command_started
            print(f'BENCHMARK COMMAND TOTAL: {elapsed:.3f}s', flush=True)
            if (BENCH / 'config/benchmark-status.json').is_file():
                from report_io import publish_timing
                publish_timing(elapsed)
