"""Provider dispatch only; source hash is part of inference identity."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / 'benchmarks'


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


def launch(directory, task, model, cancel_event=None, records=None):
    if os.environ.get('REA_BENCHMARK_OFFLINE') == '1':
        raise RuntimeError('Offline benchmark forbids provider launches')
    if model['harness'] == 'zcode':
        backend = load_module(BENCH / 'harnesses/zcode.py', 'zcode_backend')
        private = json.loads((ROOT / '.zcode-state/.zcode/v2/provider_config.json').read_text())
        provider = next(x['config'] for x in private['config']['providerConfigRules']['providerRules'] if x['providerId'] == 'zai-prepaid')
        raw = backend.launch(directory, task, (provider['api']['baseUrl'], provider['access']['apiKey']), cancel_event=cancel_event)
        if raw['policy_violations'] or raw['upstream_error'] or (raw['exit_code'] != 0 and not raw['timed_out']):
            save(directory / 'harness-failure.json', {**raw, 'environment_profile': task['environment_profile'],
                'classification': 'harness_or_provider_failure', 'model_correctness_grade_allowed': False}, True)
            raise RuntimeError('ZCode/provider failed: preserved raw traces; no model correctness grade')
        answer, provider_models = backend.collect_wire(directory)
        observations = {x['sequence']: x for x in raw['usage_observations_raw']}
        usage, normalized = records.normalize_glm_usage(list(observations.values()))
        trace = records.read_trace(directory / 'tool-calls.jsonl')
        result = {**raw, 'model': model['id'], 'model_label': model['label'], 'harness': 'ZCode',
                  'harness_version': '0.17.0', 'provider': 'bigmodel-standard-api', 'reasoning': model['reasoning'],
                  'environment_profile': task['environment_profile'], 'condition': task['condition'],
                  'language': task['language'], 'output_language': task['language'],
                  'case_id': task['id'], 'binary_sha256': task['binary_sha256'], 'prompt_sha256': task['prompt_sha256'],
                  'reported_usage': usage, 'usage_observations': normalized, 'provider_models': provider_models,
                  'tool_calls': len(trace), 'tool_errors': sum(x['is_error'] for x in trace),
                  'explicit_close': any(x['tool'] == 'close_binary' and not x['is_error'] for x in trace),
                  'actual_cost_usd': None}
        save(directory / 'inference.json', result, True)
        save(directory / 'result.json', result, True)
    else:
        environment = {**os.environ, 'REA_EXPLICIT_RUN_AUTHORIZED': '1',
                       'REA_BENCHMARK_CONFIG': str(Path(task['input_directory']) / 'harness-config.json')}
        child = subprocess.Popen([str(ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'),
            str(BENCH / 'harnesses/deepseek-harness.mjs'), '--task', str(directory / 'task.json')],
            env=environment, start_new_session=True)
        try:
            while True:
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

