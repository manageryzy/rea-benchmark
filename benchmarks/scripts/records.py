"""Read-only benchmark records, shared by orchestration and report generation."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import usage
from grading import store as grading_store

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / 'benchmarks'
INPUTS = {}


def read_bytes(path):
    path = Path(path)
    data = path.read_bytes()
    INPUTS[str(path.relative_to(ROOT))] = hashlib.sha256(data).hexdigest()
    return data


def read(path, default=None):
    path = Path(path)
    return json.loads(read_bytes(path)) if path.exists() else default


def failure_marker(path):
    """Preserve an interrupted diagnostic write as invalid evidence, never success.

    Only failure markers are recoverable for reporting. Results, grades, action
    identities and other JSON still fail closed through read().
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        value = read(path)
        if isinstance(value, dict) and value:
            return value
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {'classification': 'malformed_failure_receipt',
            'model_correctness_grade_allowed': False}


def sha(path):
    return hashlib.sha256(read_bytes(path)).hexdigest()


def registry():
    return read(BENCH / 'config/experiments.json')


def models():
    return {item['id']: item for item in read(BENCH / 'config/models.json')['models']}


def frozen_cases(experiment):
    base = BENCH / 'runs' / experiment['id']
    manifest = read(base / 'run-manifest.json')
    config = manifest['configuration']
    cases = config.get('cases') or [{'id': language, 'language': language} for language in config['languages']]
    if experiment['role'] == 'diagnostic':
        existing = {path.parent.name for model in config['models'] for path in (base / model['id']).glob('*/result.json')}
        cases = [case for case in cases if case['id'] in existing]
    result = []
    imports = registry()['imported_attempts']
    for case in cases:
        imported = next((x for x in imports if x['experiment'] == experiment['id'] and x['case'] == case['id']), None)
        extra = {}
        if imported:
            source = read(ROOT / imported['manifest'])
            extra = next(x for x in source['cases'] if x['id'] == imported['manifest_case'])
        result.append({**case, 'experiment': experiment['id'], 'suite': experiment['suite'],
                       'label': extra.get('id', case['id']),
                       'tool_count': extra.get('tool_count', 12 if config.get('analysis_tools_file') else 8),
                       'max_tool_calls': config['max_tool_calls'],
                       'timeout_seconds': config['task_timeout_seconds'],
                       'max_output_tokens': config['max_output_tokens'],
                       'standard': extra.get('standard', case.get('standard')),
                       'string_abi': extra.get('string_abi', case.get('string_abi'))})
    from native_case_registry import extensions
    return result + extensions(experiment, result)


def public_directory(experiment, case):
    """Use explicitly registered immutable inputs, then the original frozen fixtures."""
    if case.get('native_case_origin') == 'compiler_matrix':
        from native_case_registry import public_directory as registered_directory
        return registered_directory(case)
    base = BENCH / 'runs' / experiment['id'] / 'inputs/public' / case['id']
    if (base / 'target').exists():
        return base
    return BENCH / 'suites' / experiment['suite'] / 'public' / case['language']


def imported_attempt(experiment, case, model):
    return next((x for x in registry()['imported_attempts']
                 if (x['experiment'], x['case'], x['model']) == (experiment, case, model)), None)


def attempt_directory(experiment, case, model, condition='legacy-recorded'):
    if condition != 'legacy-recorded':
        return BENCH / 'runs' / experiment / 'conditions' / condition / model / case
    standard = BENCH / 'runs' / experiment / model / case
    if standard.exists():
        return standard
    imported = imported_attempt(experiment, case, model)
    legacy = ROOT / imported['directory'] if imported else None
    return legacy if legacy and legacy.exists() else standard


def normalize_glm_usage(observations):
    result = {'inputTokens': 0, 'cacheReadTokens': 0, 'outputTokens': 0, 'totalTokens': 0}
    normalized = []
    for observation in observations:
        raw = observation.get('raw_usage', observation.get('usage', {}))
        cached = (raw.get('prompt_tokens_details') or {}).get('cached_tokens', 0) or 0
        usage = {'inputTokens': raw.get('prompt_tokens', 0) - cached, 'cacheReadTokens': cached,
                 'outputTokens': raw.get('completion_tokens', 0), 'totalTokens': raw.get('total_tokens', 0)}
        for key, value in usage.items():
            result[key] += value
        normalized.append({**{key: observation[key] for key in ('sequence', 'request_id') if key in observation},
                           'finished_at': observation.get('finished_at'), 'usage': usage, 'raw_usage': raw})
    return result, normalized


def partial_wire_receipt(directory, case, stopped):
    """Derive observed usage from saved SSE files without changing raw artifacts."""
    observations = []
    requests = sorted((directory / 'wire').glob('request-*.json'))
    responses = sorted((directory / 'wire').glob('response-*.sse'))
    for path in responses:
        last = None
        for line in read_bytes(path).splitlines():
            if not line.startswith(b'data:'):
                continue
            try:
                event = json.loads(line[5:])
            except (ValueError, UnicodeError):
                continue
            if event.get('usage'):
                last = event
        if last:
            observations.append({'sequence': int(re.search(r'(\d+)', path.stem)[1]),
                                 'finished_at': stopped.get('stopped_at'),
                                 'raw_usage': last['usage'], 'model': last.get('model')})
    usage, normalized = normalize_glm_usage(observations)
    trace = read_trace(directory / 'tool-calls.jsonl')
    return {'model': 'glm-5.3', 'harness': 'ZCode', 'harness_version': '0.17.0',
            'language': case['language'], 'output_language': case['language'],
            'reported_usage': usage, 'usage_observations': normalized,
            'request_count': len(requests), 'reported_request_count': len(observations),
            'usage_complete': False, 'derived_from_raw_cache': True,
            'tool_calls': len(trace), 'tool_errors': sum(bool(x['is_error']) for x in trace),
            'explicit_close': any(x['tool'] == 'close_binary' and not x['is_error'] for x in trace),
            'completed_at': stopped.get('stopped_at'), 'actual_cost_usd': None}


def read_trace(path):
    if not path.exists():
        return []
    result = []
    for line in read_bytes(path).splitlines():
        try:
            result.append(json.loads(line))
        except (ValueError, UnicodeError):
            pass
    return result


def timing_evidence(receipt, directory):
    requests = (receipt or {}).get('requests', [])
    if not requests:
        return None
    completed = [x for x in requests if x.get('started_at') and x.get('finished_at')]
    seconds = sum((datetime.datetime.fromisoformat(x['finished_at'].replace('Z', '+00:00')) -
                   datetime.datetime.fromisoformat(x['started_at'].replace('Z', '+00:00'))).total_seconds()
                  for x in completed)
    trace = read_trace(directory / 'tool-calls.jsonl')
    measured_tools = [x['elapsed_ms'] for x in trace if x.get('elapsed_ms') is not None]
    return {'completed_request_seconds': seconds, 'completed_requests': len(completed),
            'requests_without_end_time': len(requests) - len(completed),
            'tool_seconds_sum': sum(measured_tools) / 1000 if measured_tools else None,
            'measured_tool_calls': len(measured_tools)}


def glm_cost(receipt, pricing):
    receipt, accounting = usage.derive(receipt)
    lower, upper, cache_known = 0.0, 0.0, True
    observations = receipt.get('usage_observations', [])
    for observation in observations:
        raw = observation.get('raw_usage') or {}
        normalized = observation['usage']
        cached = normalized.get('cacheReadTokens')
        prompt = normalized.get('inputTokens', raw.get('prompt_tokens', 0)) + (cached or 0)
        output = normalized.get('outputTokens', 0)
        if cached is None:
            cache_known = False
            lower += prompt * pricing['cache_read'] + output * pricing['output']
            upper += prompt * pricing['input'] + output * pricing['output']
        else:
            cost = (prompt - cached) * pricing['input'] + cached * pricing['cache_read'] + output * pricing['output']
            lower += cost
            upper += cost
    lower, upper = lower / pricing['unit_tokens'], upper / pricing['unit_tokens']
    fx = pricing['fx']['cny_per_usd']
    complete = accounting['usage_complete']
    return usage.annotate_cost({'low_usd': lower / fx if observations else None,
            'high_usd': upper / fx if observations else None,
            'low_cny': lower if observations else None, 'high_cny': upper if observations else None,
            'cache_breakdown_known': cache_known, 'usage_complete': complete,
            'actual_billed_usd': None, 'actual_billed_cny': None, 'currency': 'CNY',
            'note': 'Observed usage estimate; incomplete streams can have additional unreported cost.' if not complete else
                    'Published-rate estimate; actual prepaid invoice debit unavailable.'}, accounting)


def selected_grade(directory):
    grade, name = grading_store.read_selected(directory, read=read, file_sha=sha)
    if grade is not None:
        original_name = 'grade-framed.json' if (directory / 'grade-framed.json').exists() else 'grade.json'
        original = read(directory / original_name, {})
        grade = {**grade, 'original_passed': original.get('passed'), 'original_total': original.get('total'),
                 'original_program_success': original.get('program_success', bool(original.get('compiled') and
                    original.get('passed') == original.get('total') and not original.get('failure')))}
        return grade, name
    name = 'grade-framed.json' if (directory / 'grade-framed.json').exists() else 'grade.json'
    return read(directory / name), name


def candidate_source(directory, language, grade):
    filename = 'reconstructed.' + {'c': 'c', 'cpp': 'cpp', 'rust': 'rs', 'go': 'go'}[language]
    source = directory / filename
    if not source.exists() and grade and grade.get('grading_id'):
        selection = read(directory / 'selected-grade.json')
        extension = filename.split('.')[-1]
        source = (directory / selection['path']).parent / 'artifacts' / ('candidate.' + extension)
    return source if source.exists() else None


def measured_cost(receipt, prices):
    from pricing import estimate
    derived, accounting = usage.derive(receipt)
    return usage.annotate_cost(estimate(derived, prices), accounting)


def readability(experiment, model, case, source):
    base = BENCH / 'runs' / experiment
    for path in [base / 'readability-reviews.json', base / 'analysis/basic-readability-reviews.json',
                 base / 'analysis/additional-readability-reviews.json']:
        data = read(path, {})
        reviews = data.get('reviews', {})
        review = reviews.get(model + '/' + case)
        if review is None and source:
            review = reviews.get(str(source.relative_to(ROOT)))
        if review:
            assert not source or sha(source) == review['candidate_source_sha256'], 'Readability review source changed'
            return review
    return None


def condition_catalog():
    return read(BENCH / 'config/conditions.json', {'conditions': [], 'active_comparison': 'legacy-recorded'})


def request_budget_state(directory, status):
    """A complete candidate's correctness and spending stop are separate facts."""
    if status in ('environment_failure', 'interrupted', 'not_started', 'running'):
        return status
    import report_budget
    task = read(directory / 'task.json', {}) or {}
    certificate = read(directory / 'request-budget.json', {}) or {}
    value = report_budget.summarize(task, certificate).get('outcome')
    if value and value['status'] == 'completed_over_budget':
        return 'completed_over_budget'
    if value and value['queue_stop_required'] and status == 'graded':
        return 'completed_queue_held'
    if certificate.get('status') == 'completed_over_budget' or (directory / 'budget-exhausted.json').exists():
        return 'budget_exhausted'
    return status


def condition_records(baseline, known_models, control):
    """Read alternate conditions without redefining cases or relabeling old runs."""
    output = []
    experiments = {e['id']: e for e in registry()['experiments']}
    # New models participate in the existing cases without inventing historical
    # attempts. The original manifests and baseline rows remain unchanged.
    templates = list(baseline)
    primary_cases = {}
    for row in baseline:
        if row['role'] == 'primary':
            primary_cases.setdefault((row['experiment'], row['case']['id']), row)
    existing = {(r['experiment'], r['case']['id'], r['model']) for r in templates}
    for (experiment_id, case_id), row in primary_cases.items():
        for model_id, model in known_models.items():
            if (experiment_id, case_id, model_id) not in existing:
                templates.append({**row, 'model': model_id, 'model_label': model['label'], 'harness': model['harness']})
    for condition in condition_catalog()['conditions']:
        if condition['id'] == 'legacy-recorded':
            continue
        for original in templates:
            experiment = experiments[original['experiment']]
            if original['role'] != 'primary' or experiment['suite'] not in condition['suites']:
                continue
            legacy = attempt_directory(original['experiment'], original['case']['id'], original['model'], condition['id'])
            if experiment['suite'] in condition.get('retired_suites', []) and not legacy.exists():
                continue
            from native_attempts import report_directories
            for directory, current, attempt_id in report_directories(legacy, root=ROOT, read=read):
                row = {**original, 'condition': condition['id'], 'condition_label': condition['label'],
                       'imported': False, 'attempt_current': current, 'attempt_id': attempt_id}
                if not current:
                    row['model_label'] += ' · historical ' + (attempt_id or '')[:12]
                receipt = read(directory / 'result.json')
                grade, grade_name = selected_grade(directory)
                failed = failure_marker(directory / 'harness-failure.json')
                interrupted = read(directory / 'interrupted.json')
                started = any((directory / p).exists() for p in ['events.jsonl', 'zcode-events.jsonl', 'task.json'])
                budget_stop = read(directory / 'budget-exhausted.json')
                status = 'budget_exhausted' if budget_stop else 'environment_failure' if failed else 'interrupted' if interrupted else 'graded' if receipt and grade else 'inference_saved_grading_pending' if receipt else 'interrupted' if started and control['experiments_stopped'] else 'running' if started else 'not_started'
                status = request_budget_state(directory, status)
                if failed or status == 'budget_exhausted':
                    grade, grade_name = None, None
                if receipt is None and started and row['model'] == 'glm-5.3':
                    receipt = partial_wire_receipt(directory, row['case'], {})
                source = candidate_source(directory, (receipt or {}).get('output_language', row['case']['language']), grade)
                if source and grade and grade.get('candidate_source_sha256'):
                    assert sha(source) == grade['candidate_source_sha256']
                review = read(directory / 'readability-review.json')
                if review:
                    assert source and review['candidate_source_sha256'] == sha(source)
                if receipt:
                    prices = read(ROOT / experiment['pricing_file'])
                    if known_models[row['model']].get('pricing_file') and row['model'] != 'glm-5.3':
                        prices = read(ROOT / known_models[row['model']]['pricing_file'])
                    cost = glm_cost(receipt, read(ROOT / known_models[row['model']]['pricing_file'])) if row['model'] == 'glm-5.3' else measured_cost(receipt, prices)
                    receipt, _ = usage.derive(receipt)
                else:
                    cost = {'low_usd': None, 'high_usd': None, 'usage_complete': False}
                points = grade.get('active_point_count', grade.get('total_function_points', 1)) if grade else None
                correct = grade.get('active_points_passed', grade.get('fully_correct_points', int(grade['compiled'] and grade['passed'] == grade['total']))) if grade else None
                row.update(directory=str(directory.relative_to(ROOT)), receipt=receipt, grade=grade, status=status,
                           grade_file=grade_name if grade else None, source=str(source.relative_to(ROOT)) if source else None,
                           readability=review, cost=cost, timing_evidence=timing_evidence(receipt, directory),
                           points_total=points, points_passed=correct,
                           program_success=bool(grade and grade.get('program_success', grade['compiled'] and grade['passed'] == grade['total'] and not grade.get('failure'))),
                           environment_failure=failed, environment_delivery='fixed_profile_not_independently_audited',
                           failure_receipt_malformed=bool(failed and failed.get('classification') == 'malformed_failure_receipt'),
                           model_only_comparison_certified=False)
                if condition['id'] == 'paged-evidence-v2':
                    row['case'] = {**row['case'], 'tool_count': row['case']['tool_count'] + 1}
                output.append(row)
    return output


def active_condition_rows(data):
    routing = data.get('conditions', {}).get('active_case_conditions', {})
    return [r for r in data.get('condition_records', []) if r.get('attempt_current', True)
            and r['condition'] == routing.get(r['case']['suite'], 'lossless-evidence-v1')]


def estimate_condition(receipt, prices):
    from pricing import estimate
    return estimate(receipt, prices)


def collect():
    from pricing import estimate
    INPUTS.clear()
    catalog, known_models = registry(), models()
    control = read(BENCH / 'config/execution.json')
    experiments = []
    records = []
    for experiment in catalog['experiments']:
        base = BENCH / 'runs' / experiment['id']
        manifest = read(base / 'run-manifest.json')
        config = manifest['configuration']
        cases = frozen_cases(experiment)
        model_ids = [model['id'] for model in config['models']]
        if experiment['role'] == 'primary':
            model_ids += [m for m in known_models if m not in model_ids and any(
                x['experiment'] == experiment['id'] and x['model'] == m for x in catalog['imported_attempts'])]
            # Include a later model's original-condition attempt only when an
            # actual saved attempt exists, never as fabricated historical data.
            model_ids += [m for m in known_models if m not in model_ids and any(
                (base / m / case['id'] / name).exists()
                for case in cases for name in ['task.json', 'result.json', 'events.jsonl'])]
        experiments.append({**experiment, 'cases': cases, 'models': model_ids, 'configuration': config})
        prices = read(ROOT / experiment['pricing_file'])
        for case in cases:
            for model in model_ids:
                directory = attempt_directory(experiment['id'], case['id'], model)
                imported = imported_attempt(experiment['id'], case['id'], model)
                stopped = read(ROOT / imported['stop_record'], {}) if imported and directory == ROOT / imported['directory'] else {}
                receipt = read(directory / 'result.json')
                grade, grade_name = selected_grade(directory)
                started = receipt is not None or (directory / 'zcode-events.jsonl').exists() or (directory / 'events.jsonl').exists()
                status = 'graded' if grade and receipt else 'inference_saved_grading_pending' if receipt else 'interrupted' if started and (stopped or control['experiments_stopped']) else 'running' if started else 'not_started'
                status = request_budget_state(directory, status)
                if status == 'budget_exhausted':
                    grade, grade_name = None, None
                if receipt is None and started and model == 'glm-5.3':
                    receipt = partial_wire_receipt(directory, case, stopped)
                output_language = (receipt or {}).get('output_language', 'c' if experiment['role'] == 'historical' else case['language'])
                source = candidate_source(directory, output_language, grade)
                if source:
                    assert not grade or not grade.get('candidate_source_sha256') or sha(source) == grade['candidate_source_sha256']
                if receipt:
                    if model == 'glm-5.3':
                        price_data = read(ROOT / known_models[model]['pricing_file'])
                        cost = glm_cost(receipt, price_data)
                    else:
                        model_prices = read(ROOT / known_models[model]['pricing_file']) if known_models.get(model, {}).get('pricing_file') else prices
                        cost = measured_cost(receipt, model_prices)
                    receipt, _ = usage.derive(receipt)
                else:
                    cost = {'low_usd': None, 'high_usd': None, 'usage_complete': False}
                points = grade.get('active_point_count', grade.get('total_function_points', 1)) if grade else None
                correct_points = grade.get('active_points_passed', grade.get('fully_correct_points',
                    int(grade['compiled'] and grade['passed'] == grade['total']))) if grade else None
                records.append({'experiment': experiment['id'], 'experiment_label': experiment['label'], 'role': experiment['role'],
                    'case': case, 'model': model, 'model_label': known_models.get(model, {}).get('label', model),
                    'harness': known_models.get(model, {}).get('harness', 'unknown'), 'status': status,
                    'directory': str(directory.relative_to(ROOT)), 'receipt': receipt, 'grade': grade,
                    'timing_evidence': timing_evidence(receipt, directory),
                    'grade_file': grade_name if grade else None, 'cost': cost,
                    'readability': readability(experiment['id'], model, case['id'], source),
                    'source': str(source.relative_to(ROOT)) if source else None, 'output_language': output_language,
                    'points_total': points, 'points_passed': correct_points,
                    'program_success': bool(grade and grade.get('program_success', grade['compiled'] and
                        grade['passed'] == grade['total'] and not grade.get('failure'))),
                    'imported': bool(imported)})
    for row in records:
        row['condition'] = 'legacy-recorded'
        row['condition_label'] = 'Original harnesses'
    corrected = condition_records(records, known_models, control)
    dates = []
    for row in records + corrected:
        receipt = row.get('receipt') or {}
        for key in ['started_at', 'completed_at']:
            if receipt.get(key):
                dates.append(datetime.datetime.fromisoformat(receipt[key].replace('Z', '+00:00')))
    stop = read(ROOT / control['stop_record'], {})
    if stop.get('stopped_at'):
        dates.append(datetime.datetime.fromisoformat(stop['stopped_at'].replace('Z', '+00:00')))
    if control.get('last_stop_at'):
        dates.append(datetime.datetime.fromisoformat(control['last_stop_at'].replace('Z', '+00:00')))
    as_of = max(dates).astimezone(datetime.timezone.utc).isoformat() if dates else None
    diagnosis = read(BENCH / 'runs/stl-rust-data-v2-20261019/analysis/cached-diagnosis.json', {})
    return {'schema_version': 1, 'scope': 'Global cumulative benchmark across all registered experiments',
            'data_as_of': as_of, 'execution': control, 'experiments': experiments,
            'models': known_models, 'records': records, 'condition_records': corrected, 'conditions': condition_catalog(), 'diagnosis': diagnosis,
            'glm_pricing': read(BENCH / 'config/pricing-glm53-20261019.json'),
            'input_files_sha256': dict(INPUTS)}
