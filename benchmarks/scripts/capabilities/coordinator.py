"""Capability hooks for the existing plan/run/cache coordinator, not a second runner."""
import hashlib
import json
import os
from pathlib import Path

import action_cache
import cache
import environment_profile
import records
import runtime_attestation
from grading.metadata_admission import metadata_validation_cache
from . import analysis_context, config, tasks, public, store, preparation, evidence_access, artifact_format, attempts

ROOT = action_cache.ROOT


def model_prompt(prompt, manifest, *, format_policy=None):
    """Supply the public premises through the adapters' only text-input channel.

    The frozen four-file package stays unchanged. This derived prompt is an
    inference input in its own right, so its exact bytes enter the action key.
    Models have no filesystem tool with which to read manifest.json themselves.
    """
    if not isinstance(json.loads(manifest), dict):
        raise ValueError('Public task manifest must be a JSON object')
    prompt.decode('utf-8')
    manifest.decode('utf-8')
    return (prompt + b'\n\nSupplied public task manifest (known premises and artifact binding):\n```json\n'
            + manifest + (b'' if manifest.endswith(b'\n') else b'\n') + b'```\n'
            + artifact_format.notice(format_policy))


def _namespace(settings, model, access='binary'):
    value = {'limits': settings['limits'], 'runtime': settings['runtime'],
             'model': cache.model_settings(model), 'condition': settings['condition']}
    if evidence_access.mode(access) != 'binary':
        value['evidence_access'] = access
    return action_cache.digest(action_cache.canonical(value))['hash']


def attempt_directory(model, case, namespace):
    return ROOT / 'benchmarks/runs/capabilities-v1' / model['id'] / case['id'] / case['public_package_sha256'] / namespace


def _entry(model, declaration, settings, state, access='binary'):
    profile = declaration['profile']
    source = profile['variant'] if profile['kind'] == 'native_abi' else (
        profile['original_compiler'] if profile['kind'] == 'matched_buffer' else 'cpp')
    if source == 'clang20': source = 'cpp'
    result = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1', 'case': declaration['task_id'],
        'model': model['id'], 'language': source, 'condition': settings['condition'],
        'preparation_status': state['status'], 'configuration_sha256': _namespace(settings, model, access),
        **({'evidence_access': access} if access != 'binary' else {})}
    if state['status'] != 'ready':
        return {**result, 'status': 'preparation_' + state['status'], 'directory': None,
                'preparation_reason': state.get('reason')}
    case = state['case']
    directory = attempt_directory(model, case, result['configuration_sha256'])
    trial = str(directory.relative_to(ROOT))
    return {**result, 'status': attempts.status(directory), 'directory': trial,
            'inference_trial': trial, 'capability_case': case}


@metadata_validation_cache()
def resolve_selected(selected, *, offline=False):
    """Resolve action/status before a schedule decides which cells are prospective.

    This is local inventory/route resolution, never an inference request. Planning
    stays inexpensive; the batch lock owns this step. Missing preparations are
    resolved by the existing preparation phase before their execution binding.
    """
    chosen = [row for row in selected if row.get('suite') == 'capabilities-v1'
              and not row.get('status', '').startswith('preparation_')
              and row.get('preparation_status') == 'ready' and 'inference_trial' in row
              and 'resolved_fingerprint' not in row]
    if not chosen:
        return selected
    from .registration import seal
    import preparation_session
    preparation_session.declare_context({entry['case'] for entry in chosen
        if entry['capability_case']['kind'] in analysis_context.KNOWN})
    models = records.models()
    routes, runtimes, validated = {}, {}, set()
    for entry in chosen:
        if not offline:
            case = entry['capability_case']
            gate = (case['kind'], case.get('output_language'))
            if gate not in validated:
                require_case_validation(case)
                validated.add(gate)
    def attest(model, configuration):
        if model['id'] not in routes:
            routes[model['id']] = runtime_attestation.provider(model)
        # Fail unsupported request caps before inventorying the full runtime.
        import request_contract
        request_contract.build(routes[model['id']], model, configuration['gateway'], configuration['harness'])
        if model['harness'] not in runtimes:
            runtimes[model['harness']] = runtime_attestation.current_runtime(model['harness'])
        return seal(model, configuration, runtimes[model['harness']], routes[model['id']])
    resolved = {}
    for entry in chosen:
        task = prepare(entry, models[entry['model']], materialize=False, attestor=attest)
        preparation_session.remember_task(entry, models[entry['model']], task)
        resolved[id(entry)] = {**entry, 'directory': task['directory'],
            'status': attempts.status(ROOT / task['directory']), 'resolved_fingerprint': task['fingerprint'],
            **({'_resolved_attestation': task['runtime_attestation']} if task.get('runtime_attestation') else {})}
    return [resolved.get(id(row), row) for row in selected]


def entries(model, *, selection=None, inventory=None, deferred=False):
    """Enumerate declarations; deferred rows make no claim of cache validity."""
    import benchmark_config
    settings, _ = config.load()
    access = evidence_access.validate_selection(selection, tasks.declared()) if selection is not None else 'binary'
    if (not settings['inference_enabled'] or selection is not None and
            (not selection.get('capabilities', True) or selection['condition'] not in ('active', settings['condition']))):
        return []
    result = []
    inventory = {} if inventory is None else inventory
    for declaration in tasks.declared():
        if not declaration['profile']['enabled']:
            continue
        selector = {'suite': 'capabilities-v1', 'experiment': 'capabilities-v1', 'case': declaration['task_id'], 'model': model['id']}
        if selection is not None and not benchmark_config.accepts(selection, selector):
            continue
        authorization = next((row for row in (selection or {}).get('authorized_retries', [])
            if row['cell'] == [model['id'], 'capabilities-v1', declaration['task_id']]), None)
        extra = {'authorized_retry': authorization} if authorization is not None else {}
        name = declaration['task_id']
        if os.environ.get('REA_BENCHMARK_OFFLINE') == '1':
            # No result can be restored when this cell has never produced one.
            # Inspect all action-addressed siblings, not just the legacy path.
            base = ROOT / 'benchmarks/runs/capabilities-v1' / model['id'] / name
            reusable = False
            for result_path in base.glob('*/*/result.json'):
                saved = records.read(result_path.parent / 'task.json', {})
                if (saved.get('evidence_access', 'binary') == access
                        and runtime_controls_current(saved)):
                    reusable = True
                    break
            if not reusable:
                result.append({**selector, 'condition': settings['condition'],
                    'language': declaration['profile'].get('original_compiler', declaration['profile'].get('variant', 'cpp')),
                    'status': 'not_started', 'directory': None, '_offline_missing': True,
                    'preparation_status': 'not_inspected_cache_miss'})
                continue
        if deferred:
            result.append({**_entry(model, declaration, settings, {'status': 'unchecked'}, access), **extra})
            continue
        if name not in inventory:
            inventory[name] = preparation.inspect(declaration)
        result.append({**_entry(model, declaration, settings, inventory[name], access), **extra})
    return result


@metadata_validation_cache()
def inspect_selected(selected):
    """Resolve deferred metadata under the batch lock, once per distinct case.

    No deferred row is a cache hit or permission to launch. Keep the existing
    preparation AC/CAS and frozen-package checks before schedule registration.
    Already inspected callers keep their original evidence and status.
    """
    chosen = [row for row in selected if row.get('preparation_status') == 'unchecked']
    if not chosen:
        return selected
    models = records.models()
    inventory, resolved = {}, {}
    for model_id in dict.fromkeys(row['model'] for row in chosen):
        group = [row for row in chosen if row['model'] == model_id]
        for access in dict.fromkeys(row.get('evidence_access', 'binary') for row in group):
            rows = [row for row in group if row.get('evidence_access', 'binary') == access]
            conditions = {row['condition'] for row in rows}
            if len(conditions) != 1:
                raise ValueError('Mixed capability conditions in deferred selection')
            selection = {'models': [model_id], 'suites': ['capabilities-v1'],
                'experiments': ['capabilities-v1'], 'cases': [row['case'] for row in rows],
                'condition': rows[0]['condition'], 'capabilities': True, 'evidence_access': access}
            if any('authorized_retry' in row for row in rows):
                selection['authorized_retries'] = [row['authorized_retry'] for row in rows if 'authorized_retry' in row]
            current = {row['case']: row for row in entries(models[model_id], selection=selection, inventory=inventory)}
            for original in rows:
                row = current.get(original['case'])
                if row is None or (not row.get('_offline_missing') and
                        row.get('configuration_sha256') != original['configuration_sha256']):
                    raise RuntimeError('Capability configuration changed after selection: ' + original['case'])
                resolved[id(original)] = row
    return [resolved.get(id(row), row) for row in selected]


def runtime_controls_current(task):
    """Cheap rejection only: this never certifies a runtime or admits a hit."""
    proof = task.get('runtime_attestation')
    if not proof:
        return True
    runtime_attestation.validate(proof)
    component = proof['runtime']['manifest']['components'].get('attestation-control')
    if not component:
        return False
    entries = runtime_attestation.component_entries(component, action_cache.Cache().get)
    return all(entries.get('workspace/' + name, {}).get('sha256') ==
               hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
               for name in runtime_attestation.CONTROL)


@metadata_validation_cache()
def prepare_selected(selected):
    """Resolve preparation once per selected case, under the existing batch lock."""
    chosen = [row for row in selected if row.get('suite') == 'capabilities-v1' and not row.get('_offline_missing')]
    if not chosen:
        return selected
    settings, _ = config.load()
    if not settings['inference_enabled']:
        raise RuntimeError('Capability inference was disabled after planning; no preparation or model launch')
    frozen_configuration = action_cache.canonical(settings)
    declarations = {row['task_id']: row for row in tasks.declared()}
    models = records.models()
    states = {}
    for entry in chosen:
        name = entry['case']
        row = declarations.get(name)
        if row is None or not row['profile']['enabled']:
            raise RuntimeError('Selected capability profile was removed or disabled: ' + name)
        if (entry['condition'] != settings['condition']
                or entry.get('configuration_sha256') != _namespace(settings, models[entry['model']], entry.get('evidence_access', 'binary'))):
            raise RuntimeError('Selected capability configuration changed after planning: ' + name)
        if name not in states:
            states[name] = preparation.inspect(row)
            if states[name]['status'] == 'invalid':
                raise ValueError('Invalid capability preparation for ' + name + ': ' + states[name]['reason'])
    import preparation_session
    from contextlib import nullcontext
    pending = any(state['status'] == 'pending' for state in states.values())
    with preparation_session.input_writes() if pending else nullcontext():
        if any(state['status'] == 'pending' for state in states.values()):
            from grading import toolchains
            toolchains.build('cpp')
            for language in sorted({declarations[name]['profile']['variant'] for name, state in states.items()
                                    if state['status'] == 'pending' and declarations[name]['profile']['kind'] == 'native_abi'}):
                toolchains.build(language)
            for language in sorted({declarations[name]['profile']['original_compiler'] for name, state in states.items()
                                    if state['status'] == 'pending' and declarations[name]['profile']['kind'] == 'matched_buffer'} - {'clang20'}):
                toolchains.build(language)
            if any(state['status'] == 'pending' and declarations[name]['profile']['kind'] == 'matched_buffer'
                   and declarations[name]['profile']['original_compiler'] == 'clang20' for name, state in states.items()):
                from .baselines import tooling as matched_tooling
                matched_tooling.setup()
            build = tasks.build
            build.setup_tools()
            if any(state['status'] == 'pending' and declarations[name]['profile']['kind'] == 'optimized_localization'
                   and declarations[name]['profile']['variant'] == 'lto' for name, state in states.items()):
                from .optimized_locations import tooling
                tooling.setup()
        for name, state in states.items():
            if state['status'] == 'pending':
                case = preparation.prepare(declarations[name])
                states[name] = {'status': 'ready', 'case': case}
    for entry in chosen:
        expected = entry.get('capability_case')
        if expected is not None and expected['public_package_sha256'] != states[entry['case']]['case']['public_package_sha256']:
            raise RuntimeError('Capability public package changed during preparation; plan again before provider work')
    # A run owns only its selected cases. The full catalogue inventory belongs
    # to make prepare-capabilities, not every small batch or cache-only replay.
    if (action_cache.canonical(config.load()[0]) != frozen_configuration
            or any(_namespace(settings, records.models()[row['model']], row.get('evidence_access', 'binary')) != row['configuration_sha256'] for row in chosen)):
        raise RuntimeError('Capability configuration changed during preparation; no provider launch')
    result = []
    for row in selected:
        if row.get('suite') != 'capabilities-v1' or row.get('_offline_missing'):
            result.append(row)
            continue
        refreshed = _entry(models[row['model']], declarations[row['case']], settings, states[row['case']], row.get('evidence_access', 'binary'))
        if 'authorized_retry' in row:
            refreshed['authorized_retry'] = row['authorized_retry']
        if row.get('resolved_fingerprint') and refreshed.get('inference_trial') == row.get('inference_trial'):
            refreshed.update({key: row[key] for key in ('directory', 'status', 'resolved_fingerprint',
                '_resolved_attestation') if key in row})
        result.append(refreshed)
    return result


def saved_entries():
    """Historical typed attempts remain regradable with inference disabled."""
    base = ROOT / 'benchmarks/runs/capabilities-v1'
    result = []
    for path in sorted(base.glob('*/*/*/*/task.json')):
        task = records.read(path)
        directory = path.parent
        if (task.get('track') != 'capability' or task.get('offline_fixture')
                or not (directory / 'result.json').exists()
                or any((directory / name).exists() for name in ('harness-failure.json', 'interrupted.json'))):
            continue
        result.append({'experiment': 'capabilities-v1', 'suite': 'capabilities-v1', 'case': task['id'],
                       'model': task['model'], 'directory': str(directory.relative_to(ROOT)), 'task': task})
    return result


def regrade_saved(selection, grade_saved):
    import benchmark_config
    outcomes, failures, preserved = [], [], {}
    for entry in saved_entries():
        if not benchmark_config.accepts(selection, entry):
            continue
        directory = ROOT / entry['directory']
        for path in directory.rglob('*'):
            if (path.is_file() and path.relative_to(directory).parts[0] not in ('grades', 'accounting')
                    and path.name not in ('selected-grade.json', 'selected-accounting.json')):
                preserved[str(path.relative_to(ROOT))] = records.sha(path)
        try:
            grade = grade_saved(directory, entry['task'])
            import run
            run.account_saved(entry, directory)
            outcomes.append({'directory': entry['directory'], 'grading_id': grade['grading_id'],
                             'selected_passed': grade['passed'], 'selected_total': grade['total'],
                             'grading_version': store.VERSION})
        except (ValueError, RuntimeError, OSError) as error:
            failures.append({'directory': entry['directory'], 'error': str(error)})
    if any(records.sha(ROOT / name) != expected for name, expected in preserved.items()):
        raise RuntimeError('Typed offline grading changed original inference artifacts')
    return outcomes, failures, preserved


from startup_policy import offline_gate

@offline_gate
def require_case_validation(case):
    """All relevant task gates precede provider resolution for a registered batch."""
    import preparation_session
    return preparation_session.once('case-validation', [case['kind'], case.get('output_language')],
                                    lambda: _require_case_validation(case))


def _require_case_validation(case):
    from .evidence import require_validation
    require_validation()
    if case['kind'] == 'localization':
        import check_capabilities
        receipt = records.read(ROOT / 'benchmarks/config/capability-validation.json', {})
        if (receipt.get('passed') is not True or receipt.get('model_api_calls') != 0
                or receipt.get('inputs_sha256') != check_capabilities.source_hashes()):
            raise RuntimeError('Localization controls missing or stale; run make check-capabilities')
        from grading.structured.evidence import require_validation as grade_gate
    elif case['kind'] == 'matched_buffer':
        from .baselines import task_runtime
        grade_gate = lambda: task_runtime.require_validation(case['output_language'])
    elif case['kind'] == 'optimized_localization':
        from .optimized_locations.evidence import require_validation as grade_gate
    elif case['kind'] == 'native_abi':
        from .native_abi.evidence import require_validation as grade_gate
    elif case['kind'] == 'indirect_call':
        from .indirect_calls.evidence import require_validation as grade_gate
    elif case['kind'] == 'variant_object':
        from .variant_objects.evidence import require_validation as grade_gate
    elif case['kind'] == 'pointer_probe':
        from .pointers.evidence import require_validation as grade_gate
    elif case['kind'] == 'abi_probe':
        from .abi.evidence import require_validation as grade_gate
    elif case['kind'] == 'cpp_buffer':
        from .cpp_buffer.evidence import require_validation as grade_gate
        from .buffer_evidence import require_validation as shared_gate
        shared_gate()
    elif case['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
        from .lifecycle_evidence import require_validation as grade_gate
        if case['kind'] == 'cpp_lifecycle':
            from .cpp_lifecycle.evidence import require_validation as cpp_gate
            cpp_gate()
    else:
        from .buffer_evidence import require_validation as grade_gate
    grade_gate()


def public_inputs(case, runtime, access):
    """Validate/project each frozen public package once, independently of model."""
    import preparation_session
    return preparation_session.once('public-inputs', [case, runtime, access],
                                    lambda: _public_inputs(case, runtime, access))


def _public_inputs(case, runtime, access):
    public_directory = ROOT / case['public_directory']
    files = {name: (public_directory / name).read_bytes() for name in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
    if {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()} != case['public_files_sha256']:
        raise ValueError('Frozen capability public input changed')
    metadata = json.loads(files['manifest.json'])
    if case['kind'] == 'localization':
        public.validate_manifest(metadata)
    elif case['kind'] in ('optimized_localization', 'native_abi', 'matched_buffer'):
        # frozen() has already checked the exact manifest against the selected
        # original AC/CAS output, supplied premises and public-file boundaries.
        if metadata['artifact_schema'] != case['artifact_schema'] or metadata['binary_sha256'] != case['binary_sha256']:
            raise ValueError('Advanced capability public contract differs')
    elif case['kind'] == 'indirect_call':
        from .indirect_calls.tasks import validate_manifest
        validate_manifest(metadata)
    elif case['kind'] == 'variant_object':
        from .variant_objects.tasks import validate_manifest
        validate_manifest(metadata)
    elif case['kind'] == 'pointer_probe':
        from .pointers.tasks import validate_manifest
        validate_manifest(metadata)
    elif case['kind'] == 'abi_probe':
        from .abi.tasks import validate_manifest
        validate_manifest(metadata)
    elif case['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
        from .lifecycle_tasks import validate_manifest
        validate_manifest(metadata)
    else:
        public.validate_buffer_manifest(metadata)
    # Both installed adapters send prompt.txt and forbid file reads. Materialize
    # the already validated public manifest into that channel before hashing.
    context = analysis_context.derive(files['target'], metadata, case['kind']) if analysis_context.enabled(runtime) else None
    format_policy = artifact_format.policy(case, runtime)
    files['prompt.txt'] = model_prompt(files['prompt.txt'], files['manifest.json'], format_policy=format_policy) + analysis_context.prompt(context)
    # Use the same lossless tool catalog projection as existing paged tasks.
    import run
    files['tools.json'] = run.paged_tools(files['tools.json'])
    files = evidence_access.project(files, access)
    return files, context, format_policy


def prepare(entry, model, *, materialize=True, offline_fixture=False, attestor=None):
    settings, _ = config.load()
    access = evidence_access.mode(entry.get('evidence_access', 'binary'))
    saved = records.read(ROOT / entry['directory'] / 'task.json', {})
    if not materialize and saved.get('offline_fixture'):
        offline_fixture = True
    if materialize and not settings['inference_enabled'] and not offline_fixture:
        raise RuntimeError('Capability inference is disabled; task preparation cannot authorize a provider')
    case = tasks.frozen(entry['case'])
    if access != 'binary' and case['kind'] not in evidence_access.TYPE_KINDS:
        raise ValueError('Premises-only controls support only type/ABI recovery tasks')
    selected_profile = next((item for item in settings['profiles'] if item['id'] == case['profile_id']), None)
    if selected_profile is None or selected_profile['kind'] != case['kind']:
        raise ValueError('Capability task differs from its configured profile')
    if materialize and not offline_fixture and not selected_profile['enabled']:
        raise RuntimeError('Capability profile is disabled; a saved selection cannot authorize a provider')
    # Grader-only revisions stay outside inference identity, but their frozen
    # private data must exist and agree with the task before any provider work.
    store.spec({**case, 'track': 'capability', 'artifact_contract_version': 1})
    if (entry.get('configuration_sha256') is not None
            and entry['configuration_sha256'] != _namespace(settings, model, access)):
        raise RuntimeError('Capability configuration changed after scheduling')
    if (entry.get('capability_case') is not None
            and entry['capability_case']['public_package_sha256'] != case['public_package_sha256']):
        raise RuntimeError('Capability public package changed after scheduling')
    if not offline_fixture:
        declaration = next(row for row in tasks.declared() if row['task_id'] == case['id'])
        if preparation.inspect(declaration)['status'] != 'ready':
            raise RuntimeError('Capability preparation is missing, stale or invalid before provider work')
    if materialize and not offline_fixture:
        require_case_validation(case)
    profile = environment_profile.current(model['harness'], settings['condition'])
    import preparation_session
    files, context, format_policy = public_inputs(case, settings['runtime'], access)
    if context is not None and not offline_fixture:
        preparation_session.require_context(task_ids={case['id']})
    paging = config.paging_policy(settings)
    case_settings = {'id': case['id'], 'language': case['source_language'], **settings['limits']}
    identity = cache.inference_identity(entry, model, case_settings, profile, files['prompt.txt'], files['target'],
        files['tools.json'], files['manifest.json'], settings['runtime'])
    if 'authorized_retry' in entry:
        identity['authorized_retry'] = attempts.authorized_retry(entry['authorized_retry'],
            [model['id'], 'capabilities-v1', case['id']])
    tools = json.loads(files['tools.json'])['tools']
    gateway = {'max_tool_calls': case_settings['max_tool_calls'], 'tool_timeout_ms': settings['runtime']['tool_timeout_ms'],
        'analysis_tools_file': '@task/tools.json', 'data_limits': settings['runtime']['data_limits'], 'evidence_paging': paging}
    harness = {**settings['runtime'], 'max_output_tokens': case_settings['max_output_tokens'],
        'analysis_tools_file': '@task/tools.json', 'timeout_seconds': case_settings['timeout_seconds'],
        'allowed_tool_names': sorted('mcp__rea__' + tool['name'] for tool in tools), 'evidence_paging': paging}
    import adaptive_budget
    baseline = None if offline_fixture else adaptive_budget.context(model, 'capabilities-v1', case, settings['condition'], completed=entry.get('_budget_completed', ()))
    if baseline is not None:
        harness['budget_context'] = baseline
    if context is not None and access == 'binary':
        gateway['analysis_context'] = context
    if not offline_fixture:
        configuration = {'gateway': gateway, 'harness': harness}
        identity['attestation'] = (attestor or runtime_attestation.prepare)(model, configuration=configuration)
        if attestor is not None:
            import copy
            import request_contract
            proof = runtime_attestation.validate(identity['attestation'])
            expected = copy.deepcopy(configuration)
            expected['harness']['request_contract'] = request_contract.build(
                proof['provider'], model, expected['gateway'], expected['harness'])
            if proof.get('configuration') != expected or proof['runtime']['manifest'].get('harness') != model['harness']:
                raise ValueError('Registered attestation differs from the effective task settings')
        harness = identity['attestation']['configuration']['harness']
        if context is not None:
            preparation_session.require_context(identity['attestation']['runtime'], task_ids={case['id']})
    evidence_access.validate_inputs(files, access, binary_sha256=case['binary_sha256'],
                                    allowed_tools=harness['allowed_tool_names'])
    if context is not None and access == 'binary':
        files[analysis_context.FILENAME] = context['seed_text'].encode('ascii')
    trial = cache.inference_trial(entry)
    if 'inference_trial' in entry:
        expected_trial = str(attempt_directory(model, case, _namespace(settings, model, access)).relative_to(ROOT))
        if trial != expected_trial:
            raise ValueError('Inference logical trial differs from its configured cell')
    action = action_cache.inference(identity, files, trial=trial,
                                   recorded_files=preparation_session.inference_files(identity))
    if entry.get('resolved_fingerprint', action.key) != action.key:
        raise ValueError('Inference action changed after attempt resolution; no provider launch')
    directory = attempts.route(entry, action.key)
    if entry.get('resolved_fingerprint') and directory != entry['directory']:
        raise ValueError('Inference attempt route changed after resolution')
    workspace = ROOT / '.cache/benchmark-runtime/tasks' / action.key
    if materialize:
        import run
        action_cache.Cache().publish(action)
        workspace.mkdir(parents=True, exist_ok=True)
        for name, raw in files.items():
            path = workspace / name
            if path.exists() and path.read_bytes() != raw:
                raise ValueError('Frozen runtime capability input changed')
            if not path.exists():
                path.write_bytes(raw)
        gateway['analysis_tools_file'] = str(workspace / 'tools.json')
        if identity.get('attestation'):
            gateway['runtime_environment'] = runtime_attestation.environment(identity['attestation'], input_directory=workspace)
        elif context is not None and access == 'binary':
            gateway['runtime_environment'] = analysis_context.environment(context, workspace)
        run.save(workspace / 'gateway.json', gateway)
        run.save(workspace / 'harness-config.json', {**harness, 'analysis_tools_file': str(workspace / 'tools.json')})
    if materialize and not offline_fixture:
        adaptive_budget.track(ROOT / directory)
    # The shared adapters' legacy `language` alias describes their output. Keep
    # the target language in the typed contract and inference inputs; structured
    # artifacts have no output language. This also fixes new raw adapter receipts
    # without changing the adapters or any historical inference identity.
    return {**case, **case_settings, 'language': case['output_language'],
        'track': 'capability', 'artifact_contract_version': 1,
        'model': model['id'], 'harness': model['harness'], 'label': model['label'], 'reasoning': model['reasoning'],
        'input_directory': str(workspace), 'directory': directory, 'cache_schema': 3,
        **({'inference_trial': trial} if 'inference_trial' in entry else {}),
        'tool_count': len(tools), 'condition': settings['condition'], 'environment_profile': profile,
        'fingerprint': action.key, 'action_descriptor': action.descriptor, 'inference_identity': identity,
        'prompt_sha256': hashlib.sha256(files['prompt.txt']).hexdigest(),
        **({'runtime_attestation': identity['attestation'], 'request_contract': harness['request_contract']} if identity.get('attestation') else {}),
        **({'evidence_access': access} if access != 'binary' else {}),
        **({'analysis_context_policy': context['policy']} if context is not None else {}),
        **({'artifact_format_policy': format_policy} if format_policy else {}),
        **({'offline_fixture': True} if offline_fixture else {})}


def validate_cache(entry, model, expected):
    directory = ROOT / entry['directory']
    task = records.read(directory / 'task.json', {})
    complete = records.read(directory / 'completed.json', {})
    if evidence_access.mode(task.get('evidence_access', 'binary')) != evidence_access.mode(expected.get('evidence_access', 'binary')):
        raise ValueError('Typed inference evidence access differs; preserve without rerun')
    if task.get('analysis_context_policy') != expected.get('analysis_context_policy'):
        raise ValueError('Typed analysis-context policy differs; preserve without rerun')
    if (not task or task.get('fingerprint') != expected['fingerprint'] or task.get('model') != model['id']
            or complete.get('fingerprint') != task['fingerprint']):
        raise ValueError('Typed inference differs from current effective inputs; preserve without rerun')
    if (any(key not in task for key in ('source_language', 'output_language', 'language'))
            or task.get('source_language') != expected['source_language']
            or task.get('output_language') != expected['output_language']
            or task.get('language') != expected['output_language']):
        raise ValueError('Typed inference source/output language contract is inconsistent')
    cache.verify_files(directory, complete['files_sha256'])
    profile = task['environment_profile']
    if (complete.get('environment_profile') != profile
            or cache.digest({key: value for key, value in profile.items() if key != 'fingerprint'}) != profile['fingerprint']
            or cache.profile_settings(profile) != task['inference_identity']['environment']):
        raise ValueError('Typed inference environment profile is inconsistent')
    files = cache.inference_files(task)
    evidence_access.validate_inputs(files, task.get('evidence_access', 'binary'),
                                    binary_sha256=task['binary_sha256'],
                                    allowed_tools=task.get('request_contract', {}).get('allowed_tool_names', []))
    if cache.inference_trial(task) != cache.inference_trial(expected):
        raise ValueError('Typed inference logical trial differs')
    action = action_cache.inference(task['inference_identity'], files, trial=cache.inference_trial(task))
    if action.key != task['fingerprint'] or action.descriptor != task['action_descriptor']:
        raise ValueError('Typed inference action differs from its committed inputs')
    result = records.read(directory / 'result.json')
    if (result.get('model') != task['model'] or result.get('binary_sha256') != task['binary_sha256']
            or result.get('prompt_sha256') != task['prompt_sha256'] or result.get('environment_profile') != profile):
        raise ValueError('Typed inference receipt differs from task identity')
    if (any(key not in result for key in ('language', 'output_language'))
            or result['language'] != task['output_language']
            or result['output_language'] != task['output_language']):
        raise ValueError('Typed inference receipt output language differs from task contract')
    if task.get('runtime_attestation'):
        proof = task['runtime_attestation']
        runtime_attestation.validate(proof)
        if (task['inference_identity'].get('attestation') != proof or result.get('runtime_attestation_id') != proof['identity']
                or result.get('provider') != proof['provider']['provider']):
            raise ValueError('Typed inference lacks its runtime identity')
        for phase in ('before', 'after'):
            verified = records.read(directory / ('runtime-verification-' + phase + '.json'), {})
            if verified.get('identity') != proof['identity'] or verified.get('status') != 'verified_local_content_and_route' or verified.get('provider') != proof['provider']:
                raise ValueError('Typed inference runtime verification missing')
        import request_contract
        budget = records.read(directory / 'request-budget.json', {})
        if ('request-budget.json' not in complete['files_sha256'] or not request_contract.grading_allowed(budget, task['request_contract'])
                or budget.get('contract_sha256') != request_contract.digest(task['request_contract'])
                or budget.get('contract') != request_contract.from_task(task)
                or not budget.get('provider_forwards')):
            raise ValueError('Typed inference request-budget verification missing')
    elif not task.get('offline_fixture'):
        raise ValueError('New typed inference lacks runtime/provider attestation')
    selected, _ = store.read_selected(directory, task=task)
    key = store.action(directory, expected).key
    cache.register_inference(task, directory, action)
    return {'status': 'cache_hit', 'fingerprint': task['fingerprint'], 'grading_pending': not selected or selected['grading_id'] != key,
            'grading_key': key, 'action_cache_schema': 3,
            'reuse_scope': 'attested_action' if task.get('runtime_attestation') else 'offline_fixture'}
