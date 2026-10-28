"""Provider dispatch only; source hash is part of inference identity."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / 'benchmarks'
sys.path.insert(0, str(BENCH / 'scripts'))
import runtime_attestation
import usage as usage_accounting
import request_contract


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(path, value, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def budget_outcome(result, certificate):
    """Expose resource compliance separately from answer/behavior grading."""
    return {**result, 'budget_status': certificate['status'],
            'budget_exceeded': certificate.get('budget_exceeded', False),
            'queue_stop_required': certificate.get('queue_stop_required', False),
            'queue_stop_reason': certificate.get('queue_stop_reason')}


def launch(directory, task, model, cancel_event=None, records=None):
    if os.environ.get('REA_BENCHMARK_OFFLINE') == '1':
        raise RuntimeError('Offline benchmark forbids provider launches')
    if task.get('offline_fixture'):
        raise ValueError('Offline fixture cannot dispatch a model')
    if cancel_event and cancel_event.is_set():
        raise InterruptedError('Batch cancelled before runtime verification; no provider launch')
    if task['model'] != model['id'] or task['harness'] != model['harness']:
        raise ValueError('Attested dispatch model mismatch')
    before = runtime_attestation.verify(task)
    if cancel_event and cancel_event.is_set():
        raise InterruptedError('Batch cancelled during runtime verification; no provider launch')
    save(directory / 'runtime-verification-before.json', before, True)
    try:
        return _launch(directory, task, model, cancel_event, records=records, proof=before)
    finally:
        try:
            after = runtime_attestation.verify(task)
            save(directory / 'runtime-verification-after.json', after, True)
        except Exception:
            if not (directory / 'harness-failure.json').exists():
                save(directory / 'harness-failure.json', {'classification': 'runtime_or_provider_drift',
                    'model_correctness_grade_allowed': False}, True)
            raise RuntimeError('Runtime/provider drift: preserve outputs without correctness grading') from None


def _launch(directory, task, model, cancel_event=None, records=None, proof=None):
    if model['harness'] == 'zcode':
        backend = load_module(BENCH / 'harnesses/zcode.py', 'zcode_backend')
        credentials = runtime_attestation.glm_credentials(task['runtime_attestation'])
        raw = backend.launch(directory, task, credentials, cancel_event=cancel_event, attestation_proof=proof)
        answer, provider_models = backend.collect_wire(directory)
        observations = {x['sequence']: x for x in raw['usage_observations_raw']}
        usage, normalized = records.normalize_glm_usage(list(observations.values()))
        trace = records.read_trace(directory / 'tool-calls.jsonl')
        result = {**raw, 'model': model['id'], 'model_label': model['label'], 'harness': 'ZCode',
                  'harness_version': '0.17.0', 'provider': 'bigmodel-standard-api', 'reasoning': model['reasoning'],
                  'environment_profile': task['environment_profile'], 'condition': task['condition'],
                  'runtime_attestation_id': task['runtime_attestation']['identity'],
                  'language': task['language'], 'output_language': task['language'],
                  'case_id': task['id'], 'binary_sha256': task['binary_sha256'], 'prompt_sha256': task['prompt_sha256'],
                  'reported_usage': usage, 'usage_observations': normalized, 'provider_models': provider_models,
                  'tool_calls': len(trace), 'tool_errors': sum(x['is_error'] for x in trace),
                  'explicit_close': any(x['tool'] == 'close_binary' and not x['is_error'] for x in trace),
                  'actual_cost_usd': None}
        final_usage = {item['sequence']: item for item in raw['request_budget']['usage_observations']}
        request_status = {item['sequence']: item for item in raw['requests']}
        for observation in result.get('usage_observations_raw', []):
            observation['complete'] = bool(final_usage.get(observation['sequence'], {}).get('complete')) and request_status.get(observation['sequence'], {}).get('response_complete') is True
        result, _ledger = usage_accounting.derive({**result, 'usage_observations': raw['request_budget']['usage_observations'],
            'provider_forwards': raw['request_budget']['provider_forwards'],
            'request_ledger_complete': raw['request_budget']['request_ledger_complete']})
        result = budget_outcome(result, raw['request_budget'])
        save(directory / 'inference.json', result, True)
        save(directory / 'result.json', result, True)
        if (raw['timed_out'] and not raw['upstream_error']
                and all(x.get('category') == 'wall_limit_reached' for x in raw['policy_violations'])
                and request_contract.deadline_grade_allowed(raw['request_budget'], task['request_contract'])):
            save(directory / 'deadline-exceeded.json', {'classification': 'completed_not_passed',
                'reason': 'wall_limit_reached', 'automatic_retry': False,
                'contract_sha256': raw['request_budget']['contract_sha256']}, True)
            return
        if raw['request_budget']['status'] == 'budget_exhausted':
            save(directory / 'budget-exhausted.json', {'reason': raw['request_budget']['stop_reason'],
                'classification': 'task_budget_exhausted', 'automatic_retry': False}, True)
            raise request_contract.BudgetExhausted('Task limit reached: ' + raw['request_budget']['stop_reason'] + '; attempt preserved without retry')
        if (raw['policy_violations'] or raw['upstream_error'] or (raw['exit_code'] != 0 and not raw['timed_out'])
                or not request_contract.grading_allowed(raw['request_budget'], task['request_contract'])
                or (raw['request_budget']['status'] == 'completed_over_budget' and (raw['exit_code'] != 0 or raw['timed_out']))):
            save(directory / 'harness-failure.json', {**raw, 'environment_profile': task['environment_profile'],
                'classification': 'harness_or_provider_failure', 'model_correctness_grade_allowed': False}, True)
            raise RuntimeError('ZCode/provider failed: preserved raw traces and usage; no model correctness grade')
    else:
        budget = load_module(BENCH / 'harnesses/provider-transport.py', 'benchmark_budget_transport')
        failure = None
        try:
            with budget.transport(directory, task) as transport:
                _launch_dsh(directory, task, cancel_event, transport.base_url, transport.deadline_ms,
                            budget_stop=lambda: transport.gate.exhausted)
        except BaseException as error:
            failure = error
        if not (directory / 'request-budget.json').exists():
            if failure:
                raise failure
            raise RuntimeError('Missing request-budget receipt')
        certificate = json.loads((directory / 'request-budget.json').read_bytes())
        harness_path = directory / 'harness-result.json'
        raw = json.loads(harness_path.read_bytes()) if harness_path.exists() else {
            'model': task['model'], 'provider': task['runtime_attestation']['provider']['provider'],
            'runtime_attestation_id': task['runtime_attestation']['identity'],
            'exit_code': 1, 'timed_out': False, 'environment_profile': task['environment_profile']}
        result, _ledger = usage_accounting.derive({**raw, 'request_budget': certificate, 'requests': certificate['requests'],
            'usage_observations': certificate['usage_observations'], 'provider_forwards': certificate['provider_forwards'],
            'request_ledger_complete': certificate['request_ledger_complete']})
        result = budget_outcome(result, certificate)
        save(directory / 'result.json', result, True)
        if (raw.get('timed_out') is True and not isinstance(failure, InterruptedError)
                and request_contract.deadline_grade_allowed(certificate, task['request_contract'])):
            save(directory / 'deadline-exceeded.json', {'classification': 'completed_not_passed',
                'reason': 'wall_limit_reached', 'automatic_retry': False,
                'contract_sha256': certificate['contract_sha256']}, True)
            return
        if certificate['status'] == 'budget_exhausted':
            save(directory / 'budget-exhausted.json', {'reason': certificate['stop_reason'],
                'classification': 'task_budget_exhausted', 'automatic_retry': False}, True)
            raise request_contract.BudgetExhausted('Task limit reached: ' + certificate['stop_reason'] + '; attempt preserved without retry')
        if (failure or not request_contract.grading_allowed(certificate, task['request_contract']) or certificate['provider_forwards'] == 0
                or (certificate['status'] == 'completed_over_budget' and (raw.get('exit_code') != 0 or raw.get('timed_out')))):
            if not (directory / 'harness-failure.json').exists():
                save(directory / 'harness-failure.json', {'classification': 'request_contract_failure',
                    'model_correctness_grade_allowed': False}, True)
            if failure:
                raise failure
            raise RuntimeError('DSH request-budget contract failed; outputs cannot receive a model correctness grade')


def _launch_dsh(directory, task, cancel_event, base_url, deadline_ms, budget_stop=None):
        environment = {**os.environ, 'REA_EXPLICIT_RUN_AUTHORIZED': '1',
                       'REA_ATTESTED_DISPATCH': task['runtime_attestation']['identity'],
                       'REA_BENCHMARK_TRANSPORT_URL': base_url,
                       'REA_BENCHMARK_DEADLINE_MS': str(deadline_ms),
                       'REA_BENCHMARK_CONFIG': str(Path(task['input_directory']) / 'harness-config.json')}
        # Node injection, proxy and TLS overrides cannot change the sealed route.
        for key in ['NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH']:
            environment.pop(key, None)
        child = subprocess.Popen([str(ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'),
            str(BENCH / 'harnesses/deepseek-harness.mjs'), '--task', str(directory / 'task.json')],
            env=environment, start_new_session=True)
        try:
            while True:
                stop_reason = budget_stop() if budget_stop else None
                if stop_reason:
                    if stop_reason == 'response_output_limit_reached':
                        # The full provider response is already accounted. Let
                        # DSH flush its normal max-tokens exit/receipt before
                        # reaping it; the gate refuses any further requests.
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
                    raise request_contract.BudgetExhausted('Task budget exhausted')
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError('Batch cancelled; preserve attempt without retry')
                try:
                    code = child.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if code:
                raise RuntimeError('Harness failed; raw outputs preserved')
        except BaseException:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            raise

