#!/usr/bin/python3
"""Two clean offline builds must produce byte-identical complete report trees."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch
from unittest.mock import Mock
from urllib.parse import unquote, urlsplit
import zipfile

import report
import records
import glm_diagnostics
import audit_environment
import evidence_validation
import cpp_points
import cache


def check_metadata_snapshot():
    from report_io import metadata_snapshot
    with tempfile.TemporaryDirectory(prefix='report-metadata-control-') as directory:
        root = Path(directory)
        source = root / 'input.txt'
        source.write_text('original')
        with metadata_snapshot():
            assert root.is_dir() and source.is_file()
            (root / 'unrelated-cache-entry').write_text('new')
            assert root.is_dir()
        try:
            with metadata_snapshot():
                assert source.is_file()
                source.write_text('changed')
        except ValueError:
            pass
        else:
            raise AssertionError('Changed file was admitted')
        parent = root / 'parent'
        parent.mkdir()
        try:
            with metadata_snapshot():
                assert parent.is_dir()
                parent.rename(root / 'old-parent')
                parent.mkdir()
        except ValueError:
            pass
        else:
            raise AssertionError('Replaced directory was admitted')
    print('PASS: unrelated directory entry, changed input and replaced directory controls')


def frozen_runtime_inputs(data):
    """Admit only saved task inputs authenticated by their inference Merkle tree."""
    candidates = {name for name in data['input_files_sha256'] if re.fullmatch(
        r'\.cache/benchmark-runtime/tasks/[0-9a-f]{64}/(?:analysis\.seeds|manifest\.json|target|prompt\.txt)', name)}
    admitted = set()
    for name, expected in data['input_files_sha256'].items():
        if not name.startswith('benchmarks/runs/capabilities-v1/') or not name.endswith('/task.json'):
            continue
        raw = (records.ROOT / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected
        task = json.loads(raw)
        fingerprint = task.get('fingerprint', '')
        if not re.fullmatch('[0-9a-f]{64}', fingerprint):
            continue
        root = '.cache/benchmark-runtime/tasks/' + fingerprint
        if task.get('input_directory') != str(records.ROOT / root):
            continue
        names = set()
        if task.get('analysis_context_policy') == 'public-given-entrypoints-v1':
            names.update(('analysis.seeds', 'manifest.json', 'target'))
        if task.get('artifact_format_policy') == 'record-role-string-v1':
            names.add('prompt.txt')
        if names:
            assert report.action_cache.digest(report.action_cache.canonical(task['action_descriptor']))['hash'] == fingerprint
        for leaf in names:
            path = root + '/' + leaf
            if path in candidates:
                raw = (records.ROOT / path).read_bytes()
                assert hashlib.sha256(raw).hexdigest() == data['input_files_sha256'][path]
                assert cache.action_input_bytes(task['action_descriptor'], 'task/' + leaf) == raw
                admitted.add(path)
    return admitted


def pinned_metadata(path):
    return bool(path == '.cache/benchmark-grading/v1/active.json' or re.fullmatch(
        r'\.cache/(?:benchmark-c-executor/v1|benchmark-capabilities/(?:tooling-v1|baseline-clang20-tooling-v1))/'
        r'(?:active|[0-9a-f]{64})\.json', path) or re.fullmatch(
        r'\.cache/benchmark-c-executor/v1/provenance/[0-9a-f]{64}/[0-9a-f]{64}\.json', path))


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attributes):
        for name, value in attributes:
            if name in ['href', 'src'] and value:
                self.targets.append(value)


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}


def validate_capability_builds(builds, declarations, public_task_ids):
    assert set(public_task_ids) == {row['task_id'] for row in declarations}
    expected = {public_task_ids[row['task_id']]: row for row in declarations}
    assert len(expected) == len(declarations)
    assert len(builds) == len(expected) and {row['task_id'] for row in builds} == set(expected)
    for row in builds:
        declaration = expected[row['task_id']]
        profile = declaration['profile']
        compiler = (profile['original_compiler'] if profile['kind'] == 'matched_buffer' else
                    profile['variant'] if profile['kind'] == 'native_abi' else 'gcc')
        family, language = {'gcc': ('gcc', 'cpp'), 'clang20': ('clang', 'cpp'),
                            'rust': ('rustc', 'rust'), 'go': ('gc', 'go')}[compiler]
        optimization = ('compiler_default' if compiler == 'go' else
                        '-O3' if profile['kind'] == 'optimized_localization' else '-O2')
        metadata = row['metadata']
        assert row['family_id'] == declaration['family_id'] and row['profile_id'] == profile['id']
        actual = (metadata['source_language'], metadata['compiler_family'], metadata['optimization'])
        assert actual == (language, family, optimization), (row['task_id'], actual)
        assert metadata['strip_mode'] == 'strip_all_verified'


def validate(directory):
    pages, links = 0, 0
    for path in sorted(directory.rglob('*.html')):
        parsed = Links()
        parsed.feed(path.read_text())
        pages += 1
        for href in parsed.targets:
            url = urlsplit(href)
            if url.scheme or url.netloc or not url.path:
                continue
            target = (path.parent / unquote(url.path)).resolve()
            assert target.is_relative_to(directory.resolve()), 'Nonportable report link: ' + href
            assert target.is_file(), 'Broken report link: ' + str(path.relative_to(directory)) + ' ' + href
            links += 1
    data = json.loads((directory / 'data/comparison.json').read_text())
    assert not any('/reports/' in p for p in data['input_files_sha256'])
    assert data['execution']['experiments_stopped']
    configured_budget = json.loads((records.BENCH / 'config/request-contract.json').read_bytes())
    assert data['task_budget_policy'] == {'policy_id': configured_budget['id'], 'limits': configured_budget['task_limits']}
    assert 'Task spending limits' in (directory / 'index.html').read_text()
    assert 'Task spending limits' in (directory / 'measurement.html').read_text()
    assert not any(r['status'] == 'running' for r in data['records'])
    assert any(r['status'] == 'interrupted' for r in data['records'])
    for row in data['records']:
        if row['status'] == 'inference_saved_grading_pending':
            assert row['receipt'] and not row['grade']
    overview = (directory / 'index.html').read_text()
    assert 'data-chart-panel="matched"' in overview and 'Coverage before scores' in overview
    assert 'Median inference runtime' in overview
    assert data['runner_validation']['passed'] and data['runner_validation']['inputs_current']
    prepared = data['capability_preparation']
    assert prepared['frozen_inputs_current']
    from capabilities import tasks as capability_tasks
    assert prepared['families'] == 6
    assert prepared['task_definitions'] == prepared['frozen_tasks'] == len(capability_tasks.declared())
    assert {'T1', 'T2', 'T3', 'T4', 'T5', 'T6'} <= set(prepared['supported_capabilities'])
    expected_controls = {'localization': 27, 'structured_runtime': 13, 'c_buffers': 12, 'c_lifecycle': 22,
                         'cpp_buffers': 16, 'cpp_lifecycle': 12, 'abi_probes': 35, 'pointer_probes': 27,
                         'variant_objects': 24, 'indirect_calls': 28, 'optimized_locations': 42,
                         'native_abi': 40, 'coordinator': 83, 'matched_baselines': 16,
                         'rust_buffers': 17, 'go_buffers': 18}
    assert {row['id'] for row in prepared['controls']} == set(expected_controls)
    assert all(row['passed'] and row['current'] and not row['failed'] and row['checks'] >= expected_controls[row['id']]
               for row in prepared['controls'])
    typed = data['capability_measurements']
    assert typed['declared_tasks'] == prepared['task_definitions']
    assert typed['configured_cells'] == len(capability_tasks.declared()) * len(data['models'])
    assert typed['configured_cells'] == typed['scheduled_cells'] + typed['disabled_cells']
    assert typed['inference_enabled'] == prepared['inference_enabled']
    assert typed['saved_observations'] == len(typed['history'])
    assert typed['released_capabilities'] == 0
    if not prepared['inference_enabled']:
        assert typed['scheduled_cells'] == 0
    assert all(not row['observation'] or row['observation']['analysis_condition'] == row['analysis_condition']
               for row in typed['cells'])
    assert typed['schedule_status'] == 'configuration_snapshot_only'
    assert typed['validation']['passed'] and typed['validation']['current'] and typed['validation']['checks'] >= 48
    assert typed['validation']['model_api_calls'] == 0
    assert 'Typed capability inventory and measurement coverage' in ((directory / 'capabilities.html').read_text() + (directory / 'capability-inventory.html').read_text())
    from capabilities import measurements as capability_measurements
    capability_measurements.validate_projection(capability_measurements.public_projection(typed))
    from capabilities import registration_report
    registered = data['capability_registration']
    registration_report.validate(registered)
    assert registered == json.loads((directory / 'data/capability-registration.json').read_text())
    assert registered['validation']['passed'] and registered['validation']['current']
    assert registered['validation']['checks'] >= 38 and registered['validation']['model_api_calls'] == 0
    # Valid stopped schedules and real observations must survive regeneration.
    # The closed validator above verifies their saved bindings and denominators.
    assert len({row['registration_id'] for row in registered['schedules']}) == len(registered['schedules'])
    assert len({row['attempt_id'] for row in registered['observations']}) == len(registered['observations'])
    assert registered['invalid_records'] >= 0 and registered['control_records_excluded'] >= 0
    assert len(registered['builds']) == prepared['frozen_tasks']
    declarations = capability_tasks.declared()
    reader = capability_measurements.Reader(records.ROOT, records.read_bytes)
    public_task_ids = {row['task_id']: capability_measurements.public_task_id(reader,
        reader.json('.cache/benchmark-capabilities/task-index/' + row['task_id'] + '.json'))
        for row in declarations}
    validate_capability_builds(registered['builds'], declarations, public_task_ids)
    assert not any('compiler_commands' in row['metadata'] or 'compiler_commands_sha256' in row['metadata']
                   for row in registered['builds'])
    assert 'Original compiler and build conditions' in ((directory / 'capabilities.html').read_text() + (directory / 'capability-inventory.html').read_text())
    assert 'Pointer and object-role recovery: T3 preparation' in (directory / 'measurement.html').read_text()
    assert data['runner_validation']['model_api_calls'] == 0
    runner = data['runner_validation']
    assert runner['cache_hits_per_run'] + len(runner.get('changed_input_cache_misses', [])) == sum(bool(row['grade']) for row in records.active_condition_rows(data))
    assert data['runner_validation']['scheduled_tasks'] == len(records.active_condition_rows(data))
    assert 'Configured benchmark and cache reuse' in overview
    runtime = data['runtime_attestation']
    assert runtime['inputs_current'] and runtime['controls_passed'] and runtime['controls_current']
    assert runtime['model_api_calls'] == 0 and runtime['checks'] >= 13
    assert set(runtime['runtimes']) == {'deepseek-harness', 'zcode'}
    assert 'Runtime and provider attestation' in (directory / 'measurement.html').read_text()
    grading = data['grading_runtime']
    assert grading['controls_passed'] and grading['controls_current'] and grading['model_api_calls'] == 0
    assert grading['checks'] >= 14 and set(grading['profiles']) == {'c', 'cpp', 'rust', 'go'}
    assert 'Closed grading runtimes' in (directory / 'measurement.html').read_text()
    executor = data['c_executor']
    assert executor['controls_passed'] and executor['controls_current'] and executor['model_api_calls'] == 0
    assert executor['checks'] >= 29 and executor['failed'] == 0
    assert 'Host-owned restricted C returns' in (directory / 'measurement.html').read_text()
    assert all(row['grade'].get('return_provenance') == 'native_process_unauthenticated'
               for row in data['records'] + data['condition_records'] if row['grade'])
    measurement = (directory / 'measurement.html').read_text()
    assert 'Implementation plan: incomplete' in measurement
    assert all(label in measurement for label in ['R3: request budgets', 'R6: authenticated returns',
                                                   'L1–L6: localization', 'C1–C6: independent C condition',
                                                   'T1–T7: type/layout/ABI'])
    typed_abi = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6']
    if 'T7' in data['capability_preparation']['supported_capabilities']:
        typed_abi.append('T7')
    assert '/'.join(typed_abi) + ' preparation' in measurement
    assert all(row['runtime_attestation_status'] in {'historical_unknown', 'verified_at_execution', 'not_recorded', 'unverified'} for row in data['records'] + data['condition_records'])
    if data['shared_case_comparison']['cases']:
        assert 'Same-case tokens, cost and runtime' in overview
    assert 'Environment fairness: model-only comparison is not certified' in overview
    assert 'Native case × model' in (directory / 'cases/index.html').read_text()
    if any((r.get('receipt') or {}).get('timed_out') and r['grade'] and not r['source'] for r in data['condition_records']):
        assert 'No accepted native source' in (directory / 'cases/index.html').read_text()
    if any('Expected' in ((r.get('grade') or {}).get('failure') or '') and not (r.get('receipt') or {}).get('timed_out') for r in data['condition_records']):
        assert 'Submission format rejected' in (directory / 'cases/index.html').read_text()
    assert 'partial usage' in (directory / 'costs.html').read_text()
    assert 'Benchmark and reporting workflow' in (directory / 'methodology.html').read_text()
    diagnosis = data['glm_diagnosis']
    assert diagnosis == json.loads((directory / 'data/glm-diagnosis.json').read_text())
    assert diagnosis['implementation_review']['zcode_agent_version'] == '0.17.0'
    assert '.tools/zcode-3.16.1/agent/zcode.cjs' not in data['input_files_sha256']
    assert diagnosis['cases'] and 'Proposed fixes' in (directory / 'diagnostics.html').read_text()
    assert 'Fixed-condition GLM timing' in (directory / 'diagnostics.html').read_text()
    for case in diagnosis['cases']:
        assert case['reasoning_history_differences'] == 0
        if case['status'] == 'graded':
            assert case['answer_matches_wire'] and case['native_blocks_match_wire']
        if case['timed_out']:
            assert case['native_code_blocks'] == 0
            assert not case['finish_reasons'].get('length')
    environment = data['environment_audit']
    assert environment == json.loads((directory / 'data/environment-audit.json').read_text())
    assert environment['certified_model_only_comparison'] is False
    assert all(row['model_only_comparison_certified'] is False for row in data['records'])
    dsh = [c for c in environment['cases'] if c['model'] != 'glm-5.3']
    assert len(dsh) == 22 and all(c['inputs_current'] and c['complete_trace'] for c in dsh)
    assert sum(c.get('counts', {}).get('spilled', 0) for c in dsh) == 40
    assert sum(c['blocked_spill_recovery'] for c in dsh) == 40
    assert any(t['status'] == 'primary_complete' and t['delivered_chars'] > 8192 for c in dsh for t in c['tools'])
    allowed_frozen_data = ('.cache/benchmark-capabilities/task-index/', '.cache/benchmark-capabilities/task-freezes/',
                           '.cache/benchmark-capabilities/grade-specs/', '.cache/benchmark-actions/v3/cas/',
                           '.cache/benchmark-actions/v3/ac/', '.cache/benchmark-grading/v1/profiles/')
    assert pinned_metadata('.cache/benchmark-capabilities/tooling-v1/' + 'a' * 64 + '.json')
    assert not pinned_metadata('.cache/benchmark-capabilities/tooling-v1/' + 'a' * 64 + '/usr/bin/strip')
    assert not pinned_metadata('.cache/benchmark-grading/v1/images/private.tar')
    assert not pinned_metadata('.cache/benchmark-c-executor/v1/credentials.json')
    runtime_inputs = frozen_runtime_inputs(data)
    assert not any(name.endswith('/credentials.json') for name in runtime_inputs)
    assert not any(p.startswith('.tools/') or p.startswith('.cache/')
                   and not p.startswith(allowed_frozen_data) and not pinned_metadata(p) and p not in runtime_inputs
                   for p in data['input_files_sha256'])
    replay = data['evidence_validation']
    assert replay == json.loads((directory / 'data/evidence-validation.json').read_text())
    assert replay['passed'] and replay['inputs_current'] and replay['model_api_calls'] == 0
    assert replay['zcode']['corrected_clipped'] == 0 and replay['dsh']['primary_mismatches'] == 0
    assert replay['dsh_wire_smoke']['passed'] and replay['zcode_wire_smoke']['passed']
    assert replay['historical_scores_changed'] is False
    paging = data['paging_validation']
    assert paging == json.loads((directory / 'data/evidence-paging-validation.json').read_text())
    assert paging['passed'] and paging['inputs_current'] and paging['model_api_calls'] == 0
    assert paging['dsh_wire_smoke']['passed'] and paging['zcode_wire_smoke']['passed']
    assert paging['exact_reassemblies'] > 0 and paging['extra_tool'] == 'read_evidence'
    assert paging['max_projection_characters'] <= 24000 and paging['max_page_characters'] <= 24000
    active = records.active_condition_rows(data)
    assert len(active) == 11 * len(data['models']) and len({(r['experiment'], r['case']['id']) for r in active}) == 11
    # Pilot models retain pending hard cases and do not inherit historical grades.
    additions = {'gpt-6-luna', 'gpt-5.6-sol'}
    assert additions <= set(data['models'])
    original_additions = [r for r in data['records'] if r['model'] in additions]
    observed = {r['model'] for r in original_additions if any((records.ROOT / r['directory'] / name).exists() for name in ['task.json', 'result.json', 'events.jsonl'])}
    assert {r['model'] for r in original_additions} == observed
    for model in additions:
        harder = [r for r in active if r['model'] == model and r['case']['suite'] != 'record-parser']
        assert len(harder) == 8
        assert all(not r['grade'] for r in harder if not r['receipt'])
    shared = data['shared_case_comparison']
    shared_keys = {tuple(k) for k in shared['cases']}
    for item in shared['totals']:
        selected = [r for r in active if r['model'] == item['model'] and (r['experiment'], r['case']['id']) in shared_keys]
        assert len(selected) == len(shared_keys) and all(r['grade'] for r in selected)
        assert item['total_tokens'] == sum(r['receipt'].get('reported_usage', {}).get('totalTokens', 0) for r in selected)
        assert item['passed'] == sum(r['grade']['passed'] for r in selected)
        assert item['checks'] == sum(r['grade']['total'] for r in selected)
    assert 'data-chart-scope="matched" aria-pressed="true"' in overview
    assert 'data-chart-panel="all" hidden' in overview
    assert json.loads((directory / 'data/pricing-gpt-baselines.json').read_text()) == data['supplemental_pricing']
    assert {r['condition'] for r in active if r['case']['suite'] == 'record-parser'} == {'lossless-evidence-v1'}
    assert {r['condition'] for r in active if r['case']['suite'] != 'record-parser'} == {'paged-evidence-v2'}
    for row in data['condition_records']:
        assert '/conditions/' in row['directory']
        assert row['model_only_comparison_certified'] is False
        if row['status'] in ['environment_failure', 'interrupted']:
            assert not row['grade']
            # Accounting completeness is independent of task failure.
            assert row['cost'].get('usage_status', 'unknown') in ('complete', 'partial', 'unknown')
    for case in data['condition_evidence_audit']['cases']:
        if case['inputs_current'] and case['condition'] == 'paged-evidence-v2':
            assert case['paging']['original_hashes_and_slices_verified']
            assert case['paging']['retrieval_tool_available']
    assert 'Evidence delivery fixes: Passed offline replay' in overview
    assert 'Implemented evidence delivery fixes' in (directory / 'diagnostics.html').read_text()
    cpp = data['cpp_build_points']
    assert cpp == json.loads((directory / 'data/cpp-build-points.json').read_text())
    assert cpp['planned_build_points'] == 36 and len(cpp['points']) == 36
    assert all(p['inputs_current'] and p['status'] in ['locally_verified', 'reused_frozen_build'] for p in cpp['points'])
    assert cpp['model_api_calls_for_build_validation'] == 0
    assert cpp['config']['optimization_levels'] == ['O0', 'O2', 'O3']
    assert len({(p['suite'], p['base_case'], p['compiler_id'], p['optimization']) for p in cpp['points']}) == 36
    assert len(cpp['language_feature_probes']['probes']) == 12
    assert all(p['inputs_current'] and p['model_attempts'] == 0 for p in cpp['language_feature_probes']['probes'])
    assert 'Compiler and optimization are separate testing points' in (directory / 'compilers.html').read_text()
    profiles = data['environment_profile_archives']
    assert profiles == json.loads((directory / 'data/environment-profile-archives.json').read_text())
    assert profiles['model_api_calls'] == 0 and profiles['profiles']
    for fingerprint, item in profiles['profiles'].items():
        assert item['files_current'] and item['attempt_tasks']
        identity = {k: v for k, v in item['profile'].items() if k != 'fingerprint'}
        assert hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() == fingerprint
        for source_name, digest in item['profile']['files_sha256'].items():
            exported = directory / 'data/environment-profiles' / fingerprint / source_name
            assert hashlib.sha256(exported.read_bytes()).hexdigest() == digest
    for row in data['condition_records']:
        profile = (row.get('receipt') or {}).get('environment_profile')
        if profile:
            fingerprint = profile['fingerprint']
            assert fingerprint in profiles['profiles'], 'Saved environment source profile is missing'
    for row in data['records'] + data['condition_records']:
        exported = directory / 'answers' / row['experiment'] / row['condition'] / row['model'] / (row['case']['id'] + '.md')
        if exported.exists():
            original = row['directory'] + '/answer.md'
            assert data['input_files_sha256'][original] == hashlib.sha256(exported.read_bytes()).hexdigest(), 'Exported visible answer is missing from report provenance'
    with zipfile.ZipFile(directory / 'benchmark-report.zip') as archive:
        expected = hashes(directory)
        expected.pop('benchmark-report.zip')
        assert sorted(archive.namelist()) == sorted(expected)
        for name, digest in expected.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    return pages, links


def forbidden(*args, **kwargs):
    raise AssertionError('Report generation tried to access the network or execute a subprocess')


def main():
    check_metadata_snapshot()
    import run
    # A missing build-point task must never borrow a score based only on binary bytes.
    saved_data = records.collect()
    build_data = cpp_points.collect(saved_data, records.read_bytes)
    control = next(p for p in build_data['points'] if p['status'] == 'reused_frozen_build' and p['measurements'])
    missing_prompt = records.ROOT / control['public_directory'] / 'prompt.txt'
    def missing_task(path):
        if Path(path) == missing_prompt:
            raise FileNotFoundError(path)
        return records.read_bytes(path)
    unavailable = cpp_points.collect(saved_data, missing_task)
    point = next(p for p in unavailable['points'] if p['public_directory'] == control['public_directory'])
    assert not point['inputs_current'] and not point['measurements'], 'Unverified task reused a model measurement'
    # Infrastructure failures must never be converted into a zero model score.
    with tempfile.TemporaryDirectory(prefix='rea-invalid-harness-', dir='/tmp') as temporary:
        directory = Path(temporary)
        (directory / 'harness-failure.json').write_text('{}')
        try:
            run.grade_saved(directory, {})
        except RuntimeError as error:
            assert 'cannot receive a model correctness grade' in str(error)
        else:
            raise AssertionError('A harness failure was graded as a model failure')
    # Replay certification fails closed on adapter/input changes or a missing receipt.
    assert not evidence_validation.for_report(lambda _: b'{}')['passed']
    fake = {'passed': True, 'inputs_sha256': {'benchmarks/harnesses/zcode.py': 'invalid'}}
    assert not evidence_validation.for_report(lambda path: json.dumps(fake).encode() if path == evidence_validation.RECEIPT else b'changed')['passed']
    # Missing, changed or out-of-scope audit inputs must never qualify as verified.
    assert not audit_environment.receipt_current({}, forbidden)
    assert not audit_environment.receipt_current({'inputs_sha256': {'README.md': 'invalid'}}, forbidden)
    known = records.BENCH / 'runs/native-pilot-20261019/run-manifest.json'
    assert not audit_environment.receipt_current(
        {'inputs_sha256': {str(known.relative_to(records.ROOT)): 'invalid'}}, lambda _: b'changed')
    # Partial streams and empty content deltas must survive offline accounting.
    events = [
        {'choices': [{'delta': {'reasoning_content': 'α', 'content': '',
                              'tool_calls': [{'index': 0, 'id': 'call-1'}]}}]},
        {'choices': [{'delta': {'content': 'ok'}, 'finish_reason': 'tool_calls'}],
         'usage': {'total_tokens': 3}},
    ]
    stream = b''.join(b'data: ' + json.dumps(e).encode() + b'\n\n' for e in events)
    partial, text = glm_diagnostics.stream_summary(stream)
    assert text == 'ok' and partial['reasoning_chars'] == 1 and partial['content_chars'] == 2
    assert partial['has_content_delta'] and not partial['done'] and partial['usage_events'] == 1
    assert partial['tool_ids'] == ['call-1'] and partial['finish_reasons'] == ['tool_calls']
    complete, _ = glm_diagnostics.stream_summary(stream + b'data: [DONE]\n\n')
    assert complete['done'] and complete['usage_events'] == 1
    # A cancelled batch must restore stopped state and refresh the report.
    # Neither backend nor execution-state file is touched by this local check.
    refresh = Mock()
    execution = {'experiments_stopped': True}
    paused = Mock(side_effect=lambda stopped, **kwargs: execution.update(experiments_stopped=stopped))
    read = records.read
    def controlled_state(path, default=None):
        return dict(execution) if Path(path) == records.BENCH / 'config/execution.json' else read(path, default)
    status_path = records.BENCH / 'config/benchmark-status.json'
    original_status = status_path.read_bytes()
    captured_status = []
    def fixture_save(path, value, exclusive=False):
        assert Path(path) == status_path, 'Cancellation fixture attempted an unexpected write'
        captured_status.append(value)
    with tempfile.TemporaryDirectory(prefix='rea-cancel-check-', dir='/tmp') as temporary:
        with patch.object(sys, 'argv', ['benchmark', 'run', '--model', 'glm-5.3', '--resume-experiments']), \
             patch.object(run, 'ROOT', Path(temporary)), \
             patch.object(run, 'selected_entries', return_value=[{'model': 'glm-5.3', 'case': 'fixture', 'status': 'not_started'}]), \
             patch.object(run, 'execute', side_effect=KeyboardInterrupt), \
             patch.object(run, 'save', side_effect=fixture_save), \
             patch.object(records, 'read', side_effect=controlled_state), \
             patch.object(run, 'set_execution_stopped', paused), patch.object(run, 'finalize', refresh):
            try:
                run.main()
            except KeyboardInterrupt:
                pass
    assert paused.call_args.args == (True,) and refresh.called, 'Interrupted execution did not pause and refresh reports'
    assert len(captured_status) == 1 and captured_status[0]['interrupted']
    assert status_path.read_bytes() == original_status, 'Cancellation fixture modified the global batch status'
    with tempfile.TemporaryDirectory(prefix='rea-report-repro-', dir='/tmp') as temporary:
        first, second, cached = [Path(temporary) / name for name in ('first', 'second', 'cached')]
        with patch.object(socket.socket, 'connect', forbidden), patch.object(socket, 'create_connection', forbidden), \
             patch.object(subprocess, 'run', forbidden), patch.object(subprocess, 'Popen', forbidden):
            # Both independent renders must bypass AC/CAS. Restoring the first
            # render as the second tree cannot establish deterministic rendering.
            with patch.object(report.action_cache.Cache, 'lookup', return_value=None):
                report.build(first)
                # Fail on stale receipts or invalid metrics before paying for
                # two more complete saved-data collections.
                pages, links = validate(first)
                report.build(second)
            # Exercise the ordinary cache-hit entrypoint separately, forbidding
            # rendering so this third tree must come from the bound AC/CAS result.
            with patch.object(report.Report, 'build', side_effect=AssertionError('Cache hit rendered again')):
                report.build(cached)
        assert hashes(first) == hashes(second), 'Clean report builds differ'
        assert hashes(first) == hashes(cached), 'Cached report restoration differs'
        # The HTML and metadata must also agree with saved totals.
        data = json.loads((first / 'data/comparison.json').read_text())
        for item in data['totals']:
            assert f'{item["total_tokens"]:,}' in (first / 'index.html').read_text()
        print(f'PASS: two byte-identical forced fresh offline renders plus cache restoration; {len(hashes(first))} artifacts; '
              f'{pages} HTML pages / {links} portable links; complete reproducible ZIP; no subprocesses or network')


if __name__ == '__main__':
    main()
