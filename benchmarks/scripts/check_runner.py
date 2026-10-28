#!/usr/bin/python3
"""Exercise the real coordinator and cache boundary without provider access."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch

import benchmark_config
import cache
import records
import run
import action_cache
from grading import native


def rejected(operation, text):
    try:
        operation()
    except (ValueError, RuntimeError) as error:
        assert text in str(error), str(error)
    else:
        raise AssertionError('Expected explicit rejection: ' + text)


def fixture_attestation():
    manifest = {'schema_version': 1, 'environment': {}, 'components': {}, 'scope': 'offline-fixture'}
    runtime = {'identity': action_cache.digest(action_cache.canonical(manifest))['hash'], 'manifest': manifest}
    value = {'schema_version': 1, 'runtime': runtime, 'provider': {'provider': 'offline-fixture'}}
    return {**value, 'identity': action_cache.digest(action_cache.canonical(value))['hash']}


@patch.object(run.runtime_attestation, 'prepare', return_value=fixture_attestation())
def main(_attestation):
    implementation = [Path(__file__), Path(run.__file__), Path(cache.__file__), records.BENCH / 'scripts/action_cache.py', records.BENCH / 'config/action-dependencies.json', Path(benchmark_config.__file__),
                      Path(run.environment_profile.__file__), records.BENCH / 'harnesses/dispatch.py']
    implementation += sorted((records.BENCH / 'scripts/grading').glob('*.py'))
    implementation += [records.BENCH / 'scripts/grading/go_imports.go', records.BENCH / 'scripts/usage.py']
    implementation += [records.BENCH / name for name in ['scripts/runtime_attestation.py', 'harnesses/provider-auth.mjs',
                       'config/runtime-attestation.json', 'config/runtime-attestation-migration.json']]
    implementation += [records.BENCH / 'scripts/grading/metadata_admission.py']
    started_sources = {p: cache.file_sha(p) for p in implementation}
    selection = benchmark_config.load()
    # Native cache controls use the native fixture attestation above. Typed
    # capabilities have their own closed coordinator/cache controls and cannot
    # be authenticated by this deliberately synthetic native proof.
    selection['capabilities'] = False
    configured = run.selected_entries(selection)
    assert configured, 'No configured benchmark cells'
    completed = [entry for entry in configured if entry['status'] == 'cached']
    entries, changed_inputs = [], []
    # A saved grade is historical evidence, not proof that today's adapter has
    # identical inputs. Keep related adapter changes as explicit cache misses.
    for entry in completed:
        model = records.models()[entry['model']]
        expected = run.prepare_task(entry, model, materialize=False)
        try:
            cache.validate(entry, model, expected)
        except ValueError as error:
            assert str(error).startswith('Inference fingerprint mismatch:'), str(error)
            rejected(lambda: cache.validate(entry, model, expected), 'Inference fingerprint mismatch:')
            changed_inputs.append({'experiment': entry['experiment'], 'case': entry['case'],
                                   'model': entry['model'], 'condition': entry['condition'],
                                   'reason': str(error)})
        else:
            entries.append(entry)
    # A related adapter revision may legitimately invalidate every current-input
    # cache. Preserve and verify those historical receipts; exercise positive
    # miss/reuse below using the isolated fixture, never a paid replacement run.
    assert completed, 'Cache acceptance requires at least one historical saved cell'
    configured_models = selection['models'] if isinstance(selection['models'], list) else [mid for mid, m in records.models().items() if m.get('enabled', True)]
    assert {e['model'] for e in configured} == set(configured_models)
    assert len({(e['experiment'], e['case'], e['model'], e['condition']) for e in configured}) == len(configured)
    for e in configured:
        assert e['condition'] == (records.condition_catalog()['active_case_conditions'][e['suite']] if selection['condition'] == 'active' else selection['condition'])
    original_stop = (records.BENCH / 'config/execution.json').read_bytes()
    status_path = records.BENCH / 'config/benchmark-status.json'
    original_status = status_path.read_bytes() if status_path.exists() else None
    save = run.save
    validation_statuses = []
    def isolated_save(path, value, *args, **kwargs):
        if Path(path) == status_path:
            validation_statuses.append(copy.deepcopy(value))
            return
        return save(path, value, *args, **kwargs)
    with patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch forbidden')) as forbidden, \
         patch.object(native, 'evaluate', side_effect=AssertionError('Unexpected native regrading of a current cache')), \
         patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
         patch.object(run, 'save', side_effect=isolated_save), \
         contextlib.redirect_stdout(io.StringIO()):
        first = run.run_batch(entries, selection, offline=True)
        second = run.run_batch(entries, selection, offline=True)
    assert forbidden.call_count == 0
    def stable(receipt):
        value = copy.deepcopy(receipt)
        for outcome in value.get('outcomes', []):
            # A first-pass legacy import is cache reuse, not native evaluation.
            outcome.pop('offline_regraded', None)
            outcome.pop('grading_id', None)
        return value
    assert stable(first) == stable(second) and first['cache_hits'] == len(entries) and first['model_api_calls'] == 0
    assert (records.BENCH / 'config/execution.json').read_bytes() == original_stop
    assert len(validation_statuses) == 2
    assert (status_path.read_bytes() if status_path.exists() else None) == original_status, 'Validation changed real benchmark status'
    print(f'PASS: two coordinator runs, {len(entries)} validated cache hits each, zero provider launches', flush=True)
    print(f'PASS: {len(changed_inputs)} saved cells reject changed inference inputs; historical results preserved', flush=True)

    entry = (entries or completed)[0]
    model = records.models()[entry['model']]
    expected = run.prepare_task(entry, model, materialize=False)
    fixture_entry = {k: v for k, v in entry.items() if k != 'condition'}
    assert run.prepare_task(fixture_entry, model, materialize=False)['inference_identity']['condition'] == 'legacy-recorded'
    directory = records.ROOT / entry['directory']
    # Pricing/labels and scheduling do not alter inference; model inputs do.
    renamed = dict(model, label='Display label changed', pricing_note='Reporting only')
    assert run.prepare_task(entry, renamed, materialize=False)['fingerprint'] == expected['fingerprint']
    changed = copy.deepcopy(expected)
    changed['inference_identity']['limits'][0] += 1
    rejected(lambda: cache.validate(entry, model, changed), 'fingerprint mismatch')
    changed = copy.deepcopy(expected)
    changed['inference_identity']['environment']['files_sha256']['benchmarks/scripts/rea-gateway.mjs'] = '0' * 64
    rejected(lambda: cache.validate(entry, model, changed), 'verified profile')
    changed = copy.deepcopy(expected)
    changed['inference_identity']['model']['reasoning'] = 'low'
    rejected(lambda: cache.validate(entry, model, changed), 'adapter changed')
    changed = copy.deepcopy(expected)
    changed['inference_identity']['prompt'] = '0' * 64
    rejected(lambda: cache.validate(entry, model, changed), 'fingerprint mismatch')

    # Isolated missing-cache fixture: one dispatch, then a real validated cache hit.
    runtime = records.ROOT / '.cache/benchmark-runtime'
    with tempfile.TemporaryDirectory(prefix='runner-fixture-', dir=runtime) as temporary:
        fixture = Path(temporary)
        fake_entry = dict(entry, directory=str(fixture.relative_to(records.ROOT)), status='not_started')
        task = run.prepare_task(fake_entry, model)
        claim = records.ROOT / '.cache/benchmark-attempt-registry' / ('shared-' + task['fingerprint'] + '.json')
        # Claim storage is isolated so fixtures cannot collide with real inference identities.
        original_save = run.save
        def isolated_save(path, value, exclusive=False):
            if path == claim:
                path = fixture / 'fixture-claim.json'
            original_save(path, value, exclusive)
        def provider(directory, task, model, cancel_event=None):
            run.save(directory / 'result.json', {'model': model['id'], 'environment_profile': task['environment_profile'],
                'provider': task['runtime_attestation']['provider']['provider'], 'runtime_attestation_id': task['runtime_attestation']['identity'],
                'binary_sha256': task['binary_sha256'], 'prompt_sha256': task['prompt_sha256']}, True)
            for phase in ('before', 'after'):
                run.save(directory / ('runtime-verification-' + phase + '.json'), {
                    'identity': task['runtime_attestation']['identity'], 'status': 'verified_local_content_and_route',
                    'provider': task['runtime_attestation']['provider']}, True)
            (directory / 'answer.md').write_text('Offline fixture only. No benchmark candidate.\n')
        def grader(directory, task):
            grade = {'compiled': False, 'passed': 0, 'total': 1, 'failure': 'Offline fixture'}
            run.save(directory / 'grade.json', grade, True)
            return grade
        with patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '0'}), patch.object(run, 'save', side_effect=isolated_save), \
             patch.object(run, 'launch_model', side_effect=provider) as fake, patch.object(run, 'grade_saved', side_effect=grader), \
             contextlib.redirect_stdout(io.StringIO()):
            run.execute(fake_entry, model)
            hit = run.execute(dict(fake_entry, status='cached'), model)
            assert fake.call_count == 1 and hit['status'] == 'cache_hit'
        (fixture / 'answer.md').write_text('Corrupted fixture output\n')
        rejected(lambda: cache.validate(fake_entry, model, task), 'artifact missing or changed')
        (fixture / 'answer.md').write_text('Offline fixture only. No benchmark candidate.\n')
        with patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '1'}), patch.object(run, 'launch_model') as forbidden:
            rejected(lambda: run.execute(fake_entry, model), 'provider launch refused')
            rejected(lambda: run.execute(dict(fake_entry, status='interrupted; duplicate_inference_refused'), model), 'no automatic retry')
            assert forbidden.call_count == 0
    print('PASS: one missing-cache fixture dispatch; reuse on second call; corruption, changed inputs and interrupted retries rejected', flush=True)

    inputs = [*implementation, records.ROOT / 'Makefile', records.ROOT / 'bin/benchmark',
              records.BENCH / 'config/benchmark.json', records.BENCH / 'config/models.json',
              records.BENCH / 'config/conditions.json', records.BENCH / 'config/experiments.json',
              records.BENCH / 'config/cache-compatibility.json', records.BENCH / 'config/environment-profile-archives.json',
              records.BENCH / 'config/cleanup-preservation.json']
    for e in completed:
        directory = records.ROOT / e['directory']
        inputs += [directory / name for name in ['completed.json', 'task.json', 'result.json', 'grade.json']]
        if (directory / 'selected-grade.json').exists():
            inputs.append(directory / 'selected-grade.json')
            selection = records.read(directory / 'selected-grade.json')
            inputs += [directory / selection['path'], (directory / selection['path']).parent / 'manifest.json']
    for harness in {m['harness'] for m in records.models().values()}:
        for condition in {e['condition'] for e in entries}:
            inputs += [records.ROOT / name for name in run.environment_profile.current(harness, condition)['files_sha256']]
    receipt = {'schema_version': 1, 'passed': True, 'model_api_calls': 0, 'provider_launches': 0,
               'configured_tasks': len(entries), 'repeat_runs': 2, 'cache_hits_per_run': len(entries),
               'scheduled_tasks': len(configured), 'incomplete_tasks': len(configured) - len(completed),
               'historical_completed_tasks': len(completed),
               'changed_input_cache_misses': changed_inputs,
               'fixture_dispatches': 1, 'fixture_paid_calls': 0,
               'checks': ['all configured active cases', 'two identical cache-only coordinator runs',
                   'no credentials or provider dispatch', 'one simulated missing-cache dispatch',
                   'corrupt output and changed prompt, model, budgets and adapters rejected',
                   'no automatic retry', 'execution stop state unchanged'],
               'inputs_sha256': {str(p.relative_to(records.ROOT)): cache.file_sha(p) for p in sorted(set(inputs))}}
    assert all(cache.file_sha(p) == expected for p, expected in started_sources.items()), 'Validation source changed during checks; rerun'
    run.save(records.BENCH / 'config/runner-validation.json', receipt)


if __name__ == '__main__':
    main()
