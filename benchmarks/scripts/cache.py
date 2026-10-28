"""Separate inference identity from immutable receipts, grading and reporting.

Legacy bindings are a reviewed migration, not a blanket waiver of adapter hashes.
Any subsequent change to an effective input fails closed without another request.
"""
import hashlib
import json
import copy
from functools import lru_cache
from pathlib import Path

import records
import action_cache
from grading import store as grading_store

NON_INFERENCE_FILES = {'benchmarks/scripts/run.py', 'benchmarks/scripts/environment_profile.py',
                       'benchmarks/config/conditions.json', 'benchmarks/scripts/cache.py',
                       'benchmarks/scripts/benchmark_config.py'}
MODEL_METADATA = {'label', 'pricing_file', 'pricing_note', 'comparison_role', 'enabled'}


class StaleInference(ValueError):
    """Preserved observation whose effective inputs differ from this selection."""


def action_input_bytes(action, name, *, read_blob=None):
    """Resolve one declared Merkle input, validating its complete path and blob."""
    read_blob = read_blob or action_cache.Cache().get
    parts = action_cache.relative(name).parts
    if len(parts) > 64:
        raise ValueError('Action input path exceeds its depth bound')
    current = action['input_root_digest']
    for index, part in enumerate(parts):
        action_cache.valid_digest(current)
        raw = read_blob(current)
        if len(raw) > 64 * 1024**2 or action_cache.digest(raw) != current:
            raise ValueError('Action input tree changed or exceeded its bound')
        node = json.loads(raw)
        if set(node) != {'files', 'directories'}:
            raise ValueError('Invalid action input directory')
        names = set()
        for category in ('files', 'directories'):
            if not isinstance(node[category], list) or len(node[category]) > 100000:
                raise ValueError('Invalid action directory entries')
            for entry in node[category]:
                expected = {'name', 'digest', 'is_executable'} if category == 'files' else {'name', 'digest'}
                if set(entry) != expected or category == 'files' and type(entry['is_executable']) is not bool:
                    raise ValueError('Invalid action input entry')
                relative = action_cache.relative(entry['name'])
                if len(relative.parts) != 1 or entry['name'] in names:
                    raise ValueError('Duplicate or escaping action input')
                names.add(entry['name'])
                action_cache.valid_digest(entry['digest'])
        category = 'files' if index == len(parts) - 1 else 'directories'
        matches = [entry for entry in node[category] if entry['name'] == part]
        if len(matches) != 1:
            raise ValueError('Declared action input is missing')
        current = matches[0]['digest']
    action_cache.valid_digest(current)
    raw = read_blob(current)
    if len(raw) > 64 * 1024**2 or action_cache.digest(raw) != current:
        raise ValueError('Action input blob changed or exceeded its bound')
    return raw


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=20000)
def _file_sha(path, signature):
    return records.sha(path)


def file_sha(path):
    path = Path(path)
    stat = path.stat()
    return _file_sha(path, (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))


def model_settings(model):
    return {k: v for k, v in model.items() if k not in MODEL_METADATA}


def profile_settings(profile):
    return {'id': profile['id'], 'harness': profile['harness'],
            'files_sha256': {k: v for k, v in profile['files_sha256'].items() if k not in NON_INFERENCE_FILES}}


def inference_identity(entry, model, case, profile, prompt, binary, tools, manifest, config):
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    return {'schema_version': 2, 'experiment': entry['experiment'], 'case': case['id'],
            'condition': entry.get('condition', 'legacy-recorded'), 'language': case['language'], 'model': model_settings(model),
            'environment': profile_settings(profile), 'prompt': sha(prompt), 'binary': sha(binary),
            'tools': sha(tools), 'manifest': sha(manifest),
            'limits': [case['max_tool_calls'], case['timeout_seconds'], case['max_output_tokens']],
            'runtime': {key: config.get(key) for key in ['schema_version', 'harness_version', 'tool_timeout_ms', 'data_limits']}}


def grading_identity(task, directory):
    """Grader/source changes have their own key and never request inference."""
    if task.get('track') == 'capability':
        from capabilities import store
        return store.action(directory, task).descriptor
    return grading_store.identity(directory, task)[0]


def grading_key(task, directory):
    if task.get('track') == 'capability':
        from capabilities import store
        return store.action(directory, task).key
    return grading_store.action(directory, task).key


def profile_bytes(identity):
    """Resolve declared historical bytes only by their recorded content hashes."""
    profile = identity['environment']
    names = action_cache.dependencies('inference', harness=profile['harness'], condition=identity['condition'])
    names += ['benchmarks/config/evidence-delivery.json']
    if identity['condition'] == 'paged-evidence-v2':
        names += ['benchmarks/config/evidence-paging.json']
    archives = records.read(records.BENCH / 'config/environment-profile-archives.json')['profiles'].values()
    files = {}
    for name in names:
        expected = profile['files_sha256'].get(name)
        current = records.ROOT / name
        if current.is_file() and file_sha(current) == expected:
            files[name] = current.read_bytes()
            continue
        for archive in archives:
            candidate = records.ROOT / archive['directory'] / name
            if archive['profile']['files_sha256'].get(name) == expected and candidate.is_file() and file_sha(candidate) == expected:
                files[name] = candidate.read_bytes()
                break
        else:
            raise ValueError('Verified historical dependency unavailable: ' + name)
    return files


def inference_files(task):
    """Reconstruct every public action leaf, including declared entry seeds."""
    directory = Path(task['input_directory'])
    files = {name: (directory / name).read_bytes()
             for name in ('prompt.txt', 'target', 'tools.json', 'manifest.json')}
    if task.get('analysis_context_policy'):
        from capabilities import analysis_context
        seed = directory / analysis_context.FILENAME
        if task['analysis_context_policy'] != analysis_context.POLICY:
            raise ValueError('Unsupported recorded analysis-context policy')
        if task.get('evidence_access', 'binary') == 'binary':
            import json
            expected = analysis_context.derive(files['target'], json.loads(files['manifest.json']), task['kind'])
            if expected is None:
                raise ValueError('Recorded task cannot declare analysis seeds')
            analysis_context.environment(expected, directory)
            files[analysis_context.FILENAME] = expected['seed_text'].encode('ascii')
        elif seed.exists() or seed.is_symlink():
            raise ValueError('Withheld-binary control cannot contain analysis seeds')
    return files


def inference_trial(record):
    """Stable logical trial; old records retain their exact directory encoding."""
    return record.get('inference_trial', record['directory'])


def register_inference(task, directory, action=None):
    if action is None:
        inputs = inference_files(task)
        action = action_cache.inference(task['inference_identity'], inputs,
                                       recorded_files=profile_bytes(task['inference_identity']), trial=inference_trial(task))
    local = action_cache.Cache()
    hit = local.lookup(action)
    outputs = {name: (directory / name).read_bytes() for name in ['answer.md', 'result.json']}
    if hit:
        if set(hit['output_files']) != set(outputs) or any(local.get(hit['output_files'][name]['digest']) != data for name, data in outputs.items()):
            raise ValueError('Inference action output differs from immutable cached observation')
    else:
        local.commit(action, outputs)
    return action.key


def historical_native_scope(expected, recorded, inputs):
    """Keep the reviewed source bridge inside its original native condition.

    Seeded/withheld/typed and attested actions have different execution paths;
    the adapter changes reviewed for ordinary native tasks cannot bridge them.
    """
    identities = (expected['inference_identity'], recorded)
    if expected.get('runtime_attestation') or any(value.get('attestation') for value in identities):
        raise ValueError('Historical compatibility cannot certify an attested action')
    identity_keys = {'schema_version', 'experiment', 'case', 'condition', 'language',
                     'model', 'environment', 'prompt', 'binary', 'tools', 'manifest',
                     'limits', 'runtime'}
    runtime_keys = {'schema_version', 'harness_version', 'tool_timeout_ms', 'data_limits'}
    if (expected.get('track') not in {'basic', 'complex', 'stl', 'core'}
            or expected.get('analysis_context_policy')
            or expected.get('evidence_access', 'binary') != 'binary'
            or any(set(value) != identity_keys or set(value.get('runtime', {})) != runtime_keys
                   for value in identities)
            or set(inputs) != {'prompt.txt', 'target', 'tools.json', 'manifest.json'}
            or not inputs['target']):
        raise ValueError('Historical compatibility is restricted to unseeded native binary tasks')
    try:
        tools = json.loads(inputs['tools.json'])['tools']
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError('Historical native compatibility requires a nonempty tool catalogue') from error
    if (not isinstance(tools, list) or not tools
            or any(not isinstance(tool, dict) or not isinstance(tool.get('name'), str)
                   or not tool['name'] for tool in tools)):
        raise ValueError('Historical native compatibility requires a nonempty tool catalogue')


def historical_action(expected, recorded, inputs, trial):
    """Reviewed source-only bridge for reusing an unattested observation.

    It neither asserts runtime equivalence nor creates an attested action alias.
    Each replacement binds both exact source versions; any later edit fails.
    """
    historical_native_scope(expected, recorded, inputs)
    identity = copy.deepcopy(expected['inference_identity'])
    migration = records.read(records.BENCH / 'config/runtime-attestation-migration.json', {})
    files = profile_bytes(identity)
    hashes = identity['environment']['files_sha256']
    for name, spec in migration.get('changes', {}).items():
        old = recorded['environment']['files_sha256'].get(name)
        if old == spec['before_sha256'] and hashes.get(name) == spec['after_sha256']:
            raw = (records.ROOT / spec['archive']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != old:
                raise ValueError('Historical attestation upgrade source archive changed')
            files[name] = raw
            hashes[name] = old
    return action_cache.inference(identity, inputs, recorded_files=files, trial=trial)


def legacy_fingerprint(entry, model, task, expected):
    identity = expected['inference_identity']
    return digest({'experiment': entry['experiment'], 'case': entry['case'], 'model': model,
        'environment_profile': task['environment_profile'], 'condition': entry['condition'],
        'prompt': identity['prompt'], 'binary': identity['binary'], 'tools': identity['tools'],
        'limits': identity['limits']})


def confined_path(directory, name, parents):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Cached artifact path must be relative and confined')
    original = directory / relative
    parent = original.parent
    if parent not in parents:
        parents[parent] = parent.resolve()
    path = parents[parent] / original.name
    return path.resolve() if path.is_symlink() else path


def verify_files(directory, files):
    directory = directory.resolve()
    parents = {directory: directory}
    for name, expected in files.items():
        path = confined_path(directory, name, parents)
        if not path.is_relative_to(directory) or not path.is_file() or file_sha(path) != expected:
            raise ValueError('Cached artifact missing or changed: ' + str(path))


def validate(entry, model, expected):
    """Validate all committed artifacts and effective inputs; never write a run."""
    if expected.get('track') == 'capability':
        from capabilities.coordinator import validate_cache
        return validate_cache(entry, model, expected)
    directory = records.ROOT / entry['directory']
    task = records.read(directory / 'task.json')
    if not task or task.get('model') != model['id'] or task.get('condition') != entry['condition']:
        raise ValueError('Cached task identity does not match selection')
    profile = task['environment_profile']
    if digest({k: v for k, v in profile.items() if k != 'fingerprint'}) != profile['fingerprint']:
        raise ValueError('Invalid recorded environment profile fingerprint')
    complete = records.read(directory / 'completed.json')
    if not complete or complete['fingerprint'] != task['fingerprint'] or complete['environment_profile'] != profile:
        raise ValueError('Missing or inconsistent completed cache receipt')
    verify_files(directory, complete['files_sha256'])
    if task.get('cache_schema') in (2, 3):
        recorded = task['inference_identity']
        if task['cache_schema'] == 2 and digest(recorded) != task['fingerprint']:
            raise ValueError('Recorded inference fingerprint mismatch')
        if task['cache_schema'] == 3 and action_cache.digest(action_cache.canonical(task['action_descriptor']))['hash'] != task['fingerprint']:
            raise ValueError('Recorded action fingerprint mismatch')
    else:
        binding = records.read(records.BENCH / 'config/cache-compatibility.json')['tasks'].get(task['fingerprint'])
        if not binding or binding['task'] != entry['directory'] + '/task.json':
            raise ValueError('Legacy cache has no validated migration binding')
        if records.sha(directory / 'task.json') != binding['task_sha256'] or records.sha(directory / 'completed.json') != binding['completed_sha256']:
            raise ValueError('Legacy task or completion receipt changed')
        recorded = binding['input_identity']
        if legacy_fingerprint(entry, binding['model'], task, {'inference_identity': recorded}) != task['fingerprint']:
            raise ValueError('Legacy cache input fingerprint mismatch')
        if profile_settings(binding['compatible_profile']) != recorded['environment'] or model_settings(binding['model']) != recorded['model']:
            raise ValueError('Legacy reviewed compatibility identity mismatch')
    inputs = inference_files(task)
    trial = inference_trial(task)
    if trial != inference_trial(expected):
        raise ValueError('Cached logical trial differs from selection')
    actual_action = action_cache.inference(recorded, inputs, recorded_files=profile_bytes(recorded), trial=trial)
    expected_action = action_cache.inference(expected['inference_identity'], inputs, trial=trial)
    if expected_action.descriptor != expected.get('action_descriptor'):
        raise ValueError('Inference fingerprint mismatch: effective task, model or adapter changed; no automatic rerun')
    comparison = expected_action
    if actual_action.key != expected_action.key and not recorded.get('attestation') and not expected['inference_identity'].get('attestation'):
        comparison = historical_action(expected, recorded, inputs, trial)
    if actual_action.key != comparison.key:
        raise StaleInference('Inference fingerprint mismatch: effective task, model or adapter changed; old result preserved, not reused')
    if recorded.get('attestation'):
        import runtime_attestation
        runtime_attestation.validate(recorded['attestation'])
        if task.get('runtime_attestation') != recorded['attestation']:
            raise ValueError('Task runtime/provider attestation differs from action identity')
        for phase in ('before', 'after'):
            proof = records.read(directory / ('runtime-verification-' + phase + '.json'), {})
            if proof.get('identity') != recorded['attestation']['identity'] or proof.get('status') != 'verified_local_content_and_route' or proof.get('provider') != recorded['attestation']['provider']:
                raise ValueError('Attested inference lacks pre/post execution verification')
        if task.get('request_contract') or task['runtime_attestation'].get('configuration', {}).get('harness', {}).get('request_contract'):
            import request_contract
            budget = records.read(directory / 'request-budget.json', {})
            if 'request-budget.json' not in complete['files_sha256'] or budget.get('contract') != request_contract.from_task(task) or budget.get('contract_sha256') != request_contract.digest(task['request_contract']) or not request_contract.grading_allowed(budget, task['request_contract']) or not budget.get('provider_forwards'):
                raise ValueError('Attested inference lacks committed request-budget verification')
    if task.get('cache_schema') == 3 and task['action_descriptor'] != actual_action.descriptor:
        raise ValueError('Recorded action descriptor does not match declared inputs')
    archived = records.read(records.BENCH / 'config/environment-profile-archives.json')['profiles'].get(profile['fingerprint'])
    if archived:
        if archived['profile'] != profile:
            raise ValueError('Archived source profile mismatch')
        verify_files(records.ROOT, archived['files_sha256'])
    elif task.get('cache_schema') not in (2, 3):
        raise ValueError('Legacy source archive missing')
    result = records.read(directory / 'result.json')
    if recorded.get('attestation') and (result.get('runtime_attestation_id') != recorded['attestation']['identity'] or result.get('provider') != recorded['attestation']['provider']['provider']):
        raise ValueError('Cached provider receipt differs from attested route')
    if result.get('environment_profile') != profile or result.get('model') != model['id']:
        raise ValueError('Cached model receipt mismatch')
    for key in ['binary_sha256', 'prompt_sha256']:
        if result.get(key) != task.get(key) or task.get(key) != expected.get(key):
            raise ValueError('Cached ' + key + ' mismatch')
    grade_path = directory / ('grade-framed.json' if (directory / 'grade-framed.json').exists() else 'grade.json')
    grade = records.read(grade_path)
    extension = {'c': 'c', 'cpp': 'cpp', 'rust': 'rs', 'go': 'go'}[task.get('output_language', entry['language'])]
    if grade.get('candidate_source_sha256') and records.sha(directory / ('reconstructed.' + extension)) != grade['candidate_source_sha256']:
        raise ValueError('Graded candidate source changed')
    current_grading_key = grading_key(task, directory)
    recorded_grading_key = complete.get('grading_key') if task.get('cache_schema') in (2, 3) else binding['grading_key']
    selected, _ = grading_store.read_selected(directory)
    pending = (selected or {}).get('grading_id') != current_grading_key
    if not selected and recorded_grading_key == current_grading_key:
        pending = False
    inference_key = register_inference(task, directory, actual_action)
    return {'status': 'cache_hit', 'fingerprint': task['fingerprint'], 'grading_key': recorded_grading_key,
            'runtime_attestation': 'verified_at_execution' if recorded.get('attestation') else 'historical_unknown',
            'reuse_scope': 'attested_action' if recorded.get('attestation') else 'recorded_unattested_observation',
            'grading_pending': pending,
            'inference_key': inference_key, 'action_cache_schema': 3, 'recorded_environment_profile': profile['fingerprint'],
            'grade_sha256': records.sha(grade_path)}
