"""Immutable capability schedules and complete pre-dispatch execution bindings.

Registration is provenance/admission, outside inference and grading identities.
Historical cache hits are never retroactively registered as new experiments.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import datetime
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
import cache
import records
import request_contract
import runtime_attestation
import preparation_session
from . import config, metrics, store, evidence_access, analysis_context
from .build_metadata import SavedBuilds, public_projection as public_build

ROOT = action_cache.ROOT
DIRECTORY = 'benchmarks/runs/capabilities-v1/registrations'
RECEIPT = ROOT / 'benchmarks/config/capability-registration-validation.json'
POLICY_SOURCES = ('benchmarks/scripts/capabilities/registration.py', 'benchmarks/scripts/capabilities/metrics.py',
    'benchmarks/config/capability-measurement-policy.json', 'benchmarks/scripts/capabilities/build_metadata.py',
    'benchmarks/scripts/capabilities/store.py', 'benchmarks/scripts/capabilities/coordinator.py',
    'benchmarks/scripts/run.py', 'benchmarks/scripts/cache.py', 'benchmarks/scripts/request_contract.py',
    'benchmarks/config/request-contract.json', 'benchmarks/scripts/runtime_attestation.py',
    'benchmarks/scripts/capabilities/evidence_access.py', 'benchmarks/scripts/capabilities/analysis_context.py')
POLICY_SOURCES += ('benchmarks/scripts/grading/metadata_admission.py', 'benchmarks/scripts/capabilities/artifact_format.py')
POLICY_SOURCES += ('benchmarks/scripts/capabilities/attempts.py',)
POLICY_SOURCES += ('benchmarks/scripts/preparation_session.py', 'benchmarks/scripts/read_session.py')
SOURCES = (*POLICY_SOURCES, 'benchmarks/scripts/capabilities/registration_report.py',
           'benchmarks/scripts/check_capability_registration.py')
_controls = ContextVar('capability_registration_controls', default=False)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprints(names=SOURCES):
    return {name: digest((ROOT / name).read_bytes()) for name in names}


@contextmanager
def validation_controls():
    token = _controls.set(True)
    try:
        yield
    finally:
        _controls.reset(token)


from startup_policy import offline_gate

@offline_gate
def require_validation():
    if _controls.get():
        return
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.is_file() else {}
    if (value.get('passed') is not True or value.get('failed') != 0 or value.get('model_api_calls') != 0
            or value.get('provider_launches') != 0 or value.get('inputs_sha256') != fingerprints()
            or type(value.get('checks')) is not int or value['checks'] < 42):
        raise RuntimeError('Registration controls missing or stale; run make check-capability-registration')
    from .evidence import require_validation as coordinator_gate
    coordinator_gate()


def scoring_state(task):
    return preparation_session.scoring(task, lambda: _scoring_state(task))


def _scoring_state(task):
    value, inputs, environment = store.fixed_inputs(task)
    contract = {'grader_version': store.VERSION, 'environment': environment,
                'inputs_sha256': {name: digest(raw) for name, raw in sorted(inputs.items())}}
    return {**contract, 'contract_sha256': digest(action_cache.canonical(contract)),
            'grading_spec_sha256': task['grading_spec_sha256'], 'kind': value['kind']}


def task_signature(task):
    fields = ('id', 'family_id', 'profile_id', 'kind', 'source_language', 'output_language', 'artifact_schema',
              'capability_ids', 'binary_sha256', 'public_package_sha256', 'grading_spec_sha256', 'build_id',
              'model', 'harness', 'reasoning', 'condition', 'fingerprint', 'inference_identity', 'request_contract')
    value = {key: task[key] for key in fields}
    if 'evidence_access' in task:
        value['evidence_access'] = evidence_access.mode(task['evidence_access'])
    if 'analysis_context_policy' in task:
        value['analysis_context_policy'] = task['analysis_context_policy']
    if 'artifact_format_policy' in task:
        value['artifact_format_policy'] = task['artifact_format_policy']
    if 'inference_trial' in task:
        value.update(inference_trial=task['inference_trial'], directory=task['directory'])
    return digest(action_cache.canonical(value))


def cell_id(entry):
    return digest(action_cache.canonical([entry['case'], entry['model'], entry['configuration_sha256']]))


def seal(model, configuration, runtime, provider):
    configuration = copy.deepcopy(configuration)
    configuration['harness']['request_contract'] = request_contract.build(
        provider, model, configuration['gateway'], configuration['harness'])
    value = {'schema_version': 1, 'runtime': copy.deepcopy(runtime), 'provider': copy.deepcopy(provider),
             'configuration': configuration}
    value['identity'] = digest(action_cache.canonical(value))
    return runtime_attestation.validate(value)


class Registry:
    def __init__(self, root=None, *, read_bytes=Path.read_bytes):
        self.root = Path(root or ROOT).resolve()
        self.read_bytes = read_bytes
        self.builds = SavedBuilds(read_bytes)

    def path(self, relative):
        path = self.root.joinpath(*action_cache.relative(relative).parts)
        if not path.resolve().is_relative_to(self.root) or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Registration path escapes its immutable root')
        return path

    def read(self, relative):
        path = self.path(relative)
        if path.stat().st_size > 32 * 1024**2:
            raise ValueError('Registration record exceeds its declared bound')
        return json.loads(self.read_bytes(path))

    def directory(self, registration_id, binding_id=None):
        for identity in (registration_id, binding_id):
            if identity is not None:
                action_cache.valid_digest({'hash': identity, 'size_bytes': 0})
        return DIRECTORY + '/' + registration_id + ('/bindings/' + binding_id if binding_id else '')

    def publish(self, document, *, registration_id=None):
        identity = digest(action_cache.canonical(document))
        relative = self.directory(registration_id or identity, identity if registration_id else None)
        destination = self.path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with tempfile.TemporaryDirectory(prefix='pending-', dir=destination.parent) as temporary:
                work = Path(temporary)
                (work / 'record.json').write_bytes(action_cache.canonical(document))
                (work / 'created.json').write_bytes(action_cache.canonical({
                    'identity': identity, 'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'offline_control': _controls.get()}))
                # Filesystem access errors can be transient during a directory
                # rename. Retry only bounded local publication, not
                # model work; load() below still authenticates the winner.
                import errno
                import time
                for attempt in range(6):
                    try:
                        work.rename(destination)
                        break
                    except OSError as error:
                        if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
                            break
                        if error.errno not in (errno.EACCES, errno.EBUSY, errno.ENOENT):
                            raise
                        if destination.exists():
                            break
                        if attempt == 5 or not work.is_dir() or not destination.parent.is_dir():
                            raise
                        time.sleep(0.02 * 2**attempt)
        saved, created = self.load(registration_id or identity, identity if registration_id else None)
        if saved != document:
            raise ValueError('Conflicting immutable registration')
        return identity

    def load(self, registration_id, binding_id=None):
        relative = self.directory(registration_id, binding_id)
        value, created = self.read(relative + '/record.json'), self.read(relative + '/created.json')
        expected = binding_id or registration_id
        if digest(action_cache.canonical(value)) != expected or created.get('identity') != expected:
            raise ValueError('Registration content identity changed')
        if type(created.get('offline_control')) is not bool:
            raise ValueError('Registration origin is missing')
        datetime.datetime.fromisoformat(created['created_at'])
        if binding_id and value.get('registration_id') != registration_id:
            raise ValueError('Execution binding belongs to another registration')
        return value, created

    def context(self, selected, selection):
        settings, families = config.load()
        models = records.models()
        caps = [row for row in selected if row.get('suite') == 'capabilities-v1']
        profiles = {row['id']: row for row in settings['profiles']}
        definitions = {row['id']: row for row in families['families']}
        from .tasks import declared
        from .coordinator import _namespace
        declarations = {row['task_id']: row for row in declared()}
        access = evidence_access.validate_selection(selection, list(declarations.values()))
        rows = []
        for entry in caps:
            if (entry['case'] not in declarations or entry['model'] not in models
                    or entry['configuration_sha256'] != _namespace(settings, models[entry['model']], access)
                    or evidence_access.mode(entry.get('evidence_access', 'binary')) != access
                    or entry['condition'] != settings['condition']):
                raise ValueError('Measurement cell differs from its configured model/task condition')
            declaration = declarations[entry['case']]
            profile = declaration['profile']
            if not settings['inference_enabled'] or not profiles[profile['id']]['enabled']:
                raise RuntimeError('Registration cannot enable a disabled capability condition')
            rows.append({'cell_id': cell_id(entry), 'case': entry['case'], 'model': entry['model'],
                'definition': {'profile': {key: value for key, value in profile.items() if key != 'enabled'},
                               'family': definitions[declaration['family_id']],
                               'convention': declaration.get('convention'), 'absent': declaration['absent']},
                'model_settings': cache.model_settings(models[entry['model']]),
                **({'authorized_retry': entry['authorized_retry']} if 'authorized_retry' in entry else {}),
                'configuration_sha256': entry['configuration_sha256']})
        if len({row['cell_id'] for row in rows}) != len(rows):
            raise ValueError('Duplicate cell in the frozen measurement denominator')
        return {'schema_version': 1, 'scope': 'capabilities-v1', 'policy': metrics.policy(),
            'implementation_sha256': fingerprints(POLICY_SOURCES),
            'selection': {**{key: selection.get(key) for key in ('models', 'suites', 'experiments', 'cases', 'condition', 'capabilities')},
                          **({'authorized_retries': selection['authorized_retries']} if selection.get('authorized_retries') else {}),
                          **({'evidence_access': access} if access != 'binary' else {})},
            'parallel_models': selection['execution']['max_parallel_models'],
            'limits': settings['limits'], 'runtime': settings['runtime'], 'condition': settings['condition'],
            'cells': sorted(rows, key=lambda row: row['cell_id'])}

    def begin(self, selected, selection, *, offline=False):
        import startup_policy
        caps = [row for row in selected if row.get('suite') == 'capabilities-v1']
        prospective = [row for row in caps if row['status'] in ('not_started', 'preparation_pending', 'preparation_invalid')
                       or startup_policy.trusted() and row['status'] == 'preparation_unchecked']
        if offline or not prospective:
            return None
        require_validation()
        context = self.context(selected, selection)
        initial = {cell_id(row): row for row in caps}
        for row in context['cells']:
            entry = initial[row['cell_id']]
            row['initial_status'] = entry['status']
            row['prospective'] = entry in prospective
            row['initial_public_package_sha256'] = entry.get('capability_case', {}).get('public_package_sha256')
        document = {**context, 'record_kind': 'frozen_schedule',
                    'native_context_cells': len(selected) - len(caps),
                    'scheduled_cells': len(caps), 'prospective_cells': len(prospective)}
        if startup_policy.trusted():
            document['binding_mode'] = 'per_cell'
            document['admission_mode'] = 'trusted_local; offline_acceptance_not_rechecked'
        identity = self.publish(document)
        return {'registration_id': identity, 'binding_id': None}

    def complete_cell(self, reference, entry, model):
        """Bind only the next cell against the full, already frozen denominator."""
        identity = reference['registration_id']
        schedule, _ = self.load(identity)
        if schedule.get('binding_mode') != 'per_cell':
            raise ValueError('Per-cell binding requires an explicit streaming schedule')
        key = cell_id(entry)
        rows = [row for row in schedule['cells'] if row['cell_id'] == key]
        if len(rows) != 1 or not rows[0]['prospective']:
            raise ValueError('Cell is not in the frozen measurement schedule')
        row = rows[0]
        if cache.model_settings(model) != row['model_settings']:
            raise ValueError('Selected model changed after scheduling')
        from .coordinator import prepare
        task = preparation_session.resolved_task(entry, model)
        if task is None:
            task = prepare(entry, model, materialize=False)
        case = entry['capability_case']
        if task.get('offline_fixture') or task['fingerprint'] != entry['resolved_fingerprint']:
            raise ValueError('Cell binding does not match resolved real inputs')
        manifest = json.loads((ROOT / case['public_directory'] / 'manifest.json').read_bytes())
        target = self.builds.target(case)
        bound = {'cell_id': key, 'prospective': True, 'public_task_id': manifest['task_id'],
            'family_id': case['family_id'], 'profile_id': case['profile_id'], 'model': model['id'],
            'public_package_sha256': case['public_package_sha256'], 'binary_sha256': case['binary_sha256'],
            'build': target, 'public_build': public_build(target, manifest),
            'task_signature': task_signature(task), 'fingerprint': task['fingerprint'],
            'attestation': task['runtime_attestation'], 'scoring': scoring_state(task)}
        document = {'schema_version': 1, 'record_kind': 'cell_execution_binding',
            'registration_id': identity, 'implementation_sha256': schedule['implementation_sha256'], 'cells': [bound]}
        binding_id = self.publish(document, registration_id=identity)
        return {**entry, 'measurement_registration': {'registration_id': identity,
                 'binding_id': binding_id, 'cell_id': key}}

    def complete(self, reference, selected, selection, prepare_task):
        if reference is None:
            return selected, None
        identity = reference['registration_id']
        schedule, _ = self.load(identity)
        context = self.context(selected, selection)
        original = copy.deepcopy(schedule)
        for key in ('record_kind', 'native_context_cells', 'scheduled_cells', 'prospective_cells'):
            original.pop(key)
        for row in original['cells']:
            for key in ('initial_status', 'prospective', 'initial_public_package_sha256'):
                row.pop(key)
        if context != original:
            raise RuntimeError('Measurement selection, definitions or policy changed during preparation')
        entries = {cell_id(row): row for row in selected if row.get('suite') == 'capabilities-v1'}
        models = records.models()
        routes, runtimes, scoring, bindings, prepared = {}, {}, {}, [], []
        settings = config.load()[0]
        known_tasks = {entries[row['cell_id']]['capability_case']['id'] for row in schedule['cells']
                       if row['prospective'] and entries[row['cell_id']]['capability_case']['kind'] in analysis_context.KNOWN}
        # Missing controls and unsupported caps fail before analysis-runtime
        # inventory or any provider client. The full tool list is sealed below.
        if not _controls.get():
            from .coordinator import require_case_validation
            validated = set()
            for row in schedule['cells']:
                if row['prospective']:
                    case = entries[row['cell_id']]['capability_case']
                    # Gates depend on the task kind (and matched output language),
                    # not on which model will receive the same frozen package.
                    key = (case['kind'], case.get('output_language'))
                    if key not in validated:
                        require_case_validation(case)
                        validated.add(key)
            if analysis_context.enabled(settings['runtime']) and known_tasks:
                preparation_session.require_context(task_ids=known_tasks)
        # Resolve every prospective participant before any queue can dispatch.
        for row in schedule['cells']:
            if not row['prospective']:
                continue
            model = models[row['model']]
            if model['id'] not in routes:
                routes[model['id']] = runtime_attestation.provider(model)
            request_contract.build(routes[model['id']], model,
                {'max_tool_calls': settings['limits']['max_tool_calls']},
                {**settings['limits'], 'tool_timeout_ms': settings['runtime']['tool_timeout_ms']})
        for row in schedule['cells']:
            if row['prospective']:
                model = models[row['model']]
                resolved = entries[row['cell_id']].get('_resolved_attestation')
                if resolved is not None:
                    # Reuse the inventory that resolved this exact action in this
                    # batch. The provider is still resolved again above, and
                    # dispatch freshly hashes content before and after the call.
                    proof = runtime_attestation.validate(resolved)
                    if (not entries[row['cell_id']].get('resolved_fingerprint')
                            or proof['provider'] != routes[model['id']]
                            or proof['runtime']['manifest'].get('harness') != model['harness']):
                        raise ValueError('Resolved runtime/provider differs before measurement binding')
                    previous = runtimes.setdefault(model['harness'], proof['runtime'])
                    if previous != proof['runtime']:
                        raise ValueError('Selected actions resolved different runtimes for one harness')
                elif model['harness'] not in runtimes:
                    runtimes[model['harness']] = runtime_attestation.current_runtime(model['harness'])
        if not _controls.get() and analysis_context.enabled(settings['runtime']):
            for harness in {models[row['model']]['harness'] for row in schedule['cells']
                            if row['prospective'] and entries[row['cell_id']]['capability_case']['id'] in known_tasks}:
                preparation_session.require_context(runtimes[harness], task_ids=known_tasks)
        for row in schedule['cells']:
            entry = entries[row['cell_id']]
            case = entry['capability_case']
            if row['initial_public_package_sha256'] is not None and case['public_package_sha256'] != row['initial_public_package_sha256']:
                raise RuntimeError('Previously frozen target changed before measurement binding')
            manifest = json.loads((ROOT / case['public_directory'] / 'manifest.json').read_bytes())
            target = self.builds.target(case)
            bound = {'cell_id': row['cell_id'], 'prospective': row['prospective'],
                     'public_task_id': manifest['task_id'], 'family_id': case['family_id'], 'profile_id': case['profile_id'],
                     'model': row['model'], 'public_package_sha256': case['public_package_sha256'],
                     'binary_sha256': case['binary_sha256'], 'build': target,
                     'public_build': public_build(target, manifest)}
            if row['prospective']:
                model = models[row['model']]
                def attestor(chosen, configuration):
                    if cache.model_settings(chosen) != row['model_settings']:
                        raise ValueError('Registered model settings changed')
                    return seal(chosen, configuration, runtimes[chosen['harness']], routes[chosen['id']])
                task = preparation_session.resolved_task(entry, model)
                if task is None:
                    task = prepare_task(entry, model, materialize=False, attestor=attestor)
                elif attestor(model, task['runtime_attestation']['configuration']) != task['runtime_attestation']:
                    raise ValueError('Prepared task attestation changed before binding')
                if task.get('offline_fixture'):
                    raise ValueError('Offline fixtures cannot become measured observations')
                if case['id'] not in scoring:
                    scoring[case['id']] = scoring_state(task)
                elif scoring_state(task) != scoring[case['id']]:
                    raise RuntimeError('Scoring contract changed while sealing the selected participants')
                prepared.append(task)
                bound.update(task_signature=task_signature(task), fingerprint=task['fingerprint'],
                             attestation=task['runtime_attestation'], scoring=scoring[case['id']])
            bindings.append(bound)
        if self.context(selected, selection) != context:
            raise RuntimeError('Measurement inputs changed before binding publication')
        if any(scoring_state(task) != scoring[task['id']] for task in prepared):
            raise RuntimeError('Scoring contract changed before binding publication')
        ready = {'schema_version': 1, 'record_kind': 'complete_execution_binding', 'registration_id': identity,
                 'cells': bindings, 'implementation_sha256': context['implementation_sha256']}
        binding_id = self.publish(ready, registration_id=identity)
        reference = {'registration_id': identity, 'binding_id': binding_id}
        prospective = {row['cell_id'] for row in schedule['cells'] if row['prospective']}
        return [{**row, 'measurement_registration': {**reference, 'cell_id': cell_id(row)}}
                if row.get('suite') == 'capabilities-v1' and cell_id(row) in prospective else row for row in selected], reference

    def binding(self, reference):
        schedule, origin = self.load(reference['registration_id'])
        ready, bound_origin = self.load(reference['registration_id'], reference['binding_id'])
        bound_ids = {row['cell_id'] for row in ready['cells']}
        scheduled_ids = {row['cell_id'] for row in schedule['cells']}
        per_cell = schedule.get('binding_mode') == 'per_cell' and ready.get('record_kind') == 'cell_execution_binding'
        coverage_invalid = ((len(ready['cells']) != 1 or not bound_ids <= scheduled_ids) if per_cell else
                            (len(ready['cells']) != len(schedule['cells']) or bound_ids != scheduled_ids))
        if ready['implementation_sha256'] != schedule['implementation_sha256'] or coverage_invalid:
            raise ValueError('Execution binding does not preserve its complete scheduled denominator')
        if (origin['offline_control'] or bound_origin['offline_control']) and not _controls.get():
            raise ValueError('Offline registration controls cannot authorize a provider')
        rows = [row for row in ready['cells'] if row['cell_id'] == reference['cell_id']]
        if len(rows) != 1 or not rows[0]['prospective']:
            raise ValueError('Measurement cell was not registered before dispatch')
        return schedule, ready, rows[0]

    def attestor(self, reference):
        _, _, bound = self.binding(reference)
        frozen = bound['attestation']
        def attest(model, configuration):
            value = seal(model, configuration, frozen['runtime'], frozen['provider'])
            if value != frozen:
                raise ValueError('Effective settings changed after measurement registration')
            return value
        return attest

    def verify_task(self, task):
        reference = task['measurement_registration']
        schedule, ready, bound = self.binding(reference)
        if (task_signature(task) != bound['task_signature'] or task.get('offline_fixture')
                or task['fingerprint'] != bound['fingerprint'] or scoring_state(task) != bound['scoring']
                or fingerprints(POLICY_SOURCES) != schedule['implementation_sha256']):
            raise ValueError('Registered task, scoring or admission implementation changed')
        return bound

    def claim(self, task):
        bound = self.verify_task(task)
        reference = task['measurement_registration']
        relative = self.directory(reference['registration_id'], reference['binding_id']) + '/dispatches/' + task['fingerprint'] + '.json'
        value = {**reference, 'fingerprint': task['fingerprint'], 'task_signature': bound['task_signature'],
                 'phase': 'before_provider_dispatch', 'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as stream:
            json.dump(value, stream, sort_keys=True)
        return value
