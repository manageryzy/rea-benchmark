"""Read-only typed observations and family macros; never prepare, grade or infer.

The current configuration is an inventory, not proof that a denominator was
frozen before a historical run. Disabled cells and historical observations are
kept separately. Public values contain no submitted artifacts or private truth.
"""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re

import action_cache
import cache
import request_contract
import records
import report_budget
import runtime_attestation
import usage
from . import config, tasks, metrics, evidence_access, analysis_context, artifact_format
from .metrics import counts, fraction, score_vector, macro_vectors

ROOT = action_cache.ROOT
RUBRIC = ('names', 'decomposition', 'language_idioms', 'formatting', 'explanatory_comments')
CAPABILITIES = tuple(['L' + str(i) for i in range(1, 7)] +
                     ['C' + str(i) for i in range(1, 7)] + ['T' + str(i) for i in range(1, 8)])
STATUSES = ('disabled', 'preparation_pending', 'preparation_stale', 'preparation_invalid',
            'not_started', 'running', 'interrupted', 'environment_failure', 'budget_exhausted', 'completed_over_budget', 'completed_queue_held', 'action_selection_required',
            'inference_saved_grading_pending', 'record_invalid', 'graded')
RECEIPT = ROOT / 'benchmarks/config/capability-measurement-validation.json'
SOURCES = ('benchmarks/scripts/capabilities/measurements.py', 'benchmarks/scripts/check_capability_measurements.py',
           'benchmarks/scripts/capabilities/metrics.py', 'benchmarks/config/capability-measurement-policy.json',
           'benchmarks/config/action-dependencies.json',
           'benchmarks/scripts/capabilities/config.py', 'benchmarks/scripts/capabilities/tasks.py',
           'benchmarks/scripts/capabilities/pointers/tasks.py',
           'benchmarks/scripts/capabilities/variant_objects/tasks.py',
           'benchmarks/scripts/capabilities/indirect_calls/tasks.py',
           'benchmarks/scripts/capabilities/optimized_locations/tasks.py',
           'benchmarks/scripts/capabilities/native_abi/tasks.py',
           'benchmarks/scripts/capabilities/coordinator.py', 'benchmarks/scripts/cache.py',
           'benchmarks/scripts/records.py', 'benchmarks/scripts/usage.py', 'benchmarks/scripts/pricing.py',
           'benchmarks/scripts/runtime_attestation.py', 'benchmarks/scripts/public_report.py',
           'benchmarks/scripts/report.py', 'benchmarks/scripts/report_charts.py')
SOURCES += ('benchmarks/scripts/capabilities/evidence_access.py',
            'benchmarks/scripts/capabilities/analysis_context.py',
            'benchmarks/scripts/request_contract.py',
            'benchmarks/scripts/capabilities/artifact_format.py')
SOURCES += ('benchmarks/scripts/capabilities/attempts.py',)
SOURCES += ('benchmarks/scripts/report_budget.py',)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', value):
        raise ValueError('Invalid typed measurement identifier')
    return value


def hash_value(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Invalid typed measurement content digest')
    return value


def public_task_id(reader, case):
    if case['public_directory'] != '.cache/benchmark-capabilities/task-freezes/' + hash_value(case['public_package_sha256']):
        raise ValueError('Typed public package escapes its content-addressed root')
    raw = reader.raw(case['public_directory'] + '/manifest.json')
    if digest(raw) != case['public_files_sha256']['manifest.json']:
        raise ValueError('Typed public manifest changed')
    manifest = json.loads(raw)
    for name in ('build_id', 'binary_sha256', 'source_language', 'output_language', 'hint_profile', 'artifact_schema', 'capability_ids'):
        if manifest.get(name) != case[name]:
            raise ValueError('Typed task metadata differs from its public package')
    if case['public_files_sha256']['target'] != case['binary_sha256']:
        raise ValueError('Typed binary identity differs from its target bytes')
    return identifier(manifest['task_id'])


def verify_action_input(reader, action, name, raw):
    """Verify a saved accounting payload against its original Merkle input tree."""
    def read_blob(value):
        return reader.raw('.cache/benchmark-actions/v3/cas/' + hash_value(value['hash']))
    if cache.action_input_bytes(action, name, read_blob=read_blob) != raw:
        raise ValueError('Saved accounting payload differs from its action')



class Reader:
    def __init__(self, root, read_bytes):
        self.root, self.read_bytes = Path(root).resolve(), read_bytes

    def path(self, name):
        path = self.root.joinpath(*action_cache.relative(str(name)).parts)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('Typed measurement escapes its workspace')
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != self.root.parent):
            raise ValueError('Typed measurement symlink refused')
        return path

    def raw(self, name):
        path = self.path(name)
        if path.stat().st_size > 32 * 1024**2:
            raise ValueError('Typed measurement input exceeds its bound')
        return self.read_bytes(path)

    def json(self, name, default=None):
        if not self.path(name).exists():
            return default
        value = json.loads(self.raw(name))
        if not isinstance(value, dict):
            raise ValueError('Typed measurement record must be an object')
        return value


def read_grade(reader, directory, relative, task):
    parts = action_cache.relative(relative).parts
    if len(parts) != 4 or parts[0] != 'grades' or parts[3] != 'grade.json':
        raise ValueError('Invalid typed grading revision path')
    version, grading_id = identifier(parts[1]), hash_value(parts[2])
    base = directory + '/' + '/'.join(parts[:-1])
    raw = reader.raw(directory + '/' + relative)
    grade = json.loads(raw)
    manifest = reader.json(base + '/manifest.json')
    if (not isinstance(grade, dict) or manifest is None or manifest.get('cache_schema') != 3
            or manifest['action'].get('format') != action_cache.FORMAT or manifest['action'].get('kind') != 'capability_submission_v1'
            or digest(action_cache.canonical(manifest['action'])) != grading_id
            or manifest.get('grading_id') != grading_id or grade.get('grading_id') != grading_id
            or grade.get('grading_version') != version
            or digest(reader.raw(directory + '/answer.md')) != manifest['answer_sha256']):
        raise ValueError('Typed grading revision identity mismatch')
    binding = manifest['task_binding']
    for name in ('binary_sha256', 'artifact_schema', 'capability_ids'):
        if binding[name] != task[name]:
            raise ValueError('Typed grade differs from its inference contract')
    spec_name = binding['grading_spec']
    if not spec_name.startswith('.cache/benchmark-capabilities/grade-specs/'):
        raise ValueError('Invalid private typed grading root')
    spec = reader.raw(spec_name)
    if (digest(spec) != binding['grading_spec_sha256']
            or json.loads(spec).get('binary_sha256') != task['binary_sha256']):
        raise ValueError('Typed grade oracle binding changed')
    files = manifest['files_sha256']
    if not isinstance(files, dict) or not 1 <= len(files) <= 32 or files.get('grade.json') != digest(raw):
        raise ValueError('Invalid typed grade artifact manifest')
    for name, expected in files.items():
        if digest(reader.raw(base + '/' + str(action_cache.relative(name)))) != hash_value(expected):
            raise ValueError('Typed grading artifact changed')
    if (grade.get('artifact_schema') != task['artifact_schema']
            or grade.get('capability_ids') != task['capability_ids']
            or type(grade.get('artifact_valid')) is not bool or type(grade.get('task_success')) is not bool):
        raise ValueError('Invalid typed grading contract')
    passed, total = counts(grade)
    if grade['task_success'] != bool(grade['artifact_valid'] and total and passed == total):
        raise ValueError('Contradictory typed task success')
    return {'grading_id': grading_id, 'grading_version': version, 'passed': passed, 'total': total,
            'artifact_valid': grade['artifact_valid'], 'task_success': grade['task_success'],
            'scores': score_vector(grade, task['capability_ids']),
            'return_provenance': 'not_executed' if not grade['artifact_valid'] and grade.get('status') in ('invalid_artifact', 'invalid_artifact_set') and not grade.get('checks')
                                 else 'host_emulator_return_v1' if grade.get('return_provenance') == 'host_emulator_return_v1'
                                 else 'host_native_abi_return_v1' if grade.get('return_provenance') == 'host_native_abi_return_v1'
                                 else 'structured_artifact' if task['kind'] in ('localization', 'optimized_localization') else 'not_executed',
            'artifacts': {name.removeprefix('artifacts/'): value for name, value in files.items() if name.startswith('artifacts/')}}


def readability(reader, directory, task, grade):
    result = {'applicable': task['output_language'] is not None, 'total': None, 'provenance': 'not_reviewed'}
    review = reader.json(directory + '/readability-review.json')
    if not result['applicable'] or not review or grade is None:
        return result
    name = next(iter(task['artifact_schema']))
    if (review.get('candidate_source_sha256') != grade['artifacts'].get(name)
            or set(review.get('scores', {})) != set(RUBRIC)
            or any(type(x) is not int or not 0 <= x <= 2 for x in review['scores'].values())
            or review.get('total') != sum(review['scores'].values())):
        raise ValueError('Typed readability review is not bound to the selected artifact')
    provenance = review.get('review_provenance', 'unknown')
    result.update(total=review['total'], provenance=provenance if provenance in ('coordinating_ai', 'human', 'independent_panel') else 'unknown')
    return result



def certified_budget(task, budget):
    if budget.get('status') == 'deadline_exceeded':
        return request_contract.deadline_grade_allowed(budget, task.get('request_contract', {}))
    if (budget.get('status') not in ('wire_enforced', 'observed_limits_enforced', 'budget_exhausted', 'completed_over_budget')
            or type(budget.get('provider_forwards')) is not int or budget['provider_forwards'] <= 0
            or not task.get('request_contract')):
        return False
    expected = task['request_contract']
    frozen = (task.get('runtime_attestation') or {}).get('configuration', {}).get('harness', {})
    if frozen.get('request_contract') != expected:
        return False
    if (budget.get('contract') != expected
            or budget.get('contract_sha256') != request_contract.digest(task['request_contract'])):
        return False
    if budget.get('status') == request_contract.success_status(expected):
        return True
    if budget.get('status') == 'completed_over_budget':
        return request_contract.grading_allowed(budget, expected)
    if (budget.get('status') != 'budget_exhausted' or budget.get('stop_reason') not in request_contract.BUDGET_STOPS
            or not isinstance(budget.get('requests'), list) or not budget['requests']):
        return False
    return all(not row.get('budget_violation')
               and (not row.get('rejected') or row['rejected'] in request_contract.BUDGET_STOPS)
               and row.get('contract_sha256') == budget['contract_sha256']
               for row in budget['requests'])


def effective_runtime(task, reader=None):
    identity = task.get('inference_identity', {})
    proof = task.get('runtime_attestation') or {}
    if not isinstance(identity, dict) or not isinstance(proof, dict):
        raise ValueError('Invalid observed runtime metadata')
    if proof and proof != identity.get('attestation'):
        raise ValueError('Observed runtime differs from its frozen inference identity')
    if proof and reader is not None:
        action = task['action_descriptor']
        if digest(action_cache.canonical(action)) != task['fingerprint']:
            raise ValueError('Runtime inference action identity changed')
        raw = cache.action_input_bytes(action, 'configuration/effective.json', read_blob=lambda value:
            reader.raw('.cache/benchmark-actions/v3/cas/' + hash_value(value['hash'])))
        effective = json.loads(raw)
        if not isinstance(effective, dict) or effective.get('attestation') != proof:
            raise ValueError('Observed runtime differs from its frozen inference action')
    configuration = proof.get('configuration', {})
    if not isinstance(configuration, dict):
        raise ValueError('Invalid observed runtime configuration')
    result = configuration['harness'] if 'harness' in configuration else identity.get('runtime', {})
    if not isinstance(result, dict):
        raise ValueError('Invalid observed harness configuration')
    return result


def observed_artifact_format(reader, task):
    expected = artifact_format.policy(task, effective_runtime(task, reader))
    if task.get('artifact_format_policy') != expected:
        raise ValueError('Observed artifact-format label differs from its effective policy')
    if expected:
        root = reader.root / '.cache/benchmark-runtime/tasks' / hash_value(task['fingerprint'])
        if Path(task.get('input_directory', '')) != root:
            raise ValueError('Artifact-format inputs are not content-addressed')
        raw = reader.raw(root.relative_to(reader.root).as_posix() + '/prompt.txt')
        if raw.count(artifact_format.NOTICE) != 1:
            raise ValueError('Explicit artifact-format notice is absent or duplicated')
        verify_action_input(reader, task['action_descriptor'], 'task/prompt.txt', raw)
    return expected


def observed_analysis_context(reader, task, access):
    policy = task.get('analysis_context_policy')
    use = analysis_context.enabled(effective_runtime(task, reader))
    expected_policy = analysis_context.POLICY if use and task['kind'] in analysis_context.KNOWN else None
    if policy != expected_policy:
        raise ValueError('Observed analysis context does not match its effective policy')
    if policy is None:
        return None
    root = reader.root / '.cache/benchmark-runtime/tasks' / hash_value(task['fingerprint'])
    if Path(task.get('input_directory', '')) != root:
        raise ValueError('Seeded task inputs are not content-addressed')
    relative = root.relative_to(reader.root).as_posix()
    proof = task.get('runtime_attestation') or {}
    if proof != task.get('inference_identity', {}).get('attestation'):
        raise ValueError('Seeded task lacks its bound runtime configuration')
    descriptor = proof.get('configuration', {}).get('gateway', {}).get('analysis_context')
    if access == 'premises_only':
        if descriptor is not None or (root / analysis_context.FILENAME).exists():
            raise ValueError('Premises-only task retained analysis seeds')
        return policy
    raw = reader.raw(relative + '/target')
    manifest = reader.json(relative + '/manifest.json')
    expected = analysis_context.derive(raw, manifest, task['kind'])
    if descriptor != expected:
        raise ValueError('Saved seed descriptor differs from its public inputs')
    seeds = reader.raw(relative + '/' + analysis_context.FILENAME)
    if seeds != expected['seed_text'].encode('ascii'):
        raise ValueError('Saved analysis seed bytes changed')
    action = task['action_descriptor']
    if digest(action_cache.canonical(action)) != task['fingerprint']:
        raise ValueError('Seeded inference action identity changed')
    verify_action_input(reader, action, 'task/' + analysis_context.FILENAME, seeds)
    return policy


def observed_trial(reader, directory, task):
    """Admit new routing metadata from its committed action, without inventory."""
    if 'inference_trial' not in task:
        return directory
    trial = str(action_cache.relative(task['inference_trial']))
    action = task['action_descriptor']
    key = hash_value(task['fingerprint'])
    addressed = str(Path(trial).parent / key)
    if (digest(action_cache.canonical(action)) != key or task.get('directory') != directory
            or directory not in (trial, addressed)):
        raise ValueError('Typed attempt physical/logical binding changed')
    raw = cache.action_input_bytes(action, 'configuration/effective.json', read_blob=lambda value:
        reader.raw('.cache/benchmark-actions/v3/cas/' + hash_value(value['hash'])))
    if json.loads(raw).get('trial') != trial:
        raise ValueError('Typed logical trial differs from its committed action')
    return trial


def observe(reader, directory, task, models, settings, stopped):
    access = evidence_access.mode(task.get('evidence_access', 'binary'))
    inputs = None
    if access == 'premises_only':
        expected = reader.root / '.cache/benchmark-runtime/tasks' / hash_value(task['fingerprint'])
        if Path(task.get('input_directory', '')) != expected:
            raise ValueError('Premises-only input directory is not its content-addressed runtime root')
        relative = expected.relative_to(reader.root).as_posix()
        inputs = {name: reader.raw(relative + '/' + name)
                  for name in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
    evidence_access.validate_observation(task, inputs)
    context_policy = observed_analysis_context(reader, task, access)
    format_policy = observed_artifact_format(reader, task)
    model = identifier(task['model'])
    profile = identifier(task['profile_id'])
    capabilities = task['capability_ids']
    if (not isinstance(capabilities, list) or not capabilities or len(set(capabilities)) != len(capabilities)
            or set(capabilities) - set(CAPABILITIES)):
        raise ValueError('Invalid observed capability selection')
    attempt_id = hash_value(task['fingerprint'])
    package = hash_value(task['public_package_sha256'])
    value = {'attempt_id': attempt_id, 'model': model, 'profile_id': profile,
             'task_id': public_task_id(reader, task), 'family_id': identifier(task['family_id']),
             'lineage_id': metrics.lineage(task['kind'], task['family_id']),
             'build_id': hash_value(task['build_id']), 'public_package_sha256': package,
             'binary_sha256': hash_value(task['binary_sha256']), 'source_language': identifier(task['source_language']),
             'hint_profile': identifier(task['hint_profile']),
             'analysis_condition': artifact_format.condition(evidence_access.analysis_condition(access, context_policy or identifier(task.get('condition', settings['condition']))), format_policy),
             'output_language': task['output_language'], 'capability_ids': capabilities,
             'status': 'not_started', 'grade': None, 'grade_revisions': [], 'invalid_grade_revisions': 0,
             'readability': {'applicable': task['output_language'] is not None, 'total': None, 'provenance': 'not_reviewed'},
             'usage_status': 'unknown', 'known_tokens': {}, 'cost': {'low_usd': None, 'high_usd': None, 'usage_complete': False},
             'runtime_status': 'not_recorded', 'budget_status': 'not_recorded',
             'accounting_revisions': [], 'accounting_policy': 'current_saved_pricing', 'record_valid': True}
    result = reader.json(directory + '/result.json')
    failed = reader.path(directory + '/harness-failure.json').exists()
    interrupted = reader.path(directory + '/interrupted.json').exists()
    budget_stop = reader.path(directory + '/budget-exhausted.json').exists()
    completed_over_budget = False
    verified_stop = False
    if failed and budget_stop:
        failure = reader.json(directory + '/harness-failure.json', {})
        marker = reader.json(directory + '/budget-exhausted.json', {})
        certificate = reader.json(directory + '/request-budget.json', {})
        verified_stop = (failure.get('classification') == 'harness_or_provider_failure'
            and marker.get('classification') == 'task_budget_exhausted'
            and marker.get('reason') == certificate.get('stop_reason')
            and certificate.get('status') == 'budget_exhausted'
            and certified_budget(task, certificate))
    value['status'] = 'budget_exhausted' if verified_stop else 'environment_failure' if failed else 'budget_exhausted' if budget_stop else 'interrupted' if interrupted or not result and stopped else 'running'
    if result:
        if result.get('model') != model or result.get('output_language') != task['output_language']:
            raise ValueError('Typed result model or output language differs from task')
        _, ledger = usage.derive(result)
        value.update(usage_status=ledger['status'], known_tokens=ledger['reported_usage'])
        price_name = models.get(model, {}).get('pricing_file') or settings['pricing_file']
        prices = reader.json(price_name)
        cost = records.glm_cost(result, prices) if model == 'glm-5.3' else records.measured_cost(result, prices)
        value['cost'] = {name: cost.get(name) for name in ('low_usd', 'high_usd', 'usage_complete')}
        value['runtime_status'] = runtime_attestation.observation(reader.path(directory), reader.read_bytes, started=True)
        budget = reader.json(directory + '/request-budget.json', {})
        value['budget_status'] = 'unverified' if budget else 'not_recorded'
        if certified_budget(task, budget):
            value['budget_status'] = budget['status']
            saved_outcome = report_budget.outcome(budget, task['request_contract'])
            if saved_outcome:
                value['budget_outcome'] = saved_outcome
            completed_over_budget = budget['status'] == 'completed_over_budget'
        elif budget.get('status') == 'completed_over_budget':
            budget_stop = True
            value['status'] = 'budget_exhausted'
        if not failed and not interrupted and (not budget_stop or completed_over_budget):
            value['status'] = 'inference_saved_grading_pending'
            if completed_over_budget:
                value['status'] = 'completed_over_budget'
    try:
        selection = reader.json(directory + '/selected-grade.json')
    except (ValueError, OSError):
        selection = None
        value['record_valid'] = False
    selected_path = selection.get('path') if selection else None
    for path in sorted(reader.path(directory).glob('grades/*/*/grade.json')):
        relative = path.relative_to(reader.path(directory)).as_posix()
        try:
            grade = read_grade(reader, directory, relative, task)
        except (ValueError, OSError, KeyError, TypeError):
            value['invalid_grade_revisions'] += 1
            if relative == selected_path:
                value['record_valid'] = False
            continue
        chosen = relative == selected_path
        if chosen and (selection.get('grading_id') != grade['grading_id']
                       or selection.get('sha256') != digest(reader.raw(directory + '/' + relative))):
            value['record_valid'] = False
            chosen = False
        value['grade_revisions'].append({**{key: grade[key] for key in ('grading_id', 'grading_version', 'passed', 'total', 'task_success')}, 'selected': chosen})
        if chosen:
            value['grade'] = grade
    if selection and (value['grade'] is None or not value['record_valid']):
        value.update(record_valid=False, grade=None)
    if value['grade'] and result and not failed and not interrupted and (not budget_stop or completed_over_budget) and value['record_valid']:
        value['status'] = ('completed_over_budget' if completed_over_budget else 'completed_queue_held'
                           if value.get('budget_outcome', {}).get('queue_stop_required') else 'graded')
        try:
            value['readability'] = readability(reader, directory, task, value['grade'])
        except (ValueError, OSError, KeyError, TypeError):
            value['readability']['provenance'] = 'invalid_review'
    else:
        value['grade'] = None
    if not value['record_valid']:
        value['status'] = 'record_invalid'
    value['invalid_accounting_revisions'] = 0
    for path in sorted(reader.path(directory).glob('accounting/*/*/record.json')):
        try:
            record = reader.json(path.relative_to(reader.root).as_posix())
            identity = record['identity']
            key = hash_value(path.parent.name)
            if (digest(action_cache.canonical(identity['action'])) != key
                    or identity['action'].get('kind') != 'accounting' or identity['action'].get('format') != action_cache.FORMAT
                    or identity['version'] != path.parent.parent.name or identity['version'] != record['ledger']['version']
                    or identity['receipt_sha256'] != digest(reader.raw(directory + '/result.json'))
                    or record['ledger']['status'] not in ('complete', 'partial', 'unknown')):
                raise ValueError('Invalid typed accounting revision binding')
            semantic = {key: value for key, value in record['ledger'].items() if key != 'receipt_sha256'}
            verify_action_input(reader, identity['action'], 'evidence/ledger.json', action_cache.canonical(semantic))
            verify_action_input(reader, identity['action'], 'evidence/cost.json', action_cache.canonical(record['cost']))
            value['accounting_revisions'].append({'accounting_id': key, 'version': identifier(identity['version']),
                'usage_status': record['ledger']['status'], 'low_usd': record['cost'].get('low_usd'), 'high_usd': record['cost'].get('high_usd')})
        except (ValueError, OSError, KeyError, TypeError):
            value['invalid_accounting_revisions'] += 1
    return value


def empty():
    return {'schema_version': 1, 'schedule_status': 'not_recorded', 'schedule_id': '',
            'inference_enabled': False, 'declared_tasks': 0, 'configured_cells': 0, 'scheduled_cells': 0,
            'disabled_cells': 0, 'saved_observations': 0, 'duplicate_saved_copies': 0, 'invalid_attempt_records': 0,
            'cells': [], 'history': [], 'macro_vectors': [], 'totals': [], 'statuses': {}, 'released_capabilities': 0,
            'validation': {'passed': False, 'current': False, 'checks': 0, 'model_api_calls': 0}}


def totals(cells, history):
    output = []
    for model in sorted({row['model'] for row in cells + history}):
        configured = [row for row in cells if row['model'] == model]
        scheduled = [row for row in configured if row['scheduled']]
        graded = [row['observation'] for row in scheduled if row['observation'] and row['observation']['grade']]
        saved = [row for row in history if row['model'] == model]
        costs = [row['cost'] for row in saved if row['cost']['low_usd'] is not None]
        reviewed = [row['readability']['total'] for row in graded if row['readability']['total'] is not None]
        output.append({'model': model, 'configured_cells': len(configured), 'scheduled_cells': len(scheduled),
            'graded_cells': len(graded), 'artifact_valid_cells': sum(row['grade']['artifact_valid'] for row in graded),
            'successful_cells': sum(row['grade']['task_success'] for row in graded),
            'saved_observations': len(saved), 'readability_n': len(reviewed),
            'readability_mean': sum(reviewed) / len(reviewed) if reviewed else None,
            'known_tokens': {key: sum(values) if values else None for key in usage.TOKEN_KEYS
                             for values in [[row['known_tokens'][key] for row in saved if key in row['known_tokens']]]},
            'cost': {'low_usd': sum(row['low_usd'] for row in costs) if costs else None,
                     'high_usd': sum(row['high_usd'] for row in costs) if costs else None,
                     'usage_complete': bool(saved) and all(row['cost']['usage_complete'] for row in saved)}})
    return output


def fingerprints():
    return {name: digest((ROOT / name).read_bytes()) for name in SOURCES}


def validation(read_bytes):
    result = empty()['validation']
    if RECEIPT.is_file():
        value = json.loads(read_bytes(RECEIPT))
        expected = {name: digest(read_bytes(ROOT / name)) for name in SOURCES}
        result.update(passed=value.get('passed') is True and value.get('failed') == 0
                      and type(value.get('checks')) is int and value['checks'] >= 51
                      and value.get('model_api_calls') == value.get('provider_launches') == 0,
                      current=value.get('inputs_sha256') == expected, checks=value.get('checks', 0))
    return result


def public_projection(value):
    """Explicit projection also removes artifact names/hashes from grade internals."""
    def pick(row, names):
        return {name: copy.deepcopy(row[name]) for name in names.split()}
    def observation(row):
        if row is None:
            return None
        out = pick(row, 'attempt_id model profile_id task_id family_id lineage_id build_id public_package_sha256 '
                       'binary_sha256 source_language hint_profile analysis_condition '
                       'output_language capability_ids status grade_revisions invalid_grade_revisions readability '
                       'usage_status known_tokens cost runtime_status budget_status accounting_revisions '
                       'accounting_policy invalid_accounting_revisions record_valid')
        out['grade'] = pick(row['grade'], 'grading_id grading_version passed total artifact_valid task_success scores return_provenance') if row['grade'] else None
        if 'budget_outcome' in row:
            out['budget_outcome'] = pick(row['budget_outcome'], ' '.join(report_budget.OUTCOME_FIELDS))
        return out
    result = pick(value, 'schema_version schedule_status schedule_id inference_enabled declared_tasks configured_cells '
                         'scheduled_cells disabled_cells saved_observations duplicate_saved_copies invalid_attempt_records '
                         'macro_vectors totals statuses released_capabilities validation')
    result['history'] = [observation(row) for row in value['history']]
    result['cells'] = [{**pick(row, 'cell_id model task_id family_id lineage_id profile_id output_language '
                                  'binary_sha256 source_language hint_profile analysis_condition '
                                  'public_package_sha256 build_id capability_ids scheduled preparation_status status'),
                        'observation': observation(row['observation'])} for row in value['cells']]
    return result


def validate_projection(value):
    """Cross-field checks run after the public export's recursive allowlist."""
    if value['schema_version'] != 1 or value['released_capabilities'] != 0:
        raise ValueError('Unknown typed report version or unreviewed capability release')
    controls = value['validation']
    # Historical public snapshots keep their recorded pre-T3 control scope.
    # They cannot certify the new pointer condition merely by being rendered
    # with a newer report generator.
    scope = {cap for row in value['cells'] + value['history'] for cap in row['capability_ids']}
    minimum_checks = 32 if 'T7' in scope else 31 if 'L6' in scope else 30 if 'T6' in scope else 29 if 'T4' in scope else 28 if 'T3' in scope else 24
    if any(row.get('hint_profile') == 'known_matched_buffer_entry_v1' for row in value['cells'] + value['history']):
        minimum_checks = max(minimum_checks, 35)
    if any(row.get('profile_id') in ('pointer_topology_recover', 'pointer_topology_recover_counterfactual')
           for row in value['cells'] + value['history']):
        minimum_checks = max(minimum_checks, 36)
    if any(row.get('analysis_condition') == 'premises-only-v1' for row in value['cells'] + value['history']):
        minimum_checks = max(minimum_checks, 39)
    if any(row.get('analysis_condition') == analysis_context.POLICY for row in value['cells'] + value['history']):
        minimum_checks = max(minimum_checks, 43)
    if any(row.get('analysis_condition', '').endswith(artifact_format.SUFFIX) for row in value['cells'] + value['history']):
        minimum_checks = max(minimum_checks, 47)
    if controls['model_api_calls'] != 0 or controls['passed'] and controls['checks'] < minimum_checks:
        raise ValueError('Inconsistent typed collection controls')
    if value['schedule_status'] not in ('not_recorded', 'configuration_snapshot_only'):
        raise ValueError('Unproven preregistered measurement denominator')
    if value['schedule_status'] == 'configuration_snapshot_only':
        hash_value(value['schedule_id'])
    elif value != empty():
        raise ValueError('Missing typed measurement evidence cannot gain observations')
    cells, history = value['cells'], value['history']
    ids = [row['attempt_id'] for row in history]
    if len(ids) != len(set(ids)) or len(history) != value['saved_observations']:
        raise ValueError('Duplicate or missing typed observations')
    if len(cells) != value['configured_cells'] or len({row['cell_id'] for row in cells}) != len(cells):
        raise ValueError('Typed configured denominator changed')
    if (sum(row['scheduled'] for row in cells) != value['scheduled_cells']
            or sum(not row['scheduled'] for row in cells) != value['disabled_cells']
            or dict(sorted(Counter(row['status'] for row in cells).items())) != value['statuses']):
        raise ValueError('Typed scheduled denominator changed')
    by_id = {row['attempt_id']: row for row in history}
    for row in cells:
        hash_value(row['cell_id'])
        if (row['status'] not in STATUSES or (row['status'] == 'disabled') == row['scheduled']
                or row['preparation_status'] not in ('prepared', 'preparation_pending', 'preparation_stale', 'preparation_invalid')):
            raise ValueError('Invalid typed cell state')
        if row['observation'] and (by_id.get(row['observation']['attempt_id']) != row['observation']
                                   or row['analysis_condition'] != row['observation']['analysis_condition']):
            raise ValueError('Typed cell lacks its saved observation')
    for row in history:
        for name in ('attempt_id', 'build_id', 'binary_sha256', 'public_package_sha256'):
            hash_value(row[name])
        for name in ('model', 'family_id', 'lineage_id', 'profile_id', 'task_id', 'hint_profile', 'analysis_condition'):
            identifier(row[name])
        if row['status'] not in STATUSES or row['usage_status'] not in ('complete', 'partial', 'unknown'):
            raise ValueError('Invalid typed observation state')
        if row['runtime_status'] not in ('not_recorded', 'historical_unknown', 'verified_at_execution', 'unverified') or row['budget_status'] not in ('not_recorded', 'unverified', 'wire_enforced', 'observed_limits_enforced', 'budget_exhausted', 'completed_over_budget', 'deadline_exceeded'):
            raise ValueError('Invalid typed certification status')
        saved_outcome = row.get('budget_outcome')
        if saved_outcome:
            report_budget.validate_outcome(saved_outcome)
            if row['budget_status'] != saved_outcome['status']:
                raise ValueError('Typed budget outcome contradicts saved receipt status')
            if row['grade'] and saved_outcome['queue_stop_required'] and row['status'] not in (
                    'completed_over_budget', 'completed_queue_held'):
                raise ValueError('Typed correctness grade lost its queue-hold state')
        if row['status'] == 'completed_over_budget' and (
                not saved_outcome or saved_outcome['status'] != 'completed_over_budget'):
            raise ValueError('Completed over-budget candidate lacks spending-stop evidence')
        if row['status'] == 'completed_queue_held' and (
                not saved_outcome or not saved_outcome['queue_stop_required'] or saved_outcome['budget_exceeded']):
            raise ValueError('Completed candidate lacks queue-hold evidence')
        if row['source_language'] not in ('cpp', 'rust', 'go') or row['output_language'] not in (None, 'c', 'cpp', 'rust', 'go') or set(row['capability_ids']) - set(CAPABILITIES):
            raise ValueError('Invalid typed measurement contract')
        if row['grade']:
            counts(row['grade'])
            if row['status'] not in ('graded', 'completed_over_budget', 'completed_queue_held') or not row['record_valid']:
                raise ValueError('Unscored typed state gained correctness')
            selected = [item for item in row['grade_revisions'] if item['selected']]
            if len(selected) != 1 or selected[0]['grading_id'] != row['grade']['grading_id']:
                raise ValueError('Typed grade selection differs from revision history')
            for score in row['grade']['scores'].values():
                fraction(score['score'])
                for component in score['components'].values():
                    fraction(component)
        if row['readability']['provenance'] not in ('not_reviewed', 'invalid_review', 'unknown', 'coordinating_ai', 'human', 'independent_panel'):
            raise ValueError('Unknown readability review provenance')
        if row['accounting_policy'] != 'current_saved_pricing':
            raise ValueError('Unreviewed typed accounting selection')
    models = sorted({row['model'] for row in cells})
    if macro_vectors(cells, models) != value['macro_vectors'] or totals(cells, history) != value['totals']:
        raise ValueError('Typed macro, coverage or accounting totals changed')
    return value


def collect(read_bytes, model_catalog, selection, *, preparation_current=None, root=ROOT):
    reader = Reader(root, read_bytes)
    metrics.policy(read_bytes, root=reader.root)
    settings, families = config.load()
    if reader.json('benchmarks/config/capabilities.json') != settings or reader.json(settings['families_file']) != families:
        raise ValueError('Capability reporting configuration changed while collecting')
    declarations = tasks.declared()
    access = evidence_access.validate_selection(selection, declarations)
    pins = reader.json('benchmarks/config/capability-task-pins.json', {})
    source_current = bool(pins.get('inputs_sha256')) and pins.get('schema_version') == 1 and pins.get('model_api_calls') == 0
    for name, expected in pins.get('inputs_sha256', {}).items():
        if not name.startswith(('benchmarks/config/', 'benchmarks/scripts/capabilities/',
                                'benchmarks/scripts/grading/', 'benchmarks/suites/capabilities-v1/')):
            source_current = False
            continue
        try:
            actual = digest(reader.raw(name))
        except OSError:
            actual = None
        source_current = source_current and actual == expected
    source_current = source_current and preparation_current is not False
    pin_rows = pins.get('tasks', [])
    pinned = {row['task_id']: row for row in pin_rows}
    source_current = source_current and len(pinned) == len(pin_rows)
    selected_models = [key for key, spec in model_catalog.items()
                       if (selection['models'] == 'all' and spec.get('enabled', True))
                       or isinstance(selection['models'], list) and key in selection['models']]
    stopped = reader.json('benchmarks/config/execution.json', {'experiments_stopped': True})['experiments_stopped']
    observed, by_directory, by_trial, copies, invalid, conflicted = {}, {}, {}, 0, 0, set()
    base = reader.path('benchmarks/runs/capabilities-v1')
    for path in sorted(base.glob('*/*/*/*/task.json')):
        directory = path.parent.relative_to(reader.root).as_posix()
        try:
            task = reader.json(path.relative_to(reader.root).as_posix())
            if task.get('offline_fixture'):
                continue
            if task.get('track') != 'capability' or task.get('artifact_contract_version') != 1:
                raise ValueError('Unregistered typed attempt')
            trial = observed_trial(reader, directory, task)
            row = observe(reader, directory, task, model_catalog, settings, stopped)
            key = row['attempt_id']
            if key in conflicted:
                raise ValueError('Conflicting copies of a typed observation')
            if key in observed:
                if row != observed[key]:
                    del observed[key]
                    conflicted.add(key)
                    for previous in by_directory:
                        if by_directory[previous] == key:
                            by_directory[previous] = None
                            invalid += 1
                    raise ValueError('Conflicting copies of a typed observation')
                copies += 1
            else:
                observed[key] = row
            by_directory[directory] = key
            by_trial.setdefault(trial, set()).add(key)
        except (ValueError, OSError, KeyError, TypeError):
            invalid += 1
            by_directory[directory] = None
    cells = []
    import benchmark_config
    from .coordinator import _namespace
    for declaration in declarations:
        name, profile = declaration['task_id'], declaration['profile']
        case, public_id = None, None
        state = 'preparation_pending'
        try:
            case = reader.json('.cache/benchmark-capabilities/task-index/' + name + '.json')
            if case:
                state = 'prepared' if source_current else 'preparation_stale'
                if case['id'] != name or case['profile_id'] != profile['id'] or case['kind'] != profile['kind']:
                    raise ValueError('Frozen task identity differs')
                public_id = public_task_id(reader, case)
                hashes = {key: digest(reader.raw(case['public_directory'] + '/' + key)) for key in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
                if hashes != case['public_files_sha256'] or digest(action_cache.canonical(hashes)) != case['public_package_sha256']:
                    raise ValueError('Frozen public task bytes changed')
                capability_ids = case['capability_ids']
                if not capability_ids or set(capability_ids) - set(CAPABILITIES):
                    raise ValueError('Unknown prepared capability')
                pin = {key: case[key] for key in ('family_id', 'profile_id', 'binary_sha256', 'public_package_sha256', 'capability_ids')}
                pin['task_id'] = name
                if pinned.get(name) != pin:
                    state = 'preparation_stale'
        except (ValueError, OSError, KeyError, TypeError):
            state, public_id = 'preparation_invalid', None
        for model in selected_models:
            entry = {'model': model, 'suite': 'capabilities-v1', 'experiment': 'capabilities-v1', 'case': name}
            if not benchmark_config.accepts(selection, entry):
                continue
            package = case.get('public_package_sha256') if case and state != 'preparation_invalid' else None
            namespace = _namespace(settings, model_catalog[model], access)
            directory = 'benchmarks/runs/capabilities-v1/' + model + '/' + name + '/' + package + '/' + namespace if package else None
            enabled = bool(settings['inference_enabled'] and profile['enabled'] and selection.get('capabilities', True)
                           and selection['condition'] in ('active', settings['condition']))
            context_policy = analysis_context.POLICY if analysis_context.enabled(settings['runtime']) and profile['kind'] in analysis_context.KNOWN else None
            format_policy = artifact_format.policy({'kind': profile['kind'], 'artifact_schema': profile['artifact_schema']}, settings['runtime'])
            analysis_condition = artifact_format.condition(evidence_access.analysis_condition(access, context_policy or settings['condition']), format_policy)
            candidates = by_trial.get(directory, set()) - conflicted
            key = next(iter(candidates)) if len(candidates) == 1 else by_directory.get(directory)
            observation = observed.get(key)
            status = observation['status'] if observation else 'record_invalid' if directory in by_directory else 'not_started' if state == 'prepared' else state
            if len(candidates) > 1:
                # Reporting cannot probe today's provider or silently pick the
                # newest/best runtime. Every action remains visible in history.
                status, observation = 'action_selection_required', None
            if observation and (observation['family_id'] != declaration['family_id']
                    or observation['profile_id'] != profile['id'] or observation['build_id'] != case['build_id']
                    or observation['binary_sha256'] != case['binary_sha256']
                    or observation['output_language'] != profile['output_language']
                    or observation['capability_ids'] != case['capability_ids']
                    or observation['analysis_condition'] != analysis_condition):
                status, observation = 'record_invalid', None
            if not enabled:
                status = 'disabled'
            capabilities = case['capability_ids'] if case and state != 'preparation_invalid' else (
                ['L1', 'L2', 'L5'] + (['L3'] if declaration['family_id'] in ('dispatch_registry', 'callback_policy') else [])
                    + (['L4'] if declaration['family_id'] in ('record_codec', 'dispatch_registry') else []) if profile['kind'] == 'localization' else ['C1', 'C2', 'C3', 'C4'] if profile['kind'] in ('c_buffer', 'cpp_buffer', 'matched_buffer') else
                (['C5', 'C6'] if declaration['family_id'] == 'callback_policy' else ['C6']) if profile['kind'] in ('c_lifecycle', 'cpp_lifecycle') else
                ['L6'] if profile['kind'] == 'optimized_localization' else
                ['T7'] if profile['kind'] == 'native_abi' else
                ['T6'] if profile['kind'] == 'indirect_call' else
                ['T4'] if profile['kind'] == 'variant_object' else
                ['T3'] if profile['kind'] == 'pointer_probe' else
                [{'scalar_recover': 'T1', 'layout_recover': 'T2', 'abi_recover': 'T5'}[profile.get('condition', profile['id'])]])
            cells.append({'cell_id': digest(action_cache.canonical([name, model, namespace])), 'model': model,
                'task_id': public_id or 'pending_' + digest(name.encode())[:20], 'family_id': declaration['family_id'],
                'lineage_id': metrics.lineage(profile['kind'], declaration['family_id']),
                'profile_id': profile['id'], 'output_language': profile['output_language'],
                'source_language': case.get('source_language', 'cpp') if case and state != 'preparation_invalid' else profile['variant'] if profile['kind'] == 'native_abi' else (
                    'cpp' if profile['original_compiler'] == 'clang20' else profile['original_compiler'])
                    if profile['kind'] == 'matched_buffer' else families.get('baseline', {}).get('language', 'cpp'),
                'binary_sha256': case.get('binary_sha256') if case and state != 'preparation_invalid' else None,
                'hint_profile': case.get('hint_profile', 'unprepared') if case and state != 'preparation_invalid' else 'unprepared',
                'analysis_condition': analysis_condition,
                'public_package_sha256': package, 'build_id': case.get('build_id') if case and state != 'preparation_invalid' else None,
                'capability_ids': capabilities, 'scheduled': enabled, 'preparation_status': state,
                'status': status, 'observation': observation})
    snapshot = {'selection': {key: selection.get(key) for key in ('models', 'suites', 'experiments', 'cases', 'condition', 'capabilities')},
                'limits': settings['limits'], 'runtime': settings['runtime'],
                'cells': [{key: row[key] for key in ('cell_id', 'model', 'task_id', 'profile_id', 'scheduled', 'public_package_sha256', 'capability_ids')} for row in cells]}
    if access != 'binary':
        snapshot['selection']['evidence_access'] = access
    return {**empty(), 'schedule_status': 'configuration_snapshot_only', 'schedule_id': digest(action_cache.canonical(snapshot)),
            'inference_enabled': settings['inference_enabled'], 'declared_tasks': len(declarations),
            'configured_cells': len(cells), 'scheduled_cells': sum(row['scheduled'] for row in cells),
            'disabled_cells': sum(not row['scheduled'] for row in cells), 'saved_observations': len(observed),
            'duplicate_saved_copies': copies, 'invalid_attempt_records': invalid,
            'cells': cells, 'history': [row for _, row in sorted(observed.items())],
            'macro_vectors': macro_vectors(cells, selected_models),
            'totals': totals(cells, list(observed.values())),
            'statuses': dict(sorted(Counter(row['status'] for row in cells).items()))}
