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
import benchmark_config
import cache

ROOT, BENCH = records.ROOT, records.BENCH


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
        if experiment['role'] != 'primary' or not experiment.get('enabled', True) or (suite != 'all' and experiment['suite'] != suite):
            continue
        if experiment_id and experiment['id'] != experiment_id:
            continue
        effective = records.condition_catalog()['active_case_conditions'].get(experiment['suite']) if condition == 'active' else condition
        registered = next(c for c in records.condition_catalog()['conditions'] if c['id'] == effective)
        if effective != 'legacy-recorded' and experiment['suite'] not in registered.get('suites', []):
            continue
        if experiment['suite'] in registered.get('retired_suites', []):
            continue
        for case in records.frozen_cases(experiment):
            if not case.get('enabled', True):
                continue
            directory = records.attempt_directory(experiment['id'], case['id'], model, effective) if model else None
            if directory and (directory / 'grade.json').exists() and (directory / 'result.json').exists():
                status = 'cached'
            elif directory and (directory / 'result.json').exists():
                status = 'saved_inference; offline_grading_pending'
            elif directory and any((directory / name).exists() for name in ['events.jsonl', 'zcode-events.jsonl', 'task.json', 'harness-failure.json', 'interrupted.json']):
                status = 'interrupted; duplicate_inference_refused'
            else:
                status = 'not_started'
            selected.append({'experiment': experiment['id'], 'suite': experiment['suite'],
                             'case': case['id'], 'language': case['language'], 'model': model, 'condition': effective,
                             'status': status, 'directory': str(directory.relative_to(ROOT)) if directory else None,
                             'cached_environment_profile': records.read(directory / 'result.json', {}).get('environment_profile', {}).get('id', 'legacy-recorded') if directory else None})
    return selected


def prepare_task(entry, model, materialize=True):
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
    manifest_bytes = records.read_bytes(public / 'manifest.json')
    identity = cache.inference_identity(entry, model, case, profile, prompt, binary, tools, manifest_bytes, config)
    fingerprint = cache.digest(identity)
    workspace = ROOT / '.cache/benchmark-runtime/tasks' / fingerprint
    if materialize:
        workspace.mkdir(parents=True, exist_ok=True)
        for name, data in [('target', binary), ('prompt.txt', prompt), ('tools.json', tools), ('manifest.json', manifest_bytes)]:
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
            'cache_schema': 2, 'inference_identity': identity, 'fingerprint': fingerprint, 'condition': entry.get('condition', 'legacy-recorded'), 'environment_profile': profile, 'prompt_sha256': hashlib.sha256(prompt).hexdigest(),
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


def launch_model(directory, task, model, cancel_event=None):
    dispatcher = load_module(BENCH / 'harnesses/dispatch.py', 'benchmark_dispatch')
    return dispatcher.launch(directory, task, model, cancel_event, records=records)


def execute(entry, model, cancel_event=None, on_launch=None):
    if cancel_event and cancel_event.is_set():
        raise InterruptedError('Batch cancelled; no new task started')
    directory = ROOT / entry['directory']
    if entry['status'] == 'cached':
        hit = cache.validate(entry, model, prepare_task(entry, model, materialize=False))
        print('CACHE HIT:', entry['experiment'], entry['case'], model['id'], '· original receipt retained', flush=True)
        return {**hit, 'provider_launches': 0}
    if entry['status'].startswith('interrupted'):
        raise RuntimeError('Interrupted attempt preserved; no automatic retry')
    if entry['status'] == 'not_started' and os.environ.get('REA_BENCHMARK_OFFLINE') == '1':
        raise RuntimeError('Offline benchmark: missing inference cache; provider launch refused')
    launched = 0
    task = prepare_task(entry, model)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'result.json').exists():
        saved = records.read(directory / 'task.json')
        for key in ['model', 'harness', 'reasoning', 'condition', 'binary_sha256', 'prompt_sha256', 'max_tool_calls', 'timeout_seconds', 'max_output_tokens']:
            if not saved or saved.get(key) != task.get(key):
                raise ValueError('Saved inference differs from selected inputs; preserve without retry')
        task = saved
    if not (directory / 'result.json').exists():
        claim = ROOT / '.cache/benchmark-attempt-registry' / ('shared-' + task['fingerprint'] + '.json')
        save(claim, {'fingerprint': task['fingerprint'], 'directory': str(directory)}, True)
        save(directory / 'task.json', task, True)
        print('START:', entry['experiment'], entry['case'], model['id'], flush=True)
        if on_launch:
            on_launch()
        launch_model(directory, task, model, cancel_event)
        launched = 1
    if cancel_event and cancel_event.is_set():
        save(directory / 'interrupted.json', {'reason': 'Batch cancelled; no automatic inference retry',
            'model_correctness_grade_allowed': False, 'stopped_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        raise InterruptedError('Batch cancelled before grading')
    grade = grade_saved(directory, task)
    print('GRADED:', entry['case'], grade['passed'], '/', grade['total'], flush=True)
    complete = {'fingerprint': task['fingerprint'], 'environment_profile': task['environment_profile'],
                'grading_key': cache.digest(cache.grading_identity(task, directory)), 'files_sha256': {
        str(path.relative_to(directory)): records.sha(path) for path in sorted(directory.rglob('*')) if path.is_file()}}
    save(directory / 'completed.json', complete, True)
    return {'status': 'inferred' if launched else 'graded_saved_inference', 'provider_launches': launched, 'fingerprint': task['fingerprint']}


def selected_entries(selection):
    model_ids = selection['models']
    if model_ids == 'all':
        model_ids = [m for m, spec in records.models().items() if spec.get('enabled', True)]
    return [entry for mid in model_ids for entry in plan(model=mid, condition=selection['condition'])
            if benchmark_config.accepts(selection, entry)]


def run_batch(selected, selection, offline=False, report_every=0, finalize=None):
    """One batch lock, one sequential queue per model, no retry or cache bypass."""
    offline = offline or os.environ.get('REA_BENCHMARK_OFFLINE') == '1'
    if offline:
        os.environ['REA_BENCHMARK_OFFLINE'] = '1'
    lock = ROOT / '.cache/benchmark-runtime/live-batch.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError('Another batch owns live-batch.lock; duplicate execution refused')
    model_ids = list(dict.fromkeys(e['model'] for e in selected))
    os.write(descriptor, json.dumps({'pid': os.getpid(), 'models': model_ids}).encode())
    os.close(descriptor)
    cancel = threading.Event()
    live = any(e['status'] == 'not_started' for e in selected) and not offline
    outcomes, failures = [], []
    launches = 0
    launch_lock = threading.Lock()
    def on_launch():
        nonlocal launches
        with launch_lock:
            launches += 1
    def interrupted(signum, frame):
        cancel.set()
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    if live:
        set_execution_stopped(False)
        def observe_stop_state():
            while not cancel.wait(1):
                if records.read(BENCH / 'config/execution.json')['experiments_stopped']:
                    cancel.set()
                    return
        threading.Thread(target=observe_stop_state, daemon=True).start()
    try:
        queues = {mid: iter([e for e in selected if e['model'] == mid]) for mid in model_ids}
        with ThreadPoolExecutor(max_workers=selection['execution']['max_parallel_models']) as workers:
            pending = {}
            def submit(mid):
                entry = next(queues[mid], None) if not cancel.is_set() else None
                if entry:
                    pending[workers.submit(execute, entry, records.models()[mid], cancel, on_launch)] = (mid, entry)
            for mid in model_ids:
                submit(mid)
            while pending:
                future = next(as_completed(pending))
                mid, entry = pending.pop(future)
                try:
                    result = future.result()
                    outcomes.append({'experiment': entry['experiment'], 'case': entry['case'], 'model': mid,
                                     'condition': entry['condition'], **result})
                except Exception as error:
                    failures.append({'model': mid, 'case': entry['case'], 'error': str(error)})
                    print('MODEL QUEUE STOPPED:', mid, entry['case'], str(error), flush=True)
                else:
                    submit(mid)
                if report_every and len(outcomes) % report_every == 0:
                    import report
                    report.build()
        receipt = {'schema_version': 1, 'selection': selection, 'selected_tasks': len(selected),
                   'cache_hits': sum(item['status'] == 'cache_hit' for item in outcomes),
                   'provider_launches': launches, 'model_api_calls': 0 if launches == 0 else None,
                   'offline': offline, 'outcomes': sorted(outcomes, key=lambda x: (x['experiment'], x['case'], x['model'])),
                   'failures': failures, 'unprocessed_tasks': len(selected) - len(outcomes) - len(failures)}
        save(BENCH / 'config/benchmark-status.json', receipt)
        print('BENCHMARK:', len(outcomes), '/', len(selected), '| cache hits:', receipt['cache_hits'],
              '| provider launches:', launches, '| failures:', len(failures), flush=True)
    finally:
        cancel.set()
        if live:
            set_execution_stopped(True)
        signal.signal(signal.SIGTERM, previous)
        try:
            if finalize:
                finalize()
        finally:
            lock.unlink(missing_ok=True)
    if failures or receipt['unprocessed_tasks']:
        raise RuntimeError('Benchmark incomplete; saved attempts preserved; see global report and benchmark-status.json')
    return receipt


def finalize():
    import archive_profiles
    import audit_condition
    import report
    archive_profiles.main()
    audit_condition.main()
    report.build()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'plan', 'run', 'verify'], nargs='?', default='list')
    parser.add_argument('--config', type=Path, default=benchmark_config.DEFAULT)
    parser.add_argument('--suite', choices=['all', *[s['id'] for s in records.registry()['suites']]])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--model', choices=[*records.models(), 'all'])
    group.add_argument('--models', nargs='+', choices=list(records.models()))
    parser.add_argument('--harness', choices=['deepseek-harness', 'zcode'])
    parser.add_argument('--experiment')
    parser.add_argument('--condition', choices=['active', *[c['id'] for c in records.condition_catalog()['conditions']]])
    parser.add_argument('--dry-run', '--cached-only', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Refuse every missing inference cache before credentials or provider launch')
    parser.add_argument('--resume-experiments', action='store_true')
    parser.add_argument('--report-every', type=int)
    args = parser.parse_args()
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
        if args.report_every is not None:
            if args.report_every < 0:
                raise ValueError('--report-every must be nonnegative')
            selection['execution']['report_every'] = args.report_every
        selected = selected_entries(selection)
        if not selected:
            raise ValueError('Selection contains no registered active cases')
        if args.harness and any(records.models()[e['model']]['harness'] != args.harness for e in selected):
            raise ValueError('Selected model is registered to a different harness')
        if args.action in ['list', 'plan'] or args.dry_run:
            print(json.dumps({'experiments_stopped': records.read(BENCH / 'config/execution.json')['experiments_stopped'],
                              'selection': selection, 'tasks': selected, 'model_calls': 0,
                              'note': 'Cache status is preliminary; execution validates all committed artifacts and effective inputs.'}, indent=2))
            return
        if args.action == 'verify':
            verify_zcode(selected)
            return
        if records.read(BENCH / 'config/execution.json')['experiments_stopped'] and not args.resume_experiments and not args.offline:
            parser.error('Experiments remain stopped. Explicit --resume-experiments is required for execution; --offline forbids provider launches.')
        run_batch(selected, selection, offline=args.offline, report_every=selection['execution']['report_every'], finalize=finalize)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, str(error) + '\n')


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
