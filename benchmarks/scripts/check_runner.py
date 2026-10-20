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


def rejected(operation, text):
    try:
        operation()
    except (ValueError, RuntimeError) as error:
        assert text in str(error), str(error)
    else:
        raise AssertionError('Expected explicit rejection: ' + text)


def main():
    implementation = [Path(__file__), Path(run.__file__), Path(cache.__file__), Path(benchmark_config.__file__),
                      Path(run.environment_profile.__file__), records.BENCH / 'harnesses/dispatch.py']
    started_sources = {p: cache.file_sha(p) for p in implementation}
    selection = benchmark_config.load()
    entries = run.selected_entries(selection)
    assert entries and all(e['status'] == 'cached' for e in entries), 'Acceptance requires the completed configured suite'
    configured_models = selection['models'] if isinstance(selection['models'], list) else [mid for mid, m in records.models().items() if m.get('enabled', True)]
    assert {e['model'] for e in entries} == set(configured_models)
    assert len({(e['experiment'], e['case'], e['model'], e['condition']) for e in entries}) == len(entries)
    for e in entries:
        assert e['condition'] == (records.condition_catalog()['active_case_conditions'][e['suite']] if selection['condition'] == 'active' else selection['condition'])
    original_stop = (records.BENCH / 'config/execution.json').read_bytes()
    with patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch forbidden')) as forbidden, \
         patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
         contextlib.redirect_stdout(io.StringIO()):
        first = run.run_batch(entries, selection, offline=True)
        second = run.run_batch(entries, selection, offline=True)
    assert forbidden.call_count == 0
    assert first == second and first['cache_hits'] == len(entries) and first['model_api_calls'] == 0
    assert (records.BENCH / 'config/execution.json').read_bytes() == original_stop
    print(f'PASS: two coordinator runs, {len(entries)} validated cache hits each, zero provider launches', flush=True)

    entry = entries[0]
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
    rejected(lambda: cache.validate(entry, model, changed), 'adapter changed')
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
                'binary_sha256': task['binary_sha256'], 'prompt_sha256': task['prompt_sha256']}, True)
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
    for e in entries:
        directory = records.ROOT / e['directory']
        inputs += [directory / name for name in ['completed.json', 'task.json', 'result.json', 'grade.json']]
    for harness in {m['harness'] for m in records.models().values()}:
        for condition in {e['condition'] for e in entries}:
            inputs += [records.ROOT / name for name in run.environment_profile.current(harness, condition)['files_sha256']]
    receipt = {'schema_version': 1, 'passed': True, 'model_api_calls': 0, 'provider_launches': 0,
               'configured_tasks': len(entries), 'repeat_runs': 2, 'cache_hits_per_run': len(entries),
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
