"""Separate inference identity from immutable receipts, grading and reporting.

Legacy bindings are a reviewed migration, not a blanket waiver of adapter hashes.
Any subsequent change to an effective input fails closed without another request.
"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import records

NON_INFERENCE_FILES = {'benchmarks/scripts/run.py', 'benchmarks/scripts/environment_profile.py',
                       'benchmarks/config/conditions.json', 'benchmarks/scripts/cache.py',
                       'benchmarks/scripts/benchmark_config.py'}
MODEL_METADATA = {'label', 'pricing_file', 'pricing_note', 'comparison_role', 'enabled'}


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
    files = [records.BENCH / 'scripts/grade.py', records.BENCH / 'scripts/collect_answer.py']
    suite = task['suite']
    if suite == 'record-parser':
        files += [records.BENCH / 'suites/record-parser/grading/vectors.json']
    elif suite == 'complex-native-v1':
        files += [records.BENCH / 'suites/complex-native-v1/scripts/grade.py',
                  records.BENCH / 'suites/complex-native-v1/private/vectors.json']
    else:
        files += [records.BENCH / 'suites/stl-native-v2/scripts/grade-framed.py',
                  records.BENCH / 'runs/stl-rust-data-v2-20261019/input-provenance.json']
        files += sorted((records.BENCH / 'runs/stl-rust-data-v2-20261019/pipeline').glob('*-vectors.json'))
    return {'files_sha256': {str(p.relative_to(records.ROOT)): file_sha(p) for p in files},
            'answer_sha256': file_sha(directory / 'answer.md'), 'language': task['language'],
            'standard': task.get('standard'), 'string_abi': task.get('string_abi')}


def legacy_fingerprint(entry, model, task, expected):
    identity = expected['inference_identity']
    return digest({'experiment': entry['experiment'], 'case': entry['case'], 'model': model,
        'environment_profile': task['environment_profile'], 'condition': entry['condition'],
        'prompt': identity['prompt'], 'binary': identity['binary'], 'tools': identity['tools'],
        'limits': identity['limits']})


def verify_files(directory, files):
    directory = directory.resolve()
    for name, expected in files.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or file_sha(path) != expected:
            raise ValueError('Cached artifact missing or changed: ' + str(path))


def validate(entry, model, expected):
    """Validate all committed artifacts and effective inputs; never write a run."""
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
    if task.get('cache_schema') == 2:
        if digest(task['inference_identity']) != task['fingerprint'] or task['inference_identity'] != expected['inference_identity']:
            raise ValueError('Inference inputs changed; recorded attempt preserved without automatic rerun')
    else:
        binding = records.read(records.BENCH / 'config/cache-compatibility.json')['tasks'].get(task['fingerprint'])
        if not binding or binding['task'] != entry['directory'] + '/task.json':
            raise ValueError('Legacy cache has no validated migration binding')
        if records.sha(directory / 'task.json') != binding['task_sha256'] or records.sha(directory / 'completed.json') != binding['completed_sha256']:
            raise ValueError('Legacy task or completion receipt changed')
        if legacy_fingerprint(entry, binding['model'], task, expected) != task['fingerprint']:
            raise ValueError('Legacy cache input fingerprint mismatch')
        compatible = dict(expected['inference_identity'], environment=profile_settings(binding['compatible_profile']),
                          model=model_settings(binding['model']))
        if compatible != expected['inference_identity']:
            raise ValueError('Effective model or adapter changed; no compatible cache and no automatic rerun')
        # Non-fingerprint inputs were additionally bound during the migration.
        if binding['input_identity'] != expected['inference_identity']:
            raise ValueError('Effective task configuration changed; cached attempt remains preserved')
    archived = records.read(records.BENCH / 'config/environment-profile-archives.json')['profiles'].get(profile['fingerprint'])
    if archived:
        if archived['profile'] != profile:
            raise ValueError('Archived source profile mismatch')
        verify_files(records.ROOT, archived['files_sha256'])
    elif task.get('cache_schema') != 2:
        raise ValueError('Legacy source archive missing')
    result = records.read(directory / 'result.json')
    if result.get('environment_profile') != profile or result.get('model') != model['id']:
        raise ValueError('Cached model receipt mismatch')
    for key in ['binary_sha256', 'prompt_sha256']:
        if result.get(key) != task.get(key) or task.get(key) != expected.get(key):
            raise ValueError('Cached ' + key + ' mismatch')
    grade_path = directory / ('grade-framed.json' if (directory / 'grade-framed.json').exists() else 'grade.json')
    grade = records.read(grade_path)
    extension = {'cpp': 'cpp', 'rust': 'rs', 'go': 'go'}[entry['language']]
    if grade.get('candidate_source_sha256') and records.sha(directory / ('reconstructed.' + extension)) != grade['candidate_source_sha256']:
        raise ValueError('Graded candidate source changed')
    grading_key = digest(grading_identity(task, directory))
    recorded_grading_key = complete.get('grading_key') if task.get('cache_schema') == 2 else binding['grading_key']
    if recorded_grading_key != grading_key:
        raise ValueError('Grading inputs changed; saved inference and original grade preserved; offline regrading required')
    return {'status': 'cache_hit', 'fingerprint': task['fingerprint'], 'grading_key': recorded_grading_key,
            'inference_key': digest(expected['inference_identity']), 'recorded_environment_profile': profile['fingerprint'],
            'grade_sha256': records.sha(grade_path)}
