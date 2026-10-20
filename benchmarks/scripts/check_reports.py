#!/usr/bin/python3
"""Two clean offline builds must produce byte-identical complete report trees."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
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
    assert not any(r['status'] == 'running' for r in data['records'])
    assert any(r['status'] == 'interrupted' for r in data['records'])
    for row in data['records']:
        if row['status'] == 'inference_saved_grading_pending':
            assert row['receipt'] and not row['grade']
    overview = (directory / 'index.html').read_text()
    assert 'data-chart-panel="matched"' in overview and 'Coverage before scores' in overview
    assert 'Median saved runtime' in overview
    assert data['runner_validation']['passed'] and data['runner_validation']['inputs_current']
    assert data['runner_validation']['model_api_calls'] == 0
    assert data['runner_validation']['cache_hits_per_run'] == len(records.active_condition_rows(data))
    assert 'Configured benchmark and cache reuse' in overview
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
    assert not any(p.startswith(('.cache/', '.tools/')) for p in data['input_files_sha256'])
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
            assert not row['grade'] and not row['cost']['usage_complete']
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
    paused = Mock()
    with tempfile.TemporaryDirectory(prefix='rea-cancel-check-', dir='/tmp') as temporary:
        with patch.object(sys, 'argv', ['benchmark', 'run', '--model', 'glm-5.3', '--resume-experiments']), \
             patch.object(run, 'ROOT', Path(temporary)), \
             patch.object(run, 'selected_entries', return_value=[{'model': 'glm-5.3', 'case': 'fixture', 'status': 'not_started'}]), \
             patch.object(run, 'execute', side_effect=KeyboardInterrupt), \
             patch.object(run, 'set_execution_stopped', paused), patch.object(run, 'finalize', refresh):
            try:
                run.main()
            except KeyboardInterrupt:
                pass
    assert paused.call_args.args == (True,) and refresh.called, 'Interrupted execution did not pause and refresh reports'
    with tempfile.TemporaryDirectory(prefix='rea-report-repro-', dir='/tmp') as temporary:
        first, second = Path(temporary) / 'first', Path(temporary) / 'second'
        with patch.object(socket.socket, 'connect', forbidden), patch.object(socket, 'create_connection', forbidden), \
             patch.object(subprocess, 'run', forbidden), patch.object(subprocess, 'Popen', forbidden):
            report.build(first)
            report.build(second)
        assert hashes(first) == hashes(second), 'Clean report builds differ'
        pages, links = validate(first)
        # The HTML and metadata must also agree with saved totals.
        data = json.loads((first / 'data/comparison.json').read_text())
        for item in data['totals']:
            assert f'{item["total_tokens"]:,}' in (first / 'index.html').read_text()
        print(f'PASS: two byte-identical clean offline builds; {len(hashes(first))} artifacts; '
              f'{pages} HTML pages / {links} portable links; complete reproducible ZIP; no subprocesses or network')


if __name__ == '__main__':
    main()
