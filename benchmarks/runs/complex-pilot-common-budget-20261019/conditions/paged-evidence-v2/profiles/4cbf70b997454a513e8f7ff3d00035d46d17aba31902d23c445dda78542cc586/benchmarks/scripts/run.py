#!/usr/bin/python3
"""One coordinator for existing suites and interchangeable harness adapters."""
import argparse
import datetime
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
from concurrent.futures import ThreadPoolExecutor, as_completed

import records
import environment_profile

ROOT, BENCH = records.ROOT, records.BENCH


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def save(path, value, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def set_execution_stopped(stopped):
    path = BENCH / 'config/execution.json'
    state = json.loads(path.read_text())
    state['experiments_stopped'] = stopped
    state['reason'] = 'Explicit batch running' if not stopped else 'Batch ended or interrupted; no automatic restart'
    if stopped:
        state['last_stop_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(path, state)


def plan(suite='all', model=None, experiment_id=None, condition='legacy-recorded'):
    selected = []
    for experiment in records.registry()['experiments']:
        if experiment['role'] != 'primary' or (suite != 'all' and experiment['suite'] != suite):
            continue
        if experiment_id and experiment['id'] != experiment_id:
            continue
        registered = next(c for c in records.condition_catalog()['conditions'] if c['id'] == condition)
        if condition != 'legacy-recorded' and experiment['suite'] not in registered.get('suites', []):
            continue
        if experiment['suite'] in registered.get('retired_suites', []):
            continue
        for case in records.frozen_cases(experiment):
            directory = records.attempt_directory(experiment['id'], case['id'], model, condition) if model else None
            if directory and (directory / 'grade.json').exists() and (directory / 'result.json').exists():
                status = 'cached'
            elif directory and (directory / 'result.json').exists():
                status = 'saved_inference; offline_grading_pending'
            elif directory and any((directory / name).exists() for name in ['events.jsonl', 'zcode-events.jsonl', 'task.json', 'harness-failure.json', 'interrupted.json']):
                status = 'interrupted; duplicate_inference_refused'
            else:
                status = 'not_started'
            selected.append({'experiment': experiment['id'], 'suite': experiment['suite'],
                             'case': case['id'], 'language': case['language'], 'model': model, 'condition': condition,
                             'status': status, 'directory': str(directory.relative_to(ROOT)) if directory else None,
                             'cached_environment_profile': records.read(directory / 'result.json', {}).get('environment_profile', {}).get('id', 'legacy-recorded') if directory else None})
    return selected


def prepare_task(entry, model):
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
    paging = records.read(BENCH / 'config/evidence-paging.json') if condition == 'paged-evidence-v2' else None
    if paging:
        # Share exactly one public paging schema across both provider adapters.
        catalog = json.loads(tools)
        for tool in catalog['tools']:
            # The transport must accept both ordinary Evidence and its explicit
            # paging envelope; the original response schema requires Evidence-only fields.
            tool['outputSchema'] = {'type': 'object', 'additionalProperties': True,
                'description': 'Complete REA Evidence or an explicit lossless paging envelope with a reachable read_evidence reference.'}
        descriptor = subprocess.check_output([str(ROOT/'.tools/node-v24.22.0-linux-x64/bin/node'), '--input-type=module', '-e',
            "import {evidenceTool} from './benchmarks/scripts/evidence-paging.mjs'; process.stdout.write(JSON.stringify(evidenceTool));"], cwd=ROOT)
        catalog['tools'].append(json.loads(descriptor))
        tools = json.dumps(catalog, sort_keys=True, indent=2).encode()
    profile = environment_profile.current(model['harness'], condition)
    fingerprint = hashlib.sha256(json.dumps({'experiment': experiment['id'], 'case': case['id'], 'model': model,
        'environment_profile': profile, 'condition': entry.get('condition', 'legacy-recorded'),
        'prompt': hashlib.sha256(prompt).hexdigest(), 'binary': hashlib.sha256(binary).hexdigest(),
        'tools': hashlib.sha256(tools).hexdigest(), 'limits': [case['max_tool_calls'], case['timeout_seconds'], case['max_output_tokens']]}, sort_keys=True).encode()).hexdigest()
    workspace = ROOT / '.cache/benchmark-runtime/tasks' / fingerprint
    workspace.mkdir(parents=True, exist_ok=True)
    for name, data in [('target', binary), ('prompt.txt', prompt), ('tools.json', tools),
                       ('manifest.json', records.read_bytes(public / 'manifest.json'))]:
        destination = workspace / name
        if destination.exists():
            assert destination.read_bytes() == data, 'Frozen runtime input changed'
        else:
            destination.write_bytes(data)
    gateway = {'max_tool_calls': case['max_tool_calls'], 'tool_timeout_ms': config['tool_timeout_ms'],
               'analysis_tools_file': str(workspace / 'tools.json'), 'data_limits': config.get('data_limits', {}),
               **({'evidence_paging': paging} if paging else {})}
    save(workspace / 'gateway.json', gateway)
    save(workspace / 'harness-config.json', {**config, 'analysis_tools_file': str(workspace / 'tools.json'), **({'evidence_paging': paging} if paging else {})})
    details = records.read(public / 'manifest.json')
    track = 'basic' if experiment['suite'] == 'record-parser' else 'complex' if experiment['suite'] == 'complex-native-v1' else 'stl' if case['language'] == 'cpp' else 'core'
    return {**case, 'tool_count': case['tool_count'] + int(bool(paging)), 'id': case['id'], 'model': model['id'], 'harness': model['harness'],
            'label': model['label'], 'reasoning': model['reasoning'], 'track': track,
            'input_directory': str(workspace), 'directory': entry['directory'],
            'fingerprint': fingerprint, 'condition': entry.get('condition', 'legacy-recorded'), 'environment_profile': profile, 'prompt_sha256': hashlib.sha256(prompt).hexdigest(),
            'binary_sha256': hashlib.sha256(binary).hexdigest(),
            'standard': details.get('standard', 'c++20' if case['language'] == 'cpp' else '2021'),
            'string_abi': details.get('string_abi')}


def grade_saved(directory, task):
    if (directory / 'harness-failure.json').exists():
        raise RuntimeError('Harness/environment failure preserved; it cannot receive a model correctness grade')
    if (directory / 'grade.json').exists():
        return records.read(directory / 'grade.json')
    if task['track'] in ['stl', 'core']:
        grader = load_module(BENCH / 'suites/stl-native-v2/scripts/grade-framed.py', 'framed')
        answer = (directory / 'answer.md').read_text()
        fence = chr(96) * 3
        blocks = re.findall(r'^' + fence + r'([^\n]*)\n(.*?)^' + fence + r'\s*$', answer, re.M | re.S)
        code = [value for tag, value in blocks if re.fullmatch('cpp|c\\+\\+' if task['language'] == 'cpp' else 'rust|rs', tag.strip(), re.I)]
        cases, vector_sha = grader.frozen_cases(task['track'])
        if len(blocks) == 1 and len(code) == 1:
            source = directory / ('reconstructed.' + ('cpp' if task['language'] == 'cpp' else 'rs'))
            source.write_text(code[0])
            result = grader.evaluate(code[0], task['language'], cases, task['standard'], task['string_abi'], directory / 'grade-artifacts')
        else:
            result = {'compiled': False, 'passed': 0, 'total': len(cases), 'failure': 'Expected one native code block',
                      'program_success': False, 'active_points_passed': 0,
                      'active_point_count': 6 if task['track'] == 'core' else 21 if task['standard'] == 'c++11' else 22}
        result['vectors_sha256'] = vector_sha
        save(directory / 'grade.json', result)
        return result
    path = BENCH / ('scripts/grade.py' if task['track'] == 'basic' else 'suites/complex-native-v1/scripts/grade.py')
    return load_module(path, 'native_grader_entry').grade(directory, task['language'])


def execute(entry, model, cancel_event=None):
    if cancel_event and cancel_event.is_set():
        return
    directory = ROOT / entry['directory']
    if entry['status'] == 'cached':
        grade = records.read(directory / ('grade-framed.json' if (directory / 'grade-framed.json').exists() else 'grade.json'))
        extension = {'cpp': 'cpp', 'rust': 'rs', 'go': 'go'}[entry['language']]
        if grade.get('candidate_source_sha256'):
            assert records.sha(directory / ('reconstructed.' + extension)) == grade['candidate_source_sha256']
        if entry.get('condition', 'legacy-recorded') != 'legacy-recorded':
            saved = records.read(directory / 'task.json')
            expected = prepare_task(entry, model)
            assert saved['fingerprint'] == expected['fingerprint'], 'Cached condition has a different environment; refuse to relabel or rerun it'
        print('CACHE HIT:', entry['experiment'], entry['case'], model['id'], '· recorded environment:',
              entry.get('cached_environment_profile', 'legacy-recorded'), '· no corrected-condition inference', flush=True)
        return
    if entry['status'].startswith('interrupted'):
        print('PRESERVED INTERRUPTED ATTEMPT:', entry['case'], '· no automatic retry', flush=True)
        return
    task = prepare_task(entry, model)
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / 'result.json').exists():
        claim = ROOT / '.cache/benchmark-attempt-registry' / ('shared-' + task['fingerprint'] + '.json')
        save(claim, {'fingerprint': task['fingerprint'], 'directory': str(directory)}, True)
        save(directory / 'task.json', task, True)
        print('START:', entry['experiment'], entry['case'], model['id'], flush=True)
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
    if cancel_event and cancel_event.is_set():
        save(directory / 'interrupted.json', {'reason': 'Batch cancelled; no automatic inference retry',
            'model_correctness_grade_allowed': False, 'stopped_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        raise InterruptedError('Batch cancelled before grading')
    grade = grade_saved(directory, task)
    print('GRADED:', entry['case'], grade['passed'], '/', grade['total'], flush=True)
    complete = {'fingerprint': task['fingerprint'], 'environment_profile': task['environment_profile'], 'files_sha256': {
        str(path.relative_to(directory)): records.sha(path) for path in sorted(directory.rglob('*')) if path.is_file()}}
    save(directory / 'completed.json', complete, True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'run', 'verify'], nargs='?', default='list')
    parser.add_argument('--suite', default='all', choices=['all', 'record-parser', 'complex-native-v1', 'stl-native-v2'])
    parser.add_argument('--model', choices=[*records.models(), 'all'])
    parser.add_argument('--harness', choices=['deepseek-harness', 'zcode'])
    parser.add_argument('--experiment')
    parser.add_argument('--condition', default='legacy-recorded', choices=[c['id'] for c in records.condition_catalog()['conditions']])
    parser.add_argument('--dry-run', '--cached-only', action='store_true')
    parser.add_argument('--resume-experiments', action='store_true')
    args = parser.parse_args()
    selected_models = list(records.models()) if args.model == 'all' else [args.model]
    selected = [entry for model_id in selected_models for entry in plan(args.suite, model_id, args.experiment, args.condition)]
    if args.action == 'list' or args.dry_run:
        print(json.dumps({'experiments_stopped': records.read(BENCH / 'config/execution.json')['experiments_stopped'],
                          'suites': records.registry()['suites'], 'models': list(records.models().values()),
                          'tasks': selected, 'model_calls': 0}, indent=2))
        return
    if args.action == 'verify':
        verify_zcode(selected)
        return
    if records.read(BENCH / 'config/execution.json')['experiments_stopped'] and not args.resume_experiments:
        parser.error('Experiments remain stopped. Use --dry-run to inspect existing caches. Explicit --resume-experiments is required for execution.')
    if not args.model:
        parser.error('Specify --model; model execution is never inferred from a report command.')
    model = records.models().get(args.model)
    if args.harness and (not model or args.harness != model['harness']):
        parser.error('Selected model is registered to a different harness.')
    import report
    # A single coordinator owns stop state and report writes for all model workers.
    lock = ROOT / '.cache/benchmark-runtime/live-batch.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        parser.error('Another batch owns live-batch.lock; inspect it before starting duplicate inference')
    os.write(descriptor, json.dumps({'pid': os.getpid(), 'condition': args.condition, 'models': selected_models}).encode())
    os.close(descriptor)
    cancel = threading.Event()
    def interrupted(signum, frame):
        cancel.set()
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    set_execution_stopped(False)
    def observe_stop_state():
        while not cancel.wait(1):
            try:
                if records.read(BENCH / 'config/execution.json')['experiments_stopped']:
                    cancel.set()
                    return
            except (OSError, ValueError):
                pass
    threading.Thread(target=observe_stop_state, daemon=True).start()
    try:
        if args.model != 'all':
            for entry in selected:
                try:
                    execute(entry, model)
                finally:
                    report.build()
        else:
            # One active task per model; a model's original case order is retained.
            queues = {m: iter([e for e in selected if e['model'] == m]) for m in selected_models}
            failures = []
            with ThreadPoolExecutor(max_workers=3) as workers:
                pending = {}
                def submit(model_id):
                    if cancel.is_set():
                        return
                    entry = next(queues[model_id], None)
                    if entry:
                        pending[workers.submit(execute, entry, records.models()[model_id], cancel)] = (model_id, entry)
                for model_id in selected_models:
                    submit(model_id)
                while pending:
                    future = next(as_completed(pending))
                    model_id, entry = pending.pop(future)
                    try:
                        future.result()
                    except Exception as error:
                        if cancel.is_set():
                            save(ROOT / entry['directory'] / 'interrupted.json', {'reason': 'Batch cancelled',
                                'stopped_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'model_correctness_grade_allowed': False})
                        failures.append({'model': model_id, 'case': entry['case'], 'error': str(error)})
                        print('ATTEMPT NEEDS ATTENTION:', model_id, entry['case'], str(error), flush=True)
                        print('MODEL QUEUE STOPPED:', model_id, '· remaining cases preserved as unstarted', flush=True)
                    else:
                        submit(model_id)
                    report.build()
            save(ROOT / '.cache/benchmark-runtime/last-batch.json', {'condition': args.condition, 'failures': failures})
    finally:
        cancel.set()
        set_execution_stopped(True)
        signal.signal(signal.SIGTERM, previous)
        lock.unlink(missing_ok=True)
        report.build()


def verify_zcode(selected):
    backend = load_module(BENCH / 'harnesses/zcode.py', 'zcode_backend')
    model = records.models()['glm-5.3']
    checks = []
    validation_root = ROOT / '.cache/benchmark-runtime/adapter-validation'
    for experiment_id, case_id in [('native-pilot-20261019', 'cpp'), ('stl-rust-data-v2-20261019', 'rust-data-v2')]:
        entry = next(x for x in plan(model='glm-5.3') if x['experiment'] == experiment_id and x['case'] == case_id)
        task = prepare_task(entry, model)
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
    main()
