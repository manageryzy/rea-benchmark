"""Allowlisted registration/build evidence for reports; no admission or providers."""
import datetime
import copy
import json
from pathlib import Path

import action_cache
from . import registration, metrics
from .build_metadata import SavedBuilds, public_projection as public_build
from .measurements import Reader, digest, hash_value

ROOT = action_cache.ROOT


def empty():
    return {'status': 'not_recorded', 'policy_id': '', 'data_as_of': None, 'schedules': [], 'builds': [],
            'observations': [], 'invalid_records': 0, 'control_records_excluded': 0,
            'validation': {'passed': False, 'current': False, 'checks': 0, 'model_api_calls': 0}}


def public_projection(value):
    return {name: copy.deepcopy(value[name]) for name in empty()}


def validate(value):
    if value['status'] not in ('not_recorded', 'installed'):
        raise ValueError('Unknown registration report state')
    if value['status'] == 'not_recorded' and value != empty():
        raise ValueError('Missing registration evidence cannot gain measurements')
    controls = value['validation']
    advanced = any(row['metadata']['compiler_family'] in ('rustc', 'gc') or row['metadata']['optimization'] == '-O3'
                   for row in value['builds'])
    if controls['model_api_calls'] != 0 or controls['passed'] and controls['checks'] < (30 if advanced else 24):
        raise ValueError('Inconsistent registration controls')
    for row in value['schedules']:
        hash_value(row['registration_id'])
        if row['prospective_cells'] + row['prior_cells'] != row['scheduled_cells'] or row['parallel_models'] < 1:
            raise ValueError('Registered denominator changed')
        for bound in row['bindings']:
            hash_value(bound['binding_id'])
            if bound['bound_cells'] != row['scheduled_cells'] or bound['dispatch_intents'] > row['prospective_cells']:
                raise ValueError('Incomplete registration execution binding')
            for identity in bound['runtime_ids'] + bound['provider_ids']:
                hash_value(identity)
    if len({row['task_id'] for row in value['builds']}) != len(value['builds']):
        raise ValueError('Duplicate original build projection')
    for row in value['builds']:
        metadata = row['metadata']
        for name in ('build_id', 'binary_sha256', 'compiler_image_id', 'compiler_executable_sha256',
                     'binutils_image_id', 'flag_summary_sha256'):
            hash_value(metadata[name])
        if digest(action_cache.canonical(metadata['flag_summary'])) != metadata['flag_summary_sha256']:
            raise ValueError('Original public flag summary changed')
        if metadata['abi'] not in ('sysv_amd64', 'ms_x64', 'linux_x86_64_sysv', 'measured_not_disclosed', 'rust_native', 'go_abi_internal'):
            raise ValueError('Private or unsupported ABI label in public build metadata')
        for flag in metadata['flag_summary']:
            if flag.startswith(('-D', '-I')) or '/' in flag or 'original_' in flag or 'ABSENT' in flag:
                raise ValueError('Private compiler variation in public build metadata')
    if len({row['attempt_id'] for row in value['observations']}) != len(value['observations']):
        raise ValueError('Duplicate registration observations')
    for row in value['observations']:
        hash_value(row['attempt_id'])
        if 'admission_mode' in row and row['admission_mode'] not in (
                'strict_offline_acceptance', 'trusted_local; offline_acceptance_not_rechecked'):
            raise ValueError('Unknown observation admission mode')
        if row['status'] not in ('historical_unregistered', 'unverified', 'offline_control_excluded',
                'registered_inference_regraded', 'registered_before_dispatch', 'registered_grading_pending'):
            raise ValueError('Unknown observation registration state')
        if row['status'].startswith('registered_'):
            hash_value(row['registration_id'])
            hash_value(row['binding_id'])
        elif row['registration_id'] is not None or row['binding_id'] is not None:
            raise ValueError('Unverified observation gained registration identity')
    return value


def _graded_under_registration(reader, directory, bound):
    index = reader.json(directory + '/selected-grade.json')
    if not index:
        return None
    relative = str(action_cache.relative(index['path']))
    base = directory + '/' + str(Path(relative).parent)
    raw = reader.raw(directory + '/' + relative)
    if digest(raw) != index['sha256']:
        raise ValueError('Selected registered grade changed')
    manifest = reader.json(base + '/manifest.json')
    if (manifest['grading_id'] != index['grading_id']
            or digest(action_cache.canonical(manifest['action'])) != index['grading_id']):
        raise ValueError('Registered grade identity mismatch')
    if manifest['grading_spec_sha256'] != bound['scoring']['grading_spec_sha256']:
        return False
    saved = SavedBuilds(reader.read_bytes, root=reader.root)
    command = json.loads(saved.blob(manifest['action']['command_digest']))
    if (command['platform'] != bound['scoring']['environment']
            or command['arguments'][0] != bound['scoring']['grader_version']):
        return False
    for name, expected in bound['scoring']['inputs_sha256'].items():
        tree = manifest['action']['input_root_digest']
        parts = action_cache.relative(name).parts
        for index, part in enumerate(parts):
            node = json.loads(saved.blob(tree))
            entries = node['files'] if index == len(parts) - 1 else node['directories']
            matches = [item for item in entries if item['name'] == part]
            if len(matches) != 1:
                return False
            tree = matches[0]['digest']
        if tree['hash'] != expected:
            return False
    return True


def observation(reader, registry, directory, task):
    reference = task.get('measurement_registration')
    result = {'attempt_id': hash_value(task['fingerprint']), 'registration_id': None, 'binding_id': None,
              'status': 'historical_unregistered', 'registered_at': None}
    if not reference:
        return result
    try:
        schedule, created = registry.load(reference['registration_id'])
        ready, origin = registry.load(reference['registration_id'], reference['binding_id'])
        if created['offline_control'] or origin['offline_control']:
            return {**result, 'status': 'offline_control_excluded'}
        cells = [row for row in ready['cells'] if row['cell_id'] == reference['cell_id']]
        bound_ids = {row['cell_id'] for row in ready['cells']}
        scheduled_ids = {row['cell_id'] for row in schedule['cells']}
        per_cell = schedule.get('binding_mode') == 'per_cell' and ready.get('record_kind') == 'cell_execution_binding'
        coverage_invalid = ((len(ready['cells']) != 1 or not bound_ids <= scheduled_ids) if per_cell else
                            (len(ready['cells']) != len(schedule['cells']) or bound_ids != scheduled_ids))
        if len(cells) != 1 or not cells[0]['prospective'] or coverage_invalid:
            raise ValueError('Registration denominator or prospective scope changed')
        bound = cells[0]
        if (bound['fingerprint'] != task['fingerprint'] or registration.task_signature(task) != bound['task_signature']
                or task['runtime_attestation'] != bound['attestation']):
            raise ValueError('Saved task differs from its pre-run binding')
        intent = registry.read(registry.directory(reference['registration_id'], reference['binding_id']) +
                               '/dispatches/' + task['fingerprint'] + '.json')
        if (intent.get('phase') != 'before_provider_dispatch' or intent.get('fingerprint') != task['fingerprint']
                or any(intent.get(key) != reference[key] for key in ('registration_id', 'binding_id', 'cell_id'))
                or intent.get('task_signature') != bound['task_signature']):
            raise ValueError('Saved dispatch intent does not bind the task')
        matches = _graded_under_registration(reader, directory, bound)
        return {**result, 'registration_id': reference['registration_id'], 'binding_id': reference['binding_id'],
                'admission_mode': schedule.get('admission_mode', 'strict_offline_acceptance'),
                'status': 'registered_inference_regraded' if matches is False else
                          'registered_before_dispatch' if matches is True else 'registered_grading_pending',
                'registered_at': origin['created_at']}
    except (ValueError, OSError, KeyError, TypeError):
        return {**result, 'status': 'unverified'}


def for_report(read_bytes, measurements, *, root=ROOT):
    reader = Reader(root, read_bytes)
    registry = registration.Registry(root, read_bytes=read_bytes)
    result = {**empty(), 'status': 'installed', 'policy_id': metrics.policy(read_bytes, root=reader.root)['id']}
    receipt_path = reader.root / registration.RECEIPT.relative_to(ROOT)
    if receipt_path.is_file():
        value = reader.json(receipt_path.relative_to(reader.root).as_posix())
        expected = {name: digest(read_bytes(ROOT / name)) for name in registration.SOURCES}
        result['validation'] = {'passed': value.get('passed') is True and value.get('failed') == 0
            and type(value.get('checks')) is int and value['checks'] >= 30
            and value.get('provider_launches') == value.get('model_api_calls') == 0,
            'current': value.get('inputs_sha256') == expected, 'checks': value.get('checks', 0), 'model_api_calls': 0}
    for path in sorted(registry.path(registration.DIRECTORY).glob('*/record.json')):
        try:
            identity = path.parent.name
            schedule, created = registry.load(identity)
            if created['offline_control']:
                result['control_records_excluded'] += 1
                continue
            if schedule['record_kind'] != 'frozen_schedule' or len(schedule['cells']) != schedule['scheduled_cells']:
                raise ValueError('Invalid frozen measurement schedule')
            bindings = []
            for ready_path in sorted(path.parent.glob('bindings/*/record.json')):
                ready, origin = registry.load(identity, ready_path.parent.name)
                if origin['offline_control']:
                    result['control_records_excluded'] += 1
                    continue
                if (ready['record_kind'] != 'complete_execution_binding'
                        or len(ready['cells']) != schedule['scheduled_cells']
                        or {row['cell_id'] for row in ready['cells']} != {row['cell_id'] for row in schedule['cells']}):
                    raise ValueError('Incomplete execution binding')
                intents = []
                for intent_path in sorted(ready_path.parent.glob('dispatches/*.json')):
                    intent = registry.read(intent_path.relative_to(reader.root).as_posix())
                    if (intent.get('phase') != 'before_provider_dispatch'
                            or intent.get('registration_id') != identity or intent.get('binding_id') != ready_path.parent.name
                            or intent.get('fingerprint') != intent_path.stem):
                        raise ValueError('Invalid saved dispatch intent')
                    intents.append(intent['fingerprint'])
                bindings.append({'binding_id': ready_path.parent.name, 'created_at': origin['created_at'],
                    'bound_cells': len(ready['cells']), 'dispatch_intents': len(set(intents)),
                    'runtime_ids': sorted({row['attestation']['runtime']['identity'] for row in ready['cells'] if row['prospective']}),
                    'provider_ids': sorted({digest(action_cache.canonical(row['attestation']['provider'])) for row in ready['cells'] if row['prospective']})})
            result['schedules'].append({'registration_id': identity, 'registered_at': created['created_at'],
                'scheduled_cells': schedule['scheduled_cells'], 'prospective_cells': schedule['prospective_cells'],
                'prior_cells': schedule['scheduled_cells'] - schedule['prospective_cells'],
                'native_context_cells': schedule['native_context_cells'], 'policy_id': schedule['policy']['id'],
                'parallel_models': schedule['parallel_models'], 'bindings': bindings})
        except (ValueError, OSError, KeyError, TypeError):
            result['invalid_records'] += 1
    packages = {row['public_package_sha256']: row for row in measurements['cells']
                if row['public_package_sha256'] and row['preparation_status'] == 'prepared'}
    builds = SavedBuilds(read_bytes, root=reader.root)
    for path in sorted(reader.path('.cache/benchmark-capabilities/task-index').glob('*.json')):
        try:
            case = reader.json(path.relative_to(reader.root).as_posix())
            row = packages.get(case['public_package_sha256'])
            if row is None:
                continue
            manifest = reader.json(case['public_directory'] + '/manifest.json')
            metadata = public_build(builds.target(case), manifest)
            result['builds'].append({'task_id': row['task_id'], 'family_id': row['family_id'],
                'profile_id': row['profile_id'], 'metadata': metadata})
        except (ValueError, OSError, KeyError, TypeError):
            result['invalid_records'] += 1
    dates, observations = [], {}
    for path in sorted(reader.path('benchmarks/runs/capabilities-v1').glob('*/*/*/*/task.json')):
        try:
            directory = path.parent.relative_to(reader.root).as_posix()
            task = reader.json(path.relative_to(reader.root).as_posix())
            if task.get('offline_fixture'):
                continue
            item = observation(reader, registry, directory, task)
            previous = observations.get(item['attempt_id'])
            if previous is not None and previous != item:
                item = {**item, 'status': 'unverified', 'registration_id': None, 'binding_id': None, 'registered_at': None}
            observations[item['attempt_id']] = item
            receipt = reader.json(directory + '/result.json', {})
            for name in ('started_at', 'completed_at'):
                if receipt.get(name):
                    try:
                        date = datetime.datetime.fromisoformat(receipt[name].replace('Z', '+00:00'))
                        if date.tzinfo is not None:
                            dates.append(date.astimezone(datetime.timezone.utc))
                    except (ValueError, TypeError, AttributeError):
                        result['invalid_records'] += 1
        except (ValueError, OSError, KeyError, TypeError):
            result['invalid_records'] += 1
    result['data_as_of'] = max(dates).isoformat() if dates else None
    result['builds'].sort(key=lambda row: row['task_id'])
    result['observations'] = [item for _, item in sorted(observations.items())]
    return validate(result)
