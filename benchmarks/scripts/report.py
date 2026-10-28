#!/usr/bin/python3
"""Generate every report offline from saved benchmark records and versioned inputs."""
import argparse
from collections import Counter
import csv
import datetime
import hashlib
import html
import io
import json
import os
import re
from pathlib import Path
import shutil
from statistics import median
from string import Template
import tempfile
import zipfile
import time

import records
import report_views
import localization_report
import i18n_report
import report_prices
import report_budget
from report_states import STATES as STATUSES
import report_charts as charts
import glm_diagnostics
import audit_environment
import evidence_validation
import cpp_points
import action_cache
import runtime_attestation
from grading import evidence as grading_evidence
from grading.c_executor import evidence as c_executor_evidence
from capabilities import summary_report as capability_summary
from capabilities import measurements as capability_measurements
from capabilities import registration_report

ROOT, BENCH = records.ROOT, records.BENCH
DEFAULT_OUTPUT = BENCH / 'reports'
ASSETS = Path(__file__).parent / 'report_assets'
escape = lambda value: html.escape(str(value), quote=True)



def json_text(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n'


def benchmark_timing_summary(status):
    """Project operational batch timings without private selection/error text."""
    timing = status.get('timings')
    if not timing or timing.get('schema_version') != 1:
        return None
    failures = status.get('failures', [])
    offline = status.get('offline') is True
    if status.get('running'):
        state = 'running'
    elif status.get('preparation_failed'):
        state = 'pre_dispatch_failure'
    elif status.get('interrupted'):
        state = 'stopped'
    elif (offline and failures and status.get('provider_launches') == 0
            and all(row.get('error') == 'Offline benchmark: missing inference cache; provider launch refused'
                    for row in failures)):
        state = 'offline_cache_miss'
    elif failures or status.get('queue_holds') or status.get('unprocessed_tasks', 0):
        state = 'incomplete'
    else:
        state = 'complete'
    return {'schema_version': 1, 'mode': 'offline' if offline else 'live', 'status': state,
            'total_seconds': timing['total_seconds'],
            'selected_tasks': status['selected_tasks'], 'cache_hits': status.get('cache_hits', 0),
            'provider_launches': status.get('provider_launches', 0),
            'model_api_calls': status.get('model_api_calls'),
            'failures': len(failures), 'unprocessed_tasks': status.get('unprocessed_tasks', 0),
            **({'queue_holds': len(status['queue_holds'])} if 'queue_holds' in status else {}),
            'stages': [{key: row[key] for key in ('stage', 'status', 'elapsed_seconds')}
                       for row in timing['stages']]}


def performance_summary():
    """Only bounded timings/counts enter public data; raw proofs stay private."""
    warm = records.read(BENCH / 'config/warm-cache-validation.json')
    cold = records.read(BENCH / 'config/cold-cache-timings.json')
    result = {'warm': None, 'cold': None}
    if warm:
        if warm.get('scope') != 'warm_artifact_integrity' or warm.get('model_api_calls') != 0 or warm.get('provider_launches') != 0:
            raise ValueError('Invalid offline warm timing receipt')
        # Track exact consulted bytes in report identity; never rerun acceptance.
        from concurrent.futures import ThreadPoolExecutor
        def current(item):
            name, expected = item
            path = ROOT.joinpath(*action_cache.relative(name).parts)
            try:
                return records.sha(path) == expected
            except OSError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            fresh = all(pool.map(current, warm['inputs_sha256'].items()))
        result['warm'] = {key: warm[key] for key in ('measured_at', 'total_seconds', 'budget_seconds',
            'prepared_cases', 'native_saved_cases', 'reusable_native_cases', 'retained_changed_input_cases',
            'accepted_regression_checks', 'verification_cache_hit', 'model_api_calls', 'provider_launches')}
        result['warm'].update(current=fresh, passed=warm['passed'],
            stages=[{key: row[key] for key in ('name', 'seconds')} for row in warm['stages']])
    if cold:
        result['cold'] = {key: cold[key] for key in ('measured_at', 'total_seconds', 'passed', 'complete')}
        result['cold']['stages'] = [{key: row[key] for key in ('name', 'seconds', 'exit_code')} for row in cold['stages']]
    benchmark = benchmark_timing_summary(records.read(BENCH / 'config/benchmark-status.json', {}))
    if benchmark:
        result['benchmark'] = benchmark
    return result


def money(cost):
    low, high = cost.get('low_usd'), cost.get('high_usd')
    if low is None:
        return 'Unknown'
    value = charts.usd(low)
    if abs(high - low) > 1e-10:
        value += '–' + charts.usd(high) + ' (rate range)'
    return value + (' known subtotal; missing usage, final cost unknown' if not cost.get('usage_complete', False) else '')


def table(headers, rows, identifier=None):
    rows = list(rows)
    attrs = ' id="' + escape(identifier) + '"' if identifier else ''
    if len(rows) > 50:
        attrs += ' data-page-size="50" data-table-search'
    caption = ' / '.join(headers)
    return '<div class="scroll" role="region" tabindex="0" aria-label="' + escape(caption) + '"><table' + attrs + '><caption class="sr-only">' + escape(caption) + '</caption><thead><tr>' + ''.join(
        '<th scope="col">' + escape(x) + '</th>' for x in headers) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join(('<th scope="row">' + str(x) + '</th>') if i == 0 else
            '<td' + (' class="numeric"' if isinstance(x, (int, float)) or (str(x)[:1].isdigit()) else '') + '>' + str(x) + '</td>'
            for i, x in enumerate(row)) + '</tr>' for row in rows) + '</tbody></table></div>'


def summary(rows, model_catalog):
    output = []
    for model in model_catalog:
        selected = [r for r in rows if r['model'] == model]
        if not selected:
            continue
        graded = [r for r in selected if r['grade']]
        receipts = [r for r in selected if r['receipt']]
        reviews = [r['readability']['total'] for r in graded if r['readability']]
        timed = [(charts.elapsed_seconds(r['receipt']), r['receipt'].get('timed_out', False))
                 for r in graded if charts.elapsed_seconds(r['receipt']) is not None]
        costs = [r['cost'] for r in receipts if r['cost'].get('low_usd') is not None]
        output.append({'model': model, 'model_label': model_catalog[model]['label'],
            'harness': model_catalog[model]['harness'], 'planned_cells': len(selected),
            'graded_attempts': len(graded), 'started_attempts': len(receipts),
            'programs_passed': sum(r['program_success'] for r in graded),
            'compiled': sum(r['grade']['compiled'] for r in graded),
            'passed': sum(r['grade']['passed'] for r in graded),
            'checks': sum(r['grade']['total'] for r in graded),
            'case_equal_mean': sum(100*r['grade']['passed']/r['grade']['total'] for r in graded if r['grade']['total']) / len([r for r in graded if r['grade']['total']]) if any(r['grade']['total'] for r in graded) else None,
            'points_passed': sum(r['points_passed'] for r in graded),
            'points_total': sum(r['points_total'] for r in graded),
            'readability_mean': sum(reviews) / len(reviews) if reviews else None,
            'readability_n': len(reviews),
            'accepted_source_n': sum(bool(r['source']) for r in graded),
            'runtime_median_seconds': median(t for t, _ in timed) if timed else None,
            'runtime_values_seconds': [t for t, _ in timed],
            'runtime_n': len(timed), 'runtime_timeouts': sum(timeout for _, timeout in timed),
            'total_tokens': sum(r['receipt'].get('reported_usage', {}).get('totalTokens') or 0 for r in receipts),
            'cost': {'low_usd': sum(c['low_usd'] for c in costs) if costs else None,
                     'high_usd': sum(c['high_usd'] for c in costs) if costs else None,
                     'usage_complete': all(r['cost'].get('usage_complete', False) for r in receipts)},
            'statuses': dict(Counter(r['status'] for r in selected))})
    return output


class Report:
    def __init__(self, output, data):
        self.output, self.data = output, data
        self.template = Template(records.read_bytes(ASSETS / 'page.html').decode())
        self.css = records.read_bytes(ASSETS / 'report.css')
        self.js = records.read_bytes(ASSETS / 'report.js')
        self.references = {}
        for suite in records.registry()['suites']:
            for path in sorted((ROOT / suite['path'] / 'authoring').rglob('*')):
                if path.is_file() and path.suffix in ('.cpp', '.inc', '.rs', '.go', '.h'):
                    self.references[str(path.relative_to(ROOT))] = records.read_bytes(path)
        self.answers = {}
        for row in data['records'] + data.get('condition_records', []):
            if row['source']:
                records.read_bytes(ROOT / row['source'])
            answer = ROOT / row['directory'] / 'answer.md'
            if answer.is_file():
                self.answers[row['directory']] = records.read_bytes(answer).decode()
        for path in [Path(__file__), Path(records.__file__), Path(charts.__file__), BENCH / 'scripts/pricing.py',
                     BENCH / 'scripts/usage.py', BENCH / 'scripts/grading/store.py']:
            records.read_bytes(path)
        self.aliases = records.read(BENCH / 'config/report-aliases.json', {})
        self.coverage = records.read(BENCH / 'suites/stl-native-v2/config/coverage.json')
        self.migration = records.read(BENCH / 'config/structure-migration.json', {})
        self.data['supplemental_pricing'] = {m: records.read(ROOT / info['pricing_file']) for m, info in data['models'].items() if info.get('pricing_file') and m != 'glm-5.3'}
        self.data['glm_diagnosis'] = glm_diagnostics.collect(data['records'], ROOT, records.read_bytes)
        records.read_bytes(Path(glm_diagnostics.__file__))
        self.data['environment_audit'] = audit_environment.for_report(data, records.read_bytes)
        records.read_bytes(Path(audit_environment.__file__))
        self.data['evidence_validation'] = evidence_validation.for_report(records.read_bytes)
        self.data['paging_validation'] = evidence_validation.for_report(records.read_bytes, BENCH / 'config/evidence-paging-validation.json')
        self.data['runner_validation'] = evidence_validation.for_report(records.read_bytes, BENCH / 'config/runner-validation.json')
        self.data['runtime_performance'] = performance_summary()
        self.data['runtime_attestation'] = runtime_attestation.for_report(records.read_bytes)
        self.data['grading_runtime'] = grading_evidence.for_report(records.read_bytes)
        self.data['c_executor'] = c_executor_evidence.for_report(records.read_bytes)
        self.data['capability_preparation'] = capability_summary.for_report(records.read_bytes)
        for row in self.data['records'] + self.data.get('condition_records', []):
            row['runtime_attestation_status'] = runtime_attestation.observation(ROOT / row['directory'], records.read_bytes,
                started=bool(row.get('receipt')) or row['status'] != 'not_started')
        self.data['benchmark_configuration'] = records.read(BENCH / 'config/benchmark.json')
        budget_policy = records.read(BENCH / 'config/request-contract.json')
        self.data['task_budget_policy'] = {'policy_id': budget_policy['id'], 'limits': budget_policy['task_limits']}
        if budget_policy.get('adaptive_budget'):
            self.data['task_budget_policy']['adaptive_budget'] = budget_policy['adaptive_budget']
        if budget_policy.get('wallet_limits'):
            self.data['task_budget_policy']['wallets'] = [{key: item[key] for key in
                ('model', 'currency', 'limit_nano', 'semantics', 'max_response_tokens') if key in item} for item in budget_policy['wallet_limits']]
        self.data['capability_measurements'] = capability_measurements.collect(records.read_bytes,
            data['models'], self.data['benchmark_configuration'])
        records.read_bytes(Path(capability_measurements.__file__))
        self.data['capability_measurements']['validation'] = capability_measurements.validation(records.read_bytes)
        self.data['capability_registration'] = registration_report.for_report(records.read_bytes, self.data['capability_measurements'])
        for path in ('registration_report.py', 'registration.py', 'build_metadata.py', 'metrics.py'):
            records.read_bytes(BENCH / 'scripts/capabilities' / path)
        dates = [value for value in (self.data.get('data_as_of'), self.data['capability_registration']['data_as_of']) if value]
        if dates:
            self.data['data_as_of'] = max(datetime.datetime.fromisoformat(value.replace('Z', '+00:00')) for value in dates).isoformat()
        self.data['benchmark_status'] = records.read(BENCH / 'config/benchmark-status.json', {})
        self.data['entrypoint_archive'] = records.read(BENCH / 'archive/entrypoint-cleanup/manifest.json')
        preservation = records.read(BENCH / 'config/cleanup-preservation.json')
        self.data['cleanup_preservation'] = {'active_tasks': preservation['active_tasks'],
                                           'preserved_files': len(preservation['files_sha256'])}
        records.read_bytes(BENCH / 'config/cache-compatibility.json')
        for name in ['run.py', 'cache.py', 'benchmark_config.py', 'check_runner.py']:
            records.read_bytes(BENCH / 'scripts' / name)
        self.data['cpp_build_points'] = cpp_points.collect(data, records.read_bytes)
        records.read_bytes(Path(cpp_points.__file__))
        records.read_bytes(Path(evidence_validation.__file__))
        live_audit = records.read(BENCH / 'config/condition-evidence-audit.json', {'cases': []})
        auditor = BENCH / 'scripts/audit_condition.py'
        auditor_hash = hashlib.sha256(records.read_bytes(auditor)).hexdigest()
        implementation = live_audit.get('auditor_implementation_sha256', {})
        expected_auditors = {'benchmarks/scripts/' + name for name in ['audit_condition.py', 'audit_environment.py', 'records.py']}
        implementation_current = set(implementation) == expected_auditors and all(
            hashlib.sha256(records.read_bytes(ROOT / name)).hexdigest() == digest for name, digest in implementation.items())
        for entry in live_audit['cases']:
            entry['inputs_current'] = implementation_current and live_audit.get('auditor_sha256') == auditor_hash and audit_environment.receipt_current(entry, records.read_bytes)
            if not entry['inputs_current']:
                entry['status'] = 'unverified'
        self.data['condition_evidence_audit'] = live_audit
        archives = records.read(BENCH / 'config/environment-profile-archives.json', {'profiles': {}})
        self.profile_files = {}
        for fingerprint, item in archives['profiles'].items():
            item['files_current'] = item.get('complete', True) and not item.get('missing_files_sha256')
            for name, digest in item['files_sha256'].items():
                try:
                    raw = records.read_bytes(ROOT / name)
                    if hashlib.sha256(raw).hexdigest() != digest:
                        item['files_current'] = False
                    else:
                        relative = str(Path(name).relative_to(item['directory']))
                        self.profile_files['data/environment-profiles/' + fingerprint + '/' + relative] = raw
                except OSError:
                    item['files_current'] = False
        self.data['environment_profile_archives'] = archives
        for row in data.get('condition_records', []):
            entry = next((c for c in live_audit['cases'] if (c['experiment'], c['case'], c['model'], c['condition']) == (row['experiment'], row['case']['id'], row['model'], row['condition'])), {})
            row['environment_delivery'] = entry.get('status', 'fixed_profile_not_independently_audited')
        for row in data['records']:
            row['environment_delivery'] = self.environment_case(row).get('status', 'unverified')
            row['model_only_comparison_certified'] = False
        report_prices.prepare(self.data)
        report_budget.prepare(self.data)
        localization_overlay = localization_report.load(records.read_bytes)
        if localization_overlay:
            self.data['localization_regrades'] = localization_overlay
        for module in (report_views, report_prices, report_budget, localization_report):
            records.read_bytes(Path(module.__file__))
        records.read_bytes(BENCH / 'scripts/report_states.py')
        self.data['input_files_sha256'] = dict(sorted(records.INPUTS.items()))
        self.build_id = hashlib.sha256(json_text(self.data).encode()).hexdigest()
        self.data['report_build_id'] = self.build_id
        if data['data_as_of']:
            date = datetime.datetime.fromisoformat(data['data_as_of']).astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            self.data_date = date.strftime('%Y-%m-%d %H:%M:%S JST')
        else:
            self.data_date = 'No model measurements recorded'

    def environment_case(self, row):
        if row.get('condition', 'legacy-recorded') != 'legacy-recorded':
            return {'status': 'fixed_profile_not_independently_audited'}
        return next((c for c in self.data['environment_audit']['cases'] if
                     (c['experiment'], c['case'], c['model']) ==
                     (row['experiment'], row['case']['id'], row['model'])), {})

    def environment_label(self, row):
        if row.get('condition', 'legacy-recorded') != 'legacy-recorded':
            if row.get('environment_delivery') == 'primary_complete':
                return 'Exact projection and retrieval verified' if row['condition'] == 'paged-evidence-v2' else 'Complete primary evidence at audited layer'
            return 'Fixed delivery profile; live evidence audit pending or unverified'
        entry = self.environment_case(row)
        if not entry:
            return 'Not audited'
        if entry.get('status') == 'unverified':
            return 'Unverified'
        spills = entry.get('counts', {}).get('spilled', 0)
        if spills:
            return str(spills) + ' spilled results; suggested recovery blocked'
        if entry.get('primary_cuts'):
            return str(entry['primary_cuts']) + ' first-copy clips'
        return 'Primary text complete at audited layer' if entry.get('status') == 'primary_complete' else 'Different wrapper / clipping policy'

    def write(self, name, content):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())

    def page(self, name, title, body):
        seen = set(re.findall(r'id="([^"]+)"', body))
        def section_anchor(match):
            base = re.sub('[^a-z0-9]+', '-', html.unescape(match[1]).lower()).strip('-') or 'section'
            anchor, suffix = base, 2
            while anchor in seen:
                anchor, suffix = base + '-' + str(suffix), suffix + 1
            seen.add(anchor)
            return '<section id="' + anchor + '"><h2>' + match[1] + '</h2>'
        body = re.sub(r'<section><h2>([^<]+)</h2>', section_anchor, body)
        parent = Path(name).parent
        link = lambda target: escape(os.path.relpath(target, str(parent)))
        subtitle = {'index.html': 'Recorded results for native reconstruction and typed capabilities.',
                    'dimensions.html': 'Measured model profiles across languages, behavior, build conditions and readability; missing evidence remains unmeasured.',
                    'costs.html': 'Known usage and published-rate estimates across both tracks; actual invoices unavailable.',
                    'capabilities.html': 'Typed capability observations, strict task scores and execution status.',
                    'capability-inventory.html': 'Searchable configuration, contracts and build evidence; preparation is not a model score.',
                    'cases/index.html': 'Native reconstruction cases, model outcomes and saved conditions.'}.get(name,
                    'Saved task, condition and outcome evidence.' if name.startswith(('attempts/', 'cases/')) else
                    'Methods, provenance and limitations of the saved benchmark results.')
        navigation = ''.join('<a href="' + link(target) + '">' + label + '</a>' for target, label in [
            ('index.html', 'Overview'), ('dimensions.html', 'Capability diagrams'), ('comparison.html', 'Native comparison'), ('cases/index.html', 'Native cases'), ('capabilities.html', 'Typed outcomes'),
            ('costs.html', 'Tokens & cost'), ('coverage.html', 'Coverage'), ('compilers.html', 'Build conditions'),
            ('diagnostics.html', 'Diagnostics'), ('measurement.html', 'Measurement validity'), ('methodology.html', 'Methodology'), ('history/index.html', 'History')])
        self.write(name, self.template.substitute(title=escape(title), stylesheet=link('assets/report.css'),
            script=link('assets/report.js'), locale_scripts=''.join('<script src="' + link('assets/' + name) + '" defer></script>' for name in ['locales/en.js', 'locales/zh-CN.js', 'locales/ja.js', 'i18n-config.js', 'i18n.js']), navigation=navigation, body=body,
            data_date=escape(self.data_date), build_id=self.build_id[:12], subtitle=escape(subtitle)))

    def summary_table(self, rows):
        return table(['Model / harness', 'Graded / configured', 'Programs passed',
                      'Check-weighted pass rate', 'Case-equal mean', 'AI readability'], [
            [escape(item['model_label']) + '<small>' + escape(item['harness']) + '</small>',
             f'{item["graded_attempts"]}/{item["planned_cells"]}',
             f'{item["programs_passed"]}/{item["graded_attempts"]}' if item['graded_attempts'] else 'Not graded (n=0)',
             f'{100*item["passed"]/item["checks"]:.2f}%<small>{item["passed"]:,}/{item["checks"]:,} checks</small>' if item['checks'] else 'Not graded (n=0)',
             f'{item["case_equal_mean"]:.2f}%<small>n={item["graded_attempts"]} cases</small>' if item['case_equal_mean'] is not None else 'Not graded (n=0)',
             f'{item["readability_mean"]:.2f}/10<small>Coordinating AI; n={item["readability_n"]}</small>' if item['readability_n'] else 'No reviewed accepted source (n=0)']
            for item in summary(rows, self.data['models'])])

    def case_link(self, row):
        suffix = '' if row.get('condition', 'legacy-recorded') == 'legacy-recorded' else '-' + row['condition']
        return 'cases/' + row['experiment'] + '/' + row['case']['id'] + suffix + '.html'

    def case_anchor(self, row):
        return row['model'] + (('-' + row['attempt_id'][:12])
            if not row.get('attempt_current', True) and row.get('attempt_id') else '')

    def all_records(self):
        return self.data['records'] + self.data.get('condition_records', [])

    def current_records(self):
        # Display current catalogue coverage using all valid saved caches, not
        # only the newest harness namespace. Individual evidence stays intact.
        return report_views.aggregate_native_rows(self.data)

    def condition_comparison(self):
        corrected = records.active_condition_rows(self.data)
        if not corrected:
            return ''
        original = [r for r in self.data['records'] if r['role'] == 'primary']
        body = '<section id="conditions"><h2>Original versus fixed harnesses</h2><p>The same 11 existing native cases are retained. Completed basic-case runs use the inline lossless v1 profile and are reused; the eight larger cases use the common paged v2 profile for every model. v2 adds a read-only evidence retrieval tool to the same analysis catalog, preserving complete results by hash and JSON-pointer pages. Binary and task text, reasoning settings, analysis tools and budgets remain unchanged. The three superseded v1 C++ setup attempts are retained separately and excluded from the active quality comparison.</p>'
        for label, rows in [('Fixed harnesses · reused basic v1 + paged hard cases v2', corrected), ('Original harnesses · legacy-recorded', original)]:
            body += '<h3>' + escape(label) + '</h3>' + self.summary_table(rows)
        by_key = {}
        for row in original + corrected:
            if row['grade']:
                phase = 'original' if row['condition'] == 'legacy-recorded' else 'fixed'
                by_key.setdefault((row['experiment'], row['case']['id']), set()).add((phase, row['model']))
        historical_models = {r['model'] for r in original}
        required = {(c, m) for c in ['original', 'fixed'] for m in historical_models}
        common = {k for k, cells in by_key.items() if required <= cells}
        body += '<h3>Equal-coverage before/after comparison</h3><p>' + str(len(common)) + ' cases have grades for every historical model in both conditions. Models without original-harness measurements are excluded from this before/after cohort. Missing attempts are excluded from this paired subset, and stay visible above. Readability reports its own source/review sample count.</p>'
        for label, rows in [('Fixed harnesses', corrected), ('Original harnesses', original)]:
            body += '<h4>' + label + '</h4>' + self.summary_table([r for r in rows if r['model'] in historical_models and (r['experiment'], r['case']['id']) in common])
        active_ids={(r['experiment'],r['case']['id'],r['model'],r['condition']) for r in corrected}
        diagnostics=[r for r in self.data.get('condition_records',[]) if (not r.get('attempt_current', True) or (r['experiment'],r['case']['id'],r['model'],r['condition']) not in active_ids) and r['receipt']]
        if diagnostics:
            body += '<h3>Preserved delivery-repair diagnostics and expenditure</h3><p>These v1 hard-case attempts were stopped or rejected by the provider context limit. They are not zero model scores and are not counted twice as completed fixed cases. Their observed tokens and costs remain in this table and the cumulative ledger.</p>' + self.summary_table(diagnostics)
        return body + '<p>Cases run sequentially within each model, with up to three models active together. Shared machine load can affect runtime. These are single-attempt measurements, not a replicated causal study. Token and price estimates use recorded provider usage and the same frozen pricing basis; actual invoices remain unavailable. Removing evidence loss does not equalize different system prompts, model context windows or provider behavior.</p></section>'

    def case_table(self, rows, prefix='', identifier=None):
        result = []
        for row in rows:
            grade = row['grade']
            state = STATUSES[row['status']]
            if grade:
                state = ('All checks passed' if row['program_success'] else
                         'No accepted native source' if (row['receipt'] or {}).get('timed_out') and not row['source'] else
                         'Submission format rejected' if 'Expected' in (grade.get('failure') or '') else
                         'Compile failed; semantic checks not executed' if not grade['compiled'] else 'Compiled; behavioral checks not all passed')
            if (row['receipt'] or {}).get('timed_out'):
                state += '; inference timed out'
            budget_label = report_budget.outcome_label(row.get('budget_condition', {}).get('outcome'))
            if grade and budget_label:
                state += '; ' + budget_label
            elapsed = charts.elapsed_seconds(row['receipt'])
            result.append(['<a href="' + escape(prefix + self.case_link(row) + '#' + self.case_anchor(row)) + '">' + escape(row['case']['label']) + '</a><small>' + escape(row['experiment_label'] + ' · output ' + str(row.get('output_language') or row['case']['language'])) + '</small>',
                escape(row['model_label']) + '<small>' + escape(row.get('condition_label', 'Original harnesses')) + '</small><small>Evidence: ' + escape(self.environment_label(row)) + '</small>', escape(state),
                f'{grade["passed"]:,}/{grade["total"]:,}' if grade else 'Not graded',
                f'{row["points_passed"]}/{row["points_total"]}' if grade else '—',
                str(row['readability']['total']) + '/10' if row['readability'] else 'No accepted native source' if grade and not row['source'] else 'Pending review',
                f'{elapsed / 60:.1f} min' if elapsed is not None else 'Unreported',
                report_views.count(row['receipt'].get('reported_usage', {}).get('totalTokens')) if row['receipt'] else '—',
                money(row['cost']) if row['receipt'] else '—'])
        return table(['Case', 'Model', 'Status', 'Exact checks', 'Points', 'Readability', 'Elapsed', 'Known tokens', 'API estimate'], result, identifier)

    def overview(self):
        report_views.overview(self)

    def case_pages(self):
        groups = {}
        for row in self.all_records():
            groups.setdefault((row['experiment'], row['case']['id'], row.get('condition', 'legacy-recorded')), []).append(row)
        for (experiment, case, condition), rows in groups.items():
            info = rows[0]['case']
            body = '<section><h2>' + escape(rows[0]['experiment_label']) + '</h2>' + self.case_table(rows, '../../') + table(
                ['Frozen condition', 'Value'], [
                    ['Language', escape(info['language'])], ['Standard / string ABI', escape(str(info['standard']) + ' / ' + str(info['string_abi']))],
                    ['Tool count / call ceiling', f'{info["tool_count"]} / {info["max_tool_calls"]}'],
                    ['Wall limit / response ceiling', f'{info["timeout_seconds"]} seconds / {info["max_output_tokens"]:,} tokens']]) + '</section>'
            body += '<section><p>Harness condition: <strong>' + escape(rows[0].get('condition_label', condition)) + '</strong>. <a href="../../history/index.html#conditions">Global before/after comparison</a>.</p></section>'
            for row in rows:
                body += '<section id="' + escape(self.case_anchor(row)) + '"><h2>' + escape(row['model_label']) + '</h2><p>' + escape(STATUSES[row['status']]) + ' · ' + escape(row['harness']) + '</p>'
                body += '<p class="notice">Environment audit: ' + escape(self.environment_label(row)) + '. This is an observed setup result, not an environment-independent model score. <a href="../../diagnostics.html#environment">Audit details</a>.</p>'
                body += report_budget.detail(row.get('budget_condition'))
                if row.get('target_build'):
                    build = row['target_build']
                    body += '<p>Target build: <strong>' + escape(build['compiler_family'] + ' ' + build['compiler_version'] + ' / -' + build['optimization']) + '</strong>; ' + escape(build['standard']) + ', ' + escape(build['standard_library']) + ', string ABI ' + str(build['string_abi']) + ', strip ' + escape(build['strip_mode']) + '. <a href="../../compilers.html">Distinct compiler/optimization test points</a>.</p>'
                if row['model'] == 'glm-5.3' and condition == 'legacy-recorded':
                    body += '<p><a href="../../diagnostics.html#' + escape(experiment + '-' + case) + '">Cached GLM trace diagnosis for this case</a></p>'
                receipt = row['receipt'] or {}
                if receipt:
                    details = []
                    elapsed = receipt.get('elapsed_seconds')
                    if elapsed is None and receipt.get('elapsed_ms') is not None:
                        elapsed = receipt['elapsed_ms'] / 1000
                    if elapsed is not None:
                        details.append(f'{elapsed:,.1f} seconds elapsed')
                    for key, label in [('tool_calls', 'tool calls'), ('tool_errors', 'tool errors')]:
                        if receipt.get(key) is not None:
                            details.append(str(receipt[key]) + ' ' + label)
                    if details:
                        body += '<p class="chart-note">Saved attempt: ' + escape(' · '.join(details)) + '.</p>'
                if row.get('environment_failure'):
                    failure = row['environment_failure']
                    body += '<p class="notice">Harness/provider failure: ' + escape(failure.get('classification', 'Recorded infrastructure failure')) + '. No model correctness grade is assigned. Observed usage is retained; an interrupted or rejected request can have unreported usage.</p>'
                grade = row['grade']
                if (row['receipt'] or {}).get('timed_out'):
                    body += '<p><strong>Inference reached the frozen wall-time limit.</strong> Any recovered source is graded unchanged; incomplete answers are retained as failures. This is distinct from a compiled program returning incorrect results.</p>'
                if grade:
                    body += '<p>Outcome: ' + escape(grade.get('failure') or ('All checks passed' if row['program_success'] else 'Behavior mismatch')) + '</p>'
                    if grade.get('points'):
                        body += table(['Function point', 'Exact checks'], [
                            [escape(point), f'{score["passed"]}/{score["total"]}'] for point, score in grade['points'].items()])
                if row['readability']:
                    review = row['readability']
                    body += '<h3>Readability</h3>' + table(['Dimension', 'Score /2'], [
                        [escape(key), value] for key, value in review['scores'].items()]) + '<p>' + escape(review['notes']) + '</p>'
                elif row['source']:
                    body += '<p>Readability review pending. No score is inferred from correctness.</p>'
                elif grade:
                    body += '<p>No source was accepted under the frozen single-block contract, so a source readability score is unavailable. The complete visible submission below may still contain code.</p>'
                if row['directory'] in self.answers:
                    answer = self.answers[row['directory']]
                    target = 'answers/' + experiment + '/' + condition + '/' + row['model'] + '/' + case + '.md'
                    self.write(target, answer)
                    body += '<details><summary>Complete visible submission, unchanged</summary><p><a href="../../' + escape(target) + '">Download visible answer</a>. This includes progress text and the final submission, without hidden reasoning or tool outputs. The frozen grading contract requires exactly one native code block; additional explanatory fences can cause a format rejection.</p><pre>' + escape(answer) + '</pre></details>'
                if row['source']:
                    source = ROOT / row['source']
                    code = records.read_bytes(source).decode()
                    target = 'code/' + experiment + '/' + ('' if condition == 'legacy-recorded' else 'conditions/' + condition + '/') + row['model'] + '/' + case + source.suffix
                    self.write(target, code)
                    body += '<details><summary>Unchanged recovered ' + escape(source.suffix) + ' source</summary><a href="../../' + escape(target) + '">Download source</a><pre><code>' + escape(code) + '</code></pre></details>'
                if row['receipt']:
                    target = 'data/attempts/' + experiment + '/' + ('' if condition == 'legacy-recorded' else 'conditions/' + condition + '/') + row['model'] + '/' + case + '.json'
                    self.write(target, json_text(row))
                    body += '<p>Estimated API cost: ' + money(row['cost']) + '. <a href="../../' + escape(target) + '">Saved result, usage, grade and provenance</a>.</p>'
                    if grade and grade.get('first_mismatches'):
                        body += '<details><summary>First scored mismatches</summary><pre>' + escape(json_text(grade['first_mismatches'])) + '</pre></details>'
                body += '</section>'
            body = '<section><h2>Case identity</h2><p>' + escape(report_views.case_identity(rows[0])) + '</p></section>' + body
            body += report_views.readability_note()
            self.page(self.case_link(rows[0]), info['label'] + ' · ' + rows[0].get('condition_label', condition), body)
        primary = self.current_records()
        body = '<section><h2>At a glance: native recovery</h2><p class="notice">The matrix shows the fixed harnesses. Original results remain in the full table below. <a href="../diagnostics.html#environment">Model-only attribution is not certified</a>; matching cases does not remove system-prompt and provider differences.</p>' + charts.heatmap(primary, self.data['models'], '../') + '</section>'
        body += '<section><h2>Every recorded comparison cell and condition</h2><label>Filter by case, model or status <input data-table-filter="all-cases" aria-label="Filter cases"></label>' + self.case_table(self.all_records(), '../', 'all-cases') + '</section>'
        self.page('cases/index.html', 'All cases and models', body)

    def costs(self):
        report_views.costs(self)

    def task_budget_section(self):
        policy = self.data.get('task_budget_policy')
        if not policy:
            return '<section id="task-budgets"><h2>Task spending limits</h2><p>No cumulative task policy was recorded in this saved snapshot. Historical results are unchanged.</p></section>'
        if policy.get('adaptive_budget'):
            a = policy['adaptive_budget']
            return ('<section id="task-budgets"><h2>Task spending limits</h2><p>New lean-evidence-v4 attempts: '
                + f"{a['max_response_tokens']:,} tokens per response on provider-capped routes. "
                + ('No turn-count limit. ' if a['max_model_turns'] is None else f"{a['max_model_turns']} turns. " )
                + 'Codex OAuth has an observed response threshold, not a provider cap. Basic native and its compiler variants have no aggregate token/cost limit. '
                + 'Other cases use twice the same-model/provider/type mean of complete answers, frozen before each case; the latest complete answer per case contributes once. '
                + 'Incorrect complete answers are included; incomplete stops are excluded. With no qualifying sample, the first case has no aggregate threshold. '
                + 'DeepSeek/GLM use separately priced cached input, fresh input and output. Routes without a fixed price use observed total tokens. '
                + 'The last admitted response may overshoot; this is not a hard invoice cap. Wall-time, tool and zero-retry guards remain active. '
                + 'Historical results retain their original conditions. Correctness and budget compliance are reported separately. Policy: <code>'
                + escape(policy['policy_id']) + '</code>.</p></section>')
        limits = policy['limits']
        wallets = ''.join('<p>Lean-v4 ' + escape(w['model']) + ': ' + escape(w['currency']) + ' ' + str(w['limit_nano']/10**9) + '/case fixed-price stop threshold.' + (f" Response ceiling {w['max_response_tokens']:,} tokens, reduced to remaining task allowance." if w.get('max_response_tokens') else '') + ' Cache reads use their lower price; full total tokens are still reported. Final response may overshoot; not a hard invoice cap.</p>' for w in policy.get('wallets', []))
        return '<section id="task-budgets"><h2>Task spending limits</h2><p>Configuration for future launches only; these limits do not retroactively certify or change historical results.</p>' + table(
            ['Bound', 'Configured limit'], [[label, f'{limits[key]:,}'] for key, label in (
                ('max_model_turns', 'Model requests / turns'), ('max_response_tokens', 'Default response cap (provider-capped routes only; wallet overrides below)'),
                ('max_output_tokens', 'Task output-token threshold'),
                ('max_observed_total_tokens', 'Legacy total-token threshold (replaced by wallet policy on eligible lean-v4 routes)'))]) + '<p>Provider-capped routes reserve each response cap before forwarding; final known output releases unused allowance and missing usage retains the reservation. The separately authorized signed-in Codex OAuth route sends no provider response cap. Its turn, time and tool limits remain active; output and total-token thresholds stop later requests using observed usage. A single response can exceed those thresholds, and missing usage stops further requests. These are distinct budget conditions, not equal output ceilings.</p><p>Reaching a task limit stops this attempt without retry; the configured queue policy may continue to the next case. Incomplete budget-stopped attempts remain ungraded. A complete final candidate can be graded even if its last response crosses an aggregate threshold; it remains labelled budget exceeded / attempt stopped and is not budget-compliant. Correctness is checked offline after submission; hidden tests do not guide live stopping. Recorded request receipts identify each attempt’s actual condition; unidentified historical limits remain unknown. Provider accounting is route-specific. GPT costs are API-equivalent estimates; actual Codex subscription debit is unknown. Policy: <code>' + escape(policy['policy_id']) + '</code>.</p>' + wallets + '</section>'

    def measurement(self):
        rows = self.all_records()
        graded = [row for row in rows if row.get('grade')]
        revised = [row for row in graded if row['grade'].get('grading_version') in ('exact-returns-v2', 'exact-returns-v3')]
        changed = [row for row in revised if row['grade'].get('original_passed') != row['grade']['passed'] or
                   row['grade'].get('original_program_success') != row['program_success']]
        body = '<section><h2>Grading and accounting revisions</h2><p>' + str(len(revised)) + '/' + str(len(graded)) + ' saved grades use the shared exact-byte return protocol. ' + str(len(changed)) + ' attempts have changed passed-check counts or program-success status. Original candidates, inference and grades are retained; offline regrading is not another model attempt.</p><p>Frames include invocation ID, value kind, length and exact bytes. Candidate stdout noise, missing/extra/duplicate IDs and malformed lengths are distinguished. Go restricted imports are inspected through its syntax tree. Native-process frames remain unauthenticated: these revisions do not establish return provenance or certify historical request budgets and runtime identity.</p></section>'
        body += self.audit_plan_section()
        body += self.task_budget_section()
        body += self.runtime_section()
        body += self.grading_runtime_section()
        body += self.c_executor_section()
        body += self.capability_preparation_section()
        body += '<section><h2>Action cache v3</h2><p>Inference, grading, accounting and report rendering have separate declared inputs. Each action hashes a canonical command, effective environment/platform and a Merkle input tree. The action cache maps that digest to output digests in a local content-addressable store. Report, test, scheduling and storage edits do not invalidate inference or grading. Go import checking affects Go grades only; changing ZCode delivery settings does not invalidate DSH results. Existing byte-verified observations are imported without new model or native evaluation calls. Cache hits reuse an observation, rather than adding a benchmark trial. New inference actions bind the local runtime manifest and resolved provider before hashing. Historical unattested observations keep their original action namespace and are never promoted to an attested execution. Provider budget guards and restricted authenticated-return execution have their own acceptance gates; a cache hit does not retroactively certify either.</p></section>'
        body += '<section><h2>Selected grades and original baseline</h2>' + table(
            ['Case', 'Model', 'Condition', 'Selected grader', 'Original checks', 'Selected checks', 'Program passed', 'Return provenance'], [
                [escape(row['case']['label']), escape(row['model_label']), escape(row.get('condition_label', row['condition'])),
                 escape(row['grade'].get('grading_version', 'legacy')),
                 str(row['grade'].get('original_passed', row['grade']['passed'])) + '/' + str(row['grade'].get('original_total', row['grade']['total'])),
                 str(row['grade']['passed']) + '/' + str(row['grade']['total']), str(row['program_success']),
                 escape(row['grade'].get('return_provenance', 'legacy; not authenticated'))]
                for row in graded]) + '</section>'
        body += '<section><h2>Usage evidence</h2><p>Completeness is derived from request-linked usage evidence, independently of process exit or task success. Explicit incomplete flags survive normal exit; repeated cumulative stream usage is counted once per request. Missing request identities or usage keep the total bill unknown. Published-rate figures cover known usage only and are not invoices.</p>' + table(
            ['Case', 'Model', 'Condition', 'Usage status', 'Observed USD estimate'], [
                [escape(row['case']['label']), escape(row['model_label']), escape(row.get('condition_label', row['condition'])),
                 escape(row['cost'].get('usage_status', 'legacy_unknown')), money(row['cost'])]
                for row in rows if row.get('receipt')]) + '</section>'
        self.page('measurement.html', 'Measurement validity and revisions', body)

    def capability_overview(self):
        value = self.data.get('capability_measurements', capability_measurements.empty())
        return ('<section><h2>Typed capability coverage</h2><p>' + str(value['scheduled_cells']) +
                ' enabled scheduled cells out of ' + str(value['configured_cells']) + ' configured cells; ' +
                str(value['disabled_cells']) + ' disabled. ' + str(value['saved_observations']) +
                ' unique saved model observations. These are separate from native reconstruction scores. '
                '<a href="capabilities.html">Capability vectors, denominators and revision history</a>.</p></section>')

    def capability_costs(self):
        value = self.data.get('capability_measurements', capability_measurements.empty())
        return ('<section><h2>Typed capability expenditure</h2><p>All unique saved typed attempts, including retired settings and failures. '
                'Cache copies and grading revisions add no model samples or token charges. Values are known subtotals; '
                'missing usage remains unknown. Rate estimates are not invoices.</p>' + table(
                ['Model', 'Saved observations', 'Known tokens', 'Observed USD estimate'], [
                    [escape(row['model']), row['saved_observations'],
                     'Unknown' if row['known_tokens']['totalTokens'] is None else f'{row["known_tokens"]["totalTokens"]:,}',
                     money(row['cost'])] for row in value['totals']]) + '</section>')

    def capability_control_section(self, value):
        controls = [row for row in value['history'] if row['analysis_condition'] == 'premises-only-v1']
        if not controls:
            return ''
        groups = {(row['model'], row['public_package_sha256'], row['profile_id']) for row in controls}
        rows = [row for row in value['history']
                if (row['model'], row['public_package_sha256'], row['profile_id']) in groups]
        rows.sort(key=lambda row: (row['model'], row['public_package_sha256'], row['analysis_condition'], row['attempt_id']))
        return ('<section id="premises-only-controls"><h2>Paired evidence-access diagnostics</h2>'
                '<p>These are separately registered recovery and premises-only observations of the same public packages. '
                'The control keeps the public premises and original grading contract while withholding target bytes and '
                'all analysis tools. It does not join recovery capability macros. Grouping by package does not establish '
                'a causal model-ability claim or an independently reviewed task. Runtime and request-budget certification '
                'remain visible; missing results are unscored. Readability is not applicable to structured type artifacts.</p>' + table(
            ['Model', 'Public task', 'Task condition', 'Evidence condition', 'State', 'Checks', 'Capability scores',
             'Known tokens', 'Observed USD estimate', 'Runtime identity', 'Request budget'], [
                [escape(row['model']), escape(row['task_id']), escape(row['profile_id']), escape(row['analysis_condition']),
                 escape(row['status']), str(row['grade']['passed']) + '/' + str(row['grade']['total']) if row['grade'] else 'Unscored',
                 escape('; '.join(cap + ': ' + ('Unmeasured' if score['score'] is None else f'{100*score["score"]:.2f}%')
                                  for cap, score in row['grade']['scores'].items())) if row['grade'] else 'Unscored',
                 'Unknown' if row['known_tokens'].get('totalTokens') is None else f'{row["known_tokens"]["totalTokens"]:,}',
                 money(row['cost']), escape(row['runtime_status']), escape(row['budget_status'])]
                for row in rows]) + '</section>')

    def capabilities_page(self):
        report_views.capabilities(self)

    def capability_inventory_page(self):
        value = report_views.capability_view(self.data)
        number = lambda x: 'Unmeasured' if x is None else f'{100*x:.2f}%'
        validation = value['validation']
        body = ('<section><h2>Typed measurement status</h2><p>The capability expansion is not released: ' +
                str(value['released_capabilities']) + '/19 sub-capabilities have passed the release gate. '
                'The current inventory contains ' + str(value['declared_tasks']) + ' declared task definitions. '
                'It does not turn preparation controls into model scores.</p>' + charts.capability_coverage(value) +
                '<p>Denominator status: <code>' + escape(value['schedule_status']) + '</code>. Configuration snapshot: <code>' +
                escape(value['schedule_id'][:16] or 'not recorded') + '</code>. This current-configuration snapshot is not '
                'evidence of a denominator frozen before a historical run. Invalid, pending and unstarted enabled cells '
                'remain in the scheduled denominator. Disabled cells are shown separately.</p><p>Offline collection controls: ' +
                str(validation['checks']) + '; passed: ' + str(validation['passed']) + '; source current: ' +
                str(validation['current']) + '. No model, compiler or candidate execution is needed to regenerate this view.</p></section>')
        body += ('<section><h2>Analysis conditions</h2><p>Default auto-analysis uses '
                 '<code>paged-evidence-v2</code>. The optional <code>public-given-entrypoints-v1</code> '
                 'condition initializes anonymous function entries from the task\'s supplied RVAs and public ELF '
                 'load mappings, making those supplied caller/callee boundaries available to function queries. '
                 'Localization remains unseeded. The original binaries, public packages and earlier attempts are '
                 'preserved. These analysis conditions remain separate in the capability summaries.</p>'
                 '<p>Conditions ending in <code>.role-string-v1</code> additionally deliver an explicit public '
                 'serialization rule: each record-field role is one identifier string, while supplied semantic paths '
                 'are arrays. The exact notice is a prompt input bound by the inference action. Earlier cached '
                 'submissions retain their original condition and grade.</p>'
                 '<p>Incomplete budget-exhausted attempts retain their observed tokens and estimated costs and remain ungraded. Complete final answers that cross a threshold retain their correctness grade separately from the budget exceeded / attempt stopped label. '
                 'A correctly bound budget-stop receipt can verify request enforcement independently of task success. '
                 'Strict runtime certification still requires its completion evidence; saved pre/post checks alone '
                 'do not retroactively create a missing completion manifest.</p></section>')
        body += ('<section><h2>Completion, artifact validity and readability</h2>' + table(
            ['Model', 'Configured', 'Scheduled', 'Graded', 'Valid artifacts', 'Tasks fully correct', 'Readability / 10', 'Reviewed n'], [
                [escape(row['model']), row['configured_cells'], row['scheduled_cells'], row['graded_cells'],
                 row['artifact_valid_cells'], row['successful_cells'],
                 'Unreviewed' if row['readability_mean'] is None else f'{row["readability_mean"]:.2f}', row['readability_n']]
                for row in value['totals']]) + report_views.localization_note(self.data) + '<p>Localization regrades are separate evidence revisions. Other task kinds retain their original artifact-admission rules.</p>'
                '<p>Code readability uses the existing five dimensions, each 0–2: names, decomposition, '
                'language idioms, formatting and explanatory comments. A review must bind the selected source artifact. '
                'Structured localization/type artifacts do not receive a source-readability score. Stored reviewer provenance '
                'is shown below; this collector does not establish independent review or a confidence interval.</p></section>')
        body += ('<section><h2>Capability vectors and family macros</h2><p>Metrics stay separate: L1 uses top-1 success, L2–L4 use F1, '
                'L5 uses full-chain success with component diagnostics, and C/T use their capability check fraction. '
                'No applicable semantic fact yields no metric observation, rather than free credit. Variants average within '
                'a family; related families average within a declared lineage; lineages receive equal weight. '
                'All current integer-record ABI forms share one lineage. Recovery and full-semantics generation conditions '
                'are separate rows. Premises-only controls remain in saved history and expenditure, separate from recovery macros. '
                'An observed-subset macro is descriptive; a full-denominator macro requires every '
                'scheduled cell to have a grade. Shared-case macros use the same graded public packages across selected models.</p>' + table(
            ['Model', 'Condition', 'Capability', 'Graded / scheduled', 'Metric n', 'Families observed / scheduled', 'Lineages',
             'Observed subset', 'Full denominator', 'Shared cells', 'Shared macro'], [
                [escape(row['model']), escape(row['profile_id']), escape(row['capability_id']),
                 str(row['graded_cells']) + '/' + str(row['scheduled_cells']), row['metric_observations'],
                 str(row['observed_families']) + '/' + str(row['scheduled_families']), row['observed_lineages'],
                 number(row['observed_macro']), number(row['complete_macro']), row['matched_cells'], number(row['matched_macro'])]
                for row in value['macro_vectors'] if row['metric_observations']]) + '</section>')
        body += '<section><h2>Per-attempt capability metrics</h2>' + table(
            ['Attempt', 'Model', 'Evidence condition', 'Capability', 'Score', 'Component diagnostics'], [
                [escape(row['attempt_id'][:12]), escape(row['model']), escape(row['analysis_condition']), escape(capability), number(metric['score']),
                 escape('; '.join(name + ': ' + number(score) for name, score in metric['components'].items()))]
                for row in value['history'] if row['grade'] for capability, metric in row['grade']['scores'].items()]) + '</section>'
        contracts = {row['task_id']: row for row in value['cells']}
        body += '<section><h2>Target and output contracts</h2>' + table(
            ['Public task', 'Build identity', 'Binary SHA', 'Source language', 'Output language', 'Hint profile', 'Analysis condition'], [
                [escape(row['task_id']), escape((row['build_id'] or 'unprepared')[:12]),
                 escape((row['binary_sha256'] or 'unprepared')[:12]), escape(row['source_language']),
                 escape(row['output_language'] or 'structured artifact'), escape(row['hint_profile']), escape(row['analysis_condition'])]
                for _, row in sorted(contracts.items())]) + '</section>'
        body += '<section><h2>Configured cells</h2><label>Filter <input data-table-filter="typed-cells" aria-label="Filter capability cells"></label>' + table(
            ['Public task', 'Family', 'Condition', 'Model', 'Preparation', 'State', 'Capabilities', 'Grading revision'], [
                [escape(row['task_id']), escape(row['family_id']), escape(row['profile_id']), escape(row['model']),
                 escape(row['preparation_status']), escape(STATUSES.get(row['status'], row['status'])), escape(', '.join(row['capability_ids'])),
                 escape(row['observation']['grade']['grading_id'][:12]) if row['observation'] and row['observation']['grade'] else 'Unscored']
                for row in value['cells']], 'typed-cells') + '</section>'
        body += self.capability_costs()
        body += self.capability_control_section(value)
        body += self.registration_section()
        body += ('<section><h2>Saved observations and certification</h2><p>' + str(value['duplicate_saved_copies']) +
                ' duplicate saved copies excluded; ' + str(value['invalid_attempt_records']) +
                ' invalid attempt records retained as gaps. Retired settings remain in this history and expenditure, '
                'without joining the current comparison. Infrastructure failures have no correctness score.</p>' + table(
            ['Attempt', 'Model', 'Public task', 'Condition', 'Evidence condition', 'State', 'Artifact', 'Checks', 'Return evidence', 'Runtime identity', 'Request budget', 'Usage', 'Readability provenance'], [
                [escape(row['attempt_id'][:12]), escape(row['model']), escape(row['task_id']), escape(row['profile_id']),
                 escape(row['analysis_condition']), escape(STATUSES.get(row['status'], row['status'])),
                 ('Valid' if row['grade']['artifact_valid'] else 'Rejected') if row['grade'] else 'Ungraded',
                 str(row['grade']['passed']) + '/' + str(row['grade']['total']) if row['grade'] else 'Unscored',
                 escape(row['grade']['return_provenance']) if row['grade'] else 'not_executed',
                 escape(row['runtime_status']), escape(row['budget_status']), escape(row['usage_status']), escape(row['readability']['provenance'])]
                for row in value['history']]) + '</section>')
        revisions = []
        for row in value['history']:
            for item in row['grade_revisions']:
                format_only = report_views.typed_category(row) == 'localization' and (
                    row.get('localization_revision', {}).get('original_grading_id') == item['grading_id']
                    or row['status'] == 'localization_format_review' and item['selected'])
                revisions.append([escape(row['attempt_id'][:12]), escape(item['grading_version']), escape(item['grading_id'][:12]),
                                  item['selected'], 'Not graded (n=0)' if format_only else str(item['passed']) + '/' + str(item['total']),
                                  'Unmeasured' if format_only else item['task_success']])
        body += ('<section><h2>Append-only grading and accounting revisions</h2><p>Selected grades use the explicit saved selection index. '
                'Other grading revisions remain visible and add no observations. Accounting shown above is derived from the '
                'current saved pricing under an explicit policy; a saved accounting revision is never chosen by filename order. '
                'Original request evidence remains unchanged.</p><p>Original localization format grades remain in the evidence archive; they are not semantic correctness scores.</p>' + table(
            ['Attempt', 'Grader version', 'Grading identity', 'Selected', 'Checks', 'Task success'], revisions))
        body += table(['Attempt', 'Accounting version', 'Accounting identity', 'Usage status', 'Known USD subtotal'], [
            [escape(row['attempt_id'][:12]), escape(item['version']), escape(item['accounting_id'][:12]),
             escape(item['usage_status']), money({**item, 'usage_complete': item['usage_status'] == 'complete'})]
            for row in value['history'] for item in row['accounting_revisions']]) + '</section>'
        self.page('capability-inventory.html', 'Typed capability configuration and evidence', '<section><p><a href="capabilities.html">Return to observed outcomes</a>. Catalogue only: configured cells are not evidence of execution.</p></section>' + body)

    def registration_section(self):
        value = self.data.get('capability_registration', registration_report.empty())
        proof = value['validation']
        body = ('<section id="registration"><h2>Immutable pre-run registration</h2><p>New capability dispatch requires an immutable '
                'schedule and a complete execution binding before any selected queue starts. The schedule fixes selection, '
                'budgets, metric definitions, family weights and parallelism. The binding fixes original builds, grading '
                'contracts and every prospective runtime/provider route. Unsupported caps fail before runtime inventory. '
                'A single-use dispatch intent links each new task to those records. Cached observations keep their original '
                'provenance; offline reuse creates no new experiment. Registration is local admission evidence, not a remote '
                'signature or model-weight attestation.</p><p>Saved schedules: ' + str(len(value['schedules'])) +
                '; invalid records: ' + str(value['invalid_records']) + '; local control records excluded: ' +
                str(value['control_records_excluded']) + '. Registration controls: ' + str(proof['checks']) +
                '; passed: ' + str(proof['passed']) + '; source current: ' + str(proof['current']) + '.</p>')
        body += table(['Registration', 'Created', 'Scheduled', 'Prospective', 'Prior observations', 'Parallel models', 'Complete bindings'], [
            [escape(row['registration_id'][:12]), escape(row['registered_at']), row['scheduled_cells'], row['prospective_cells'],
             row['prior_cells'], row['parallel_models'], len(row['bindings'])] for row in value['schedules']])
        body += table(['Binding', 'Bound cells', 'Dispatch intents', 'Runtime identities', 'Provider route identities'], [
            [escape(bound['binding_id'][:12]), bound['bound_cells'], bound['dispatch_intents'],
             escape(', '.join(identity[:12] for identity in bound['runtime_ids'])),
             escape(', '.join(identity[:12] for identity in bound['provider_ids']))]
            for row in value['schedules'] for bound in row['bindings']]) + '</section>'
        build_rows = []
        for row in value['builds']:
            metadata = row['metadata']
            identities = ('build_id', 'binary_sha256', 'compiler_image_id', 'compiler_executable_sha256',
                          'binutils_image_id', 'flag_summary_sha256')
            details = ('<details class="build-identities"><summary>Flags and full identities</summary><p>Safe flags: <code>' +
                       escape(' '.join(metadata['flag_summary'])) + '</code></p><pre>' +
                       escape('\n'.join(key + ': ' + metadata[key] for key in identities)) + '</pre></details>')
            build_rows.append([escape(row['task_id']), *[escape(metadata[key]) for key in ('compiler_family', 'compiler_version',
                'standard', 'optimization', 'standard_library', 'architecture', 'link_mode', 'abi', 'strip_mode')], details])
        body += ('<section><h2>Original compiler and build conditions</h2><p>These values come from verified saved original-build '
                'actions and their pinned compiler images, rather than a probe of today’s compiler. Candidate compilation '
                'has its own grading identity. Private presence/convention macros, linker symbols and source paths are '
                'excluded. Full commands remain privately bound; only the safe flag summary is shown. Build metadata '
                'does not establish a new model measurement.</p>' +
                ('<p class="notice">No verified original-build metadata is available in this saved snapshot.</p>'
                 if not build_rows else '') + table(
            ['Public task', 'Compiler', 'Version', 'Standard', 'Optimization', 'Library', 'Architecture', 'Link', 'ABI', 'Strip', 'Reproducibility'],
            build_rows) + '</section>')
        body += ('<section><h2>Observation registration and grading compatibility</h2><p>Runtime/budget certification remains '
                'a separate column in the saved-observation table. Later offline regrading may preserve registered inference '
                'while changing its scoring contract; that revision is not presented as the originally registered grade.</p>' + table(
            ['Attempt', 'Registration', 'Execution binding', 'Registration state'], [
                [escape(row['attempt_id'][:12]), escape((row['registration_id'] or 'unknown')[:12]),
                 escape((row['binding_id'] or 'unknown')[:12]), escape(row['status'])]
                for row in value['observations']]) + '</section>')
        return body

    def has_pointer_cell_conditions(self):
        return any(row.get('profile_id') in ('pointer_topology_recover', 'pointer_topology_recover_counterfactual')
                   for row in self.data.get('capability_measurements', {}).get('cells', []))

    def matched_baseline_tasks(self):
        """Use the saved inventory, so old snapshots keep their historical scope."""
        profiles = {compiler + '_' + language + '_' + condition
                    for compiler, languages in (('clang20', ('c', 'cpp')), ('rust', ('c', 'rust')),
                                                ('go', ('c', 'go')))
                    for language in languages for condition in ('recover', 'codegen')}
        return len({row['task_id'] for row in self.data.get('capability_measurements', {}).get('cells', [])
                    if row['profile_id'] in profiles})

    def audit_plan_section(self):
        grading = self.data.get('grading_runtime', {})
        grading_ready = grading.get('controls_passed') and grading.get('controls_current')
        executor = self.data.get('c_executor', {})
        executor_ready = executor.get('controls_passed') and executor.get('controls_current')
        type_scope = self.data.get('capability_preparation', {}).get('supported_capabilities', [])
        optimized = any(row['id'] == 'optimized_locations' for row in
                        self.data.get('capability_preparation', {}).get('controls', []))
        native = any(row['id'] == 'native_abi' for row in
                     self.data.get('capability_preparation', {}).get('controls', []))
        optimized_status = ('L6 has real O3/LTO diagnostic originals, byte-reviewed realizations, validated hint-free packages and closed instance grading; coordinator enrollment and independent review remain pending.'
                            if optimized else 'L6 preparation is not recorded in this snapshot.')
        if 'L6' in type_scope:
            optimized_status = 'L6 O3/LTO diagnostic conditions use the normal frozen-package, closed grading, coordinator and cache paths. Independent completeness review, broader baselines and measurement release remain pending.'
        native_status = ('T7 has pinned Rust-native and Go ABIInternal originals, native/emulated reference comparisons and closed bidirectional description probes. Aggregate grading requires both directions; accidental partial credit cannot pass a task. This development preparation is outside the configured inventory; coordinator enrollment, independent validity and measurement release remain pending.'
                         if native else 'T7 preparation is not recorded in this snapshot.')
        if 'T7' in type_scope:
            native_status = 'T7 Rust-native and Go ABIInternal conditions use the normal coordinator with supplied semantics/layouts, frozen originals and separate native-abi.json artifacts. Aggregate grading requires both directions; accidental partial credit cannot pass a task. Inference remains disabled; independent validity and measurement release remain pending.'
        variant_status = ('T4 adds active union alternatives, subobject and member offsets with known scalar roles.'
                          if 'T4' in type_scope else 'T4 preparation is not recorded in this snapshot.')
        indirect_status = ('T6 adds receiver, callback/context, virtual-slot and compiler-thunk adjustment probes under supplied layouts and signatures. T7 and release gates remain pending.'
                           if 'T6' in type_scope else 'T6 preparation is not recorded in this snapshot. T6/T7 and release gates remain pending.')
        if 'T7' in type_scope:
            indirect_status = indirect_status.replace('T7 and release gates remain pending.', 'Measurement release remains pending.')
        matched = self.matched_baseline_tasks()
        c_status = ('C17/C++20 and restricted Rust/Go buffer conditions configured; 0/6 released'
                    if matched else 'C17/C++20 buffer and lifecycle conditions configured; 0/6 released')
        diagnostic_enabled = self.data.get('capability_preparation', {}).get('inference_enabled', False)
        baseline_status = (str(matched) + ' matched compiler/language task definitions use the same coordinator; '
                           + ('diagnostic inference is enabled; claims remain unreleased. '
                              if diagnostic_enabled else 'inference remains disabled. ')
                           if matched else 'Broader language/compiler baseline enrollment remains pending. ')
        body = '<section id="plan-status"><h2>Implementation plan: incomplete</h2><p>Runtime/provider attestation for new inference is implemented. The full audit repair and capability expansion plan is not finished. Existing results measure the recorded language-reconstruction tasks; they do not establish the nineteen planned localization, C reconstruction and type/ABI sub-capabilities.</p>'
        prepared = self.data.get('capability_preparation', {})
        gates = list(prepared.get('controls', []))
        for section in ('capability_registration', 'capability_measurements'):
            gate = self.data.get(section, {}).get('validation')
            if gate:
                gates.append(gate)
        ready = sum(bool(gate.get('passed') and gate.get('current')) for gate in gates)
        premise_delivery = any(gate.get('id') in ('coordinator', 'coordinator_incremental') and gate.get('passed')
                               and gate.get('current') and gate.get('checks', 0) >= 71
                               for gate in gates)
        body += ('<p><strong>Saved engineering status:</strong> '
                 + str(prepared.get('frozen_tasks', 0)) + ' / '
                 + str(prepared.get('task_definitions', 0)) + ' frozen task definitions; '
                 + 'source bindings current: ' + str(bool(prepared.get('frozen_inputs_current')))
                 + '; ' + str(ready) + ' / ' + str(len(gates))
                 + ' saved offline validation gates passed with current source bindings. '
                 + 'These controls are authored tests, not model measurements or independent validity sign-off. '
                 + 'All nineteen capabilities remain unreleased.</p>')
        body += table(['Work item', 'Implementation status', 'Remaining acceptance gate'], [
            ['R1: exact return values', 'Implemented for existing tracks', 'Same-process frames remain unauthenticated; see R6.'],
            ['R2: Go imports', 'Implemented for single-file submissions', 'Future multi-file artifact admission remains planned.'],
            ['R3: request budgets', 'Route-specific limits; separately reported', 'Shared DSH/ZCode guards validate supported serialized provider caps. Authorized Codex OAuth uses observed task limits without a provider response cap; it is a distinct comparison condition. Historical unidentified budgets remain unknown.'],
            ['R4: runtime/provider', 'Implemented for new inference and grades' if grading_ready else 'Inference implemented; grading controls missing or stale', 'Pinned compiler, linker, headers, libraries, Python and executor images; see the saved grading controls below. Historical execution identities remain unknown.'],
            ['R5: usage accounting', 'Core implementation complete', 'Missing historical usage and actual invoice amounts remain unknown.'],
            ['R6: authenticated returns', 'Restricted C prototype validated' if executor_ready else 'Prototype validation missing, stale or failed', 'Host-owned C17/x86-64 SysV records; see the controls below. General C++/Rust/Go native frames remain unauthenticated.'],
            ['Action cache', 'Implemented', 'Declared stage dependencies and unrelated-change invariance have offline controls.'],
            ['Public task premises', 'Delivery accepted by current coordinator controls' if premise_delivery else 'Delivery repair requires current acceptance', 'Both adapters send prompt.txt and forbid file reads. Required public manifest facts must be included in that hashed prompt. Frozen public packages, private truth and historical results remain separate and unchanged.'],
            ['L1–L6: localization', 'Preparation in progress: 0/6 released', 'L1–L5 have strict artifacts, closed Python grading and byte-bound semantic instruction reviews. The codec error-initialization anchor and omitted XOR are repaired; required operations and optional legitimate context are separate. ' + optimized_status + ' Independent review of alternative roots/realizations and the full measurement release remain pending.'],
            ['C1–C6: independent C condition', c_status, 'Matched recovery and full-semantics generation conditions share original binaries, invocation contracts and oracles. The callback/session interfaces use explicit operations, original callback traces and interleaved instances; opaque state layouts may differ. The same coordinator handles preparation, named source artifacts, versioned grading and cached reuse. ' + baseline_status + 'Independent task-validity review, broader interfaces and measurement release remain pending.'],
            ['T1–T7: type/layout/ABI', self.type_preparation_label() + '; 0/7 released', 'Separate type/layout and call-boundary artifacts drive closed probes. Both directions retain a frozen original endpoint. Integer-record probes cover SysV and ms_abi in ELF. Paired T3 layouts exercise pointers, access, length/capacity and overlap. ' + variant_status + ' ' + indirect_status + ' ' + native_status],
            ['P7: full release', 'Workflow, registration and typed reporting implemented; release incomplete', 'The normal run prepares selected pending cases. New capability dispatch requires a frozen schedule and complete runtime/provider/build/scoring binding. The global view retains typed observations, denominators, lineage macros and grading/accounting revisions. Independent task validity, model ablations and the remaining capability/publication gates are unfinished; no capability measurements are released.'],
        ])
        body += '<p>P1 is complete for the existing tracks; P0 is partial; P2 ' + ('is complete for supported new paths' if grading_ready else 'has pending grading-runtime controls') + '; P3 ' + ('has passed its restricted C prototype gate' if executor_ready else 'has pending executor controls') + '; P4–P5 are in progress; P6 ' + ('has diagnostic preparation in progress' if optimized else 'is planned') + '; P7 is partial. The earlier C-output pilot and compiler/string-ABI build variants do not satisfy the new independent C and type-recovery conditions. ' + ('Capability inference is enabled for explicitly selected diagnostics; all claims remain unreleased. ' if diagnostic_enabled else 'New capability tasks stay disabled. ') + 'Historical results and their limitations remain preserved.</p></section>'
        return body

    def runtime_section(self):
        rows = self.all_records()
        states = Counter(row.get('runtime_attestation_status', 'historical_unknown') for row in rows)
        audit = self.data.get('runtime_attestation', {})
        body = '<section id="runtime-attestation"><h2>Runtime and provider attestation</h2><p>New launches resolve the provider, credential mode, protocol, model and HTTPS endpoint before hashing. Content manifests bind REA and dependencies, Ghidra and custom scripts, Java, Node, the selected harness, adapters, native shared libraries, TLS trust and sealed runtime settings. Fresh checks before and after execution refuse runtime or route drift. Credential values and credential-file hashes are excluded.</p>'
        body += table(['Recorded execution evidence', 'Attempts / cells'], [[escape(key), value] for key, value in sorted(states.items())])
        body += '<p><strong>Historical runtime/provider identity remains unknown where it was not recorded.</strong> Reused results retain their correctness, readability, tokens and cost; current installations cannot certify past execution. Reviewed source-upgrade compatibility applies only to those original observations. No model experiment was rerun for attestation.</p>'
        body += '<h3>Saved offline installation inventory</h3><p>Status: ' + escape(audit.get('status', 'not_recorded')) + '; attestor/configuration bytes current: ' + str(bool(audit.get('inputs_current'))) + '. This inventory describes the installation when locally inspected; report rendering does not inspect installed tools or read account state.</p>'
        body += '<p>Offline attestation controls: ' + str(audit.get('checks', 0)) + '; passed: ' + str(bool(audit.get('controls_passed'))) + '; test inputs current: ' + str(bool(audit.get('controls_current'))) + '; model API calls: 0.</p>'
        components = [[escape(harness), escape(name), '<code>' + escape(item['sha256'][:16]) + '</code>', item['files'], f'{item["bytes"]:,}']
                      for harness, value in sorted(audit.get('runtimes', {}).items()) for name, item in sorted(value['components'].items())]
        body += table(['Harness', 'Logical runtime component', 'Content digest prefix', 'Files', 'Bytes'], components)
        body += '<p>These are local content receipts, not remote signatures or model-weight attestations. They do not prevent a privileged host from modifying a running process between checks. Historical request budgets and runtime identities remain uncertified; native returns remain unauthenticated. Public pages publish only bounded status and digest summaries.</p></section>'
        return body

    def grading_runtime_section(self):
        audit = self.data.get('grading_runtime', {})
        graded = [row['grade'] for row in self.all_records() if row.get('grade')]
        closed = sum(bool(grade.get('grading_runtime', {}).get('id')) or grade.get('grading_runtime_status') == 'verified_closed_image' for grade in graded)
        body = '<section id="grading-runtime"><h2>Closed grading runtimes</h2><p>Candidate compilation and execution use content-addressed images containing the selected compiler, linker, headers, libraries, ELF loader, Python interpreter and bubblewrap executor. The image is fully hashed on extraction and checked for drift before and after execution. Worker sources are copied from declared action inputs; host bytecode caches are excluded. CPU capabilities and kernel enter grading identity. An explicit refresh selects host toolchain upgrades; unrelated host software and report edits do not change a pinned image.</p>'
        body += '<p>Saved offline controls: ' + str(audit.get('checks', 0)) + '; passed: ' + str(bool(audit.get('controls_passed'))) + '; inputs current: ' + str(bool(audit.get('controls_current'))) + '; model API calls: 0. ' + str(closed) + '/' + str(len(graded)) + ' selected grades contain evidence of closed-image execution. Missing evidence is not backfilled from the current installation.</p>'
        body += table(['Candidate language', 'Compiler', 'Image digest prefix', 'Declared files', 'Bytes'], [
            [escape(language), escape(profile['compiler_version']), '<code>' + escape(profile['id'][:16]) + '</code>', profile['files'], f'{profile["bytes"]:,}']
            for language, profile in sorted(audit.get('profiles', {}).items())])
        return body + '<p>Versioned offline regrading preserves original model outputs and grades. This closes the toolchain dependency boundary for new grades; it does not authenticate native return frames or certify historical inference environments.</p></section>'

    def c_executor_section(self):
        audit = self.data.get('c_executor', {})
        ready = audit.get('controls_passed') and audit.get('controls_current')
        body = '<section id="c-executor"><h2>Host-owned restricted C returns</h2>' + charts.executor_workflow()
        body += '<p>Status: ' + ('validated prototype' if ready else 'validation missing, stale or failed') + '; offline controls: ' + str(audit.get('checks', 0)) + '; failed: ' + str(audit.get('failed', 0)) + '; source inputs current: ' + str(bool(audit.get('controls_current'))) + '; model API calls: 0.</p>'
        body += '<p>The host invokes a static freestanding C17 function under a fixed x86-64 virtual CPU. Guest code receives only the current invocation and explicit prior state, with no host syscalls, original target or hidden expected results. Exact-byte memory permissions, stack and preserved-register checks, instruction/time limits and host-recorded callbacks are enforced. Output records are collected after a valid call boundary. Printing a native grading marker cannot supply a return record.</p>'
        body += table(['Engine', 'Sealed module digest prefix', 'Base compiler/Python image', 'Module files', 'Module bytes'], [[
            escape(audit.get('engine_version') or 'Not recorded'), escape(audit.get('engine_id', '')[:16] or '—'),
            escape(audit.get('base_image', '')[:16] or '—'), audit.get('module_files', 0), f'{audit.get("module_bytes", 0):,}']])
        return body + '<p>This is an executor control result, not a new model measurement or completion of C1–C6. Existing native C++/Rust/Go scores and return-provenance labels are retained. General native runtimes and dynamic ELF loading are outside the prototype; the emulator, loader and host kernel remain trusted.</p></section>'

    def type_preparation_label(self):
        supported = self.data.get('capability_preparation', {}).get('supported_capabilities', [])
        names = [name for name in supported if name.startswith('T')]
        return '/'.join(names) + ' preparation' if names else 'Type/ABI preparation not recorded'

    def capability_preparation_section(self):
        value = self.data.get('capability_preparation', capability_summary.empty())
        body = '<section id="capability-preparation"><h2>Capability conditions: offline preparation</h2>'
        body += '<p>' + str(value['families']) + ' reusable C++ families; ' + str(value['task_definitions']) + ' task definitions; ' + str(value['frozen_tasks']) + ' public packages recorded as frozen. These task definitions cover ' + escape(', '.join(value['supported_capabilities'])) + ' preparation; they are distinct from the nineteen sub-capabilities in the full plan. Model inference enabled: ' + str(value['inference_enabled']) + '. Frozen source inputs current: ' + str(value['frozen_inputs_current']) + '.</p>'
        body += '<p><strong>How offline checks work:</strong> authored correct artifacts must pass, observationally equivalent artifacts must also pass, and deliberately incorrect claims must fail. Trusted originals and generated probes execute as real compiled programs under the pinned native/emulated controls. Separate reference implementations check defined observations; cached traces and dummy loopback providers exercise harness delivery and spending limits. This verifies the behaviors exercised by those controls. It does not measure model performance, establish realistic difficulty, exhaust possible answers or replace independent task-validity review and premises-only model ablations.</p>'
        body += table(['Offline gate', 'Passed', 'Source inputs current', 'Controls', 'Failed'], [[
            escape(row['id']), str(row['passed']), str(row['current']), row['checks'], row['failed']]
            for row in value['controls']])
        body += charts.preparation_workflow()
        body += '<p>The configured run prepares selected new/stale capability cases under the existing batch lock, once per case across models. A separate Merkle preparation action caches the exact public package, private grading specification and checked index record. Missing files can be restored without original compilation or candidate evaluation. Reports, model routes, prices and run budgets do not enter preparation identity; inference and grading retain their own keys.</p>'
        body += '<p>Read-only planning includes every selected pending/invalid cell and never prepares credentials, builds or models. Invalid immutable bytes are not overwritten. Preparation failure/cancellation preserves the scheduled denominator, records zero provider launches and releases the lock. Stop requests remain effective through preparation; a previously ready target or run configuration cannot change before provider work.</p>'
        body += '<p>The existing coordinator accepts named artifact blocks and keeps append-only typed grades. Localization JSON is graded in a pinned Python image. C17 and original-language C++20 candidates receive only one invocation at a time under the same host-owned executor. Their original ELF bytes, entry, vectors and oracle agree; the parent compares status, length, exact output bytes and preserved memory. Independent full-semantics controls in both languages test code generation without requiring binary analysis.</p>'
        body += '<p>The paired C++ profile uses a pinned GCC compiler and freestanding, nonallocating C++20. Header-only algorithms, arrays, spans, lambdas and local record methods are exercised by readable controls. General C++ runtime services, heap allocation, exceptions and TLS remain outside this profile. The candidate-source byte budget excludes the trusted forced declaration in both languages.</p>'
        body += charts.cpp_pairing_workflow()
        body += '<p><strong>Explicit callback and persistent-state preparation:</strong> the C5/C6 path has two stripped original interfaces in the existing callback/session families. C17 and original-language C++20 have separate recovery and full-semantics generation packages, sharing the same binary and all 106 original calls. Source-bound controls check callback arguments/count, failure rollback, output preservation, destroy/recreate, reset and interleaved instances. Candidate-owned opaque state and guest globals persist within a scenario and start fresh between scenarios. Whole-task correctness and C5/C6 component vectors are separate. Preparation, admission and grading use the existing configured coordinator; no separate experiment runner is added. Independent validity review and release remain pending. Controls and cached machine executions add no model observations, tokens or cost.</p>'
        body += '<p>Preparation validates the frozen private grading specification before provider preparation or workspace creation. Missing data, a changed digest or a conflicting artifact/score contract fail before a model attempt. Valid private-only revisions preserve inference identity.</p>'
        body += '<p>Completion review: the typed task separates source and output language; generic adapter receipts follow the output contract, with contradictory cached labels rejected. Capability score selection is a grading action input. Localization questions identify the complete scalar operation and define each semantic role and dispatch condition. Static instruction review found a codec check anchor on error-return initialization and an omitted salt XOR. Exact-binary, byte-checked required operations replace those anchors. Legitimate local context is accepted but cannot substitute for required operations; partial instruction ranges and old anchor-only truth cannot receive a full score. This is a coordinating-agent review, with independent review of alternative roots/realizations still required.</p>'
        body += '<p>Type/ABI conditions separate scalar recovery with layout supplied, layout recovery with scalar types supplied, and ABI recovery with the complete type supplied. Submitted descriptions generate probes linked with the same frozen original caller/callee object. Return-slot guards, nested arrays, scalar boundaries, register/stack transfers and hidden return pointers are checked under the host-owned executor. Only defined values and observable effects count; equivalent padding/alignment descriptions are accepted. These are instrumented Linux ELF conditions, not Windows/MSVC compatibility or Rust-native/Go-internal ABI scores.</p>'
        body += '<p>The three integer-record forms share one interface/probe lineage. Twelve counterfactual conditions vary observable scalar width/sign or physical layout across the same three families and two conventions. Paired prompts, tool catalogs and supplied nonmeasured facts agree. Offline controls test correct descriptions, equivalent ordering and fixed baseline/counterfactual guesses rebound only to the other binary identity. The selected header and declared source alias enter the original compiler action; unrelated variants and gold-control facts do not.</p>'
        body += '<p>These fixed-description controls do not estimate LLM no-binary performance or exhaust all equivalent descriptions. A separate premises-only model ablation, broader scalar kinds and independent task-validity review remain release gates. Compiler/emulator agreement validates the mechanism; it does not establish that a task requires reverse engineering.</p>'
        body += charts.abi_workflow()
        body += charts.abi_counterfactual_workflow()
        pointer_cells = self.has_pointer_cell_conditions()
        if pointer_cells:
            body += '<p><strong>T3 pointer/object preparation:</strong> four configured conditions vary physical layouts and pointer paths under identical supplied signatures, scalar roles, prompts and tools. They share one interface lineage. Submitted descriptions drive offsets, pointer-cell loads, access, extent and overlap checks in both original-endpoint directions. The host compares buffers, status, produced length, preserved descriptors, separate pointer cells and guards. Fourteen nonce vectors per original cover short/full/empty buffers, null/capacity errors and overlap. Native, emulated and Python references calibrate the originals; gold descriptions and wrong-depth/cell-mutation controls check the mechanism. The source-bound gate table records current acceptance. These conditions do not recover erased ownership or exact unused allocation size, and do not establish arbitrary graph or runtime support. Independent validity, model ablations and release remain pending.</p>'
        else:
            body += '<p><strong>T3 pointer/object preparation:</strong> two paired physical layouts share the same supplied signature, scalar roles, prompts and tools under one interface lineage. The submitted pointer-object description drives physical offsets, pointer dereferences, read/write access, scalar extent bindings and overlap checks. The host compares memory, status, produced length and preserved descriptors/guards in both original-endpoint directions. Fourteen nonce vectors per target cover short/full/empty buffers, null/capacity errors and overlapping regions. Native calibration, emulator execution and a separate Python oracle agree on the original reference. Exact unused allocation size and erased ownership are outside this score; depth-two hypotheses are checked but these originals use depth one.</p>'
        body += charts.pointer_workflow(pointer_cells)
        if 'T4' in value['supported_capabilities']:
            body += '<p><strong>T4 tagged variants and subobjects:</strong> paired layouts change discriminants, base offsets, union placement and pair-member order while sharing the same public premises. A submitted description drives tag tests and concrete memory accesses against unchanged original caller/callee objects. Native execution, emulation and a separate Python reference compare defined active values and protected memory, including unknown-tag errors. Correct and equivalent descriptions must pass; wrong tag mappings, subobject/payload offsets and cross-layout guesses must fail. The source-bound gate below records acceptance status. Nominal inheritance, exact unused allocation size and erased source names are outside this condition; independent validity review and model ablations remain open.</p>'
            body += charts.variant_object_workflow()
        if 'T6' in value['supported_capabilities']:
            body += '<p><strong>T6 indirect and object-call preparation:</strong> the complete supplied root layout and call signatures isolate receiver/function/context associations, virtual-method slots and signed receiver adjustments. Paired C++ originals use real nonprimary bases, compiler-generated adjustment thunks and function-pointer callbacks. A submitted calls.json drives actual vtable calls and, in a complementary probe, calls to the supplied original implementation with the claimed receiver adjustment. A generated root also calls the unchanged original callee. The host compares both object states, both callback-context states, counters, return bits and preserved memory. Native execution, emulation and a separate Python reference calibrate the originals. A wrong slot or adjustment may pass the bridge while failing the replacement; every check must pass for whole-task success. These are bounded GCC/Linux conditions, with no nominal class recovery, virtual inheritance, Rust-native or Go-internal ABI claim. The source-bound gate below records acceptance status; independent task validity and premises-only model ablations remain open.</p>'
            body += charts.indirect_call_workflow()
        if any(row['id'] == 'optimized_locations' for row in value['controls']):
            body += '<p><strong>L6 optimized-location diagnostic preparation:</strong> two real GCC O3 originals compare separate translation units with cross-unit LTO. They contain distinct inline bodies, actual specialization clones, hot/cold partitions and receiver-adjusting thunks; LTO removes a verified cross-unit boundary. Both builds agree with a separate Python reference on 216 native calibration calls, including near-misses and the cold branch. Same-build DWARF 4/5 supplies physical source-instance provenance; a separate exact-instruction review checks arithmetic, branch destinations and explicit receiver premises against final bytes. A bounded instance artifact and one-to-one closed grader accept equivalent ranges and reject missing paths, wrong adjustments, near-misses and overclaims. Verified receiver wrappers and the LTO inline adjustment path are accepted alternatives. Hint-free packages and grading are checked against the original build; host-side scoring patches cannot replace the sealed worker. The categories describe private compiler provenance; solvers are not asked to infer unobservable source history. Its offline gate records preparation status. Independent completeness review, task validity and measurement release remain pending.</p>'
            body += ('<p>The L6 conditions are configured in the normal task inventory; inference remains disabled.</p>'
                     if 'L6' in value['supported_capabilities'] else '<p>L6 enrollment is pending.</p>')
            body += charts.optimized_location_workflow('L6' in value['supported_capabilities'])
        if any(row['id'] == 'native_abi' for row in value['controls']):
            body += '<p><strong>T7 native-language ABI diagnostic preparation:</strong> pinned Rust 1.91.1 and Go 1.23.2 Linux AMD64 originals use ordinary Rust function boundaries and Go ABIInternal. Known 24-byte input/output layouts and complete scalar semantics isolate the physical transfer problem. A distinct Rust C FFI control does not count as native-ABI success. Sixty native calibration observations, 24 emulated original observations and 48 bidirectional gold checks compare defined fields with a separate Python reference. A submitted description generates a replacement called by the unchanged original caller, and a bridge calling the unchanged original callee. The host owns result collection and enforces code, memory, return-slot and runtime-context guards. Both directions must pass for whole-task success; a wrong nonce register that coincides at zero retains partial credit only. Malformed or wrong-binary artifacts launch no probes. Aggregate grades, compiled descriptions and individual observations have separate cache actions; changing the private vector selection reuses matching observations. This bounded preparation does not certify heap/GC behavior, captured closures, unwinding, other compiler versions or historical native results. Independent validity and model ablations remain pending.</p>'
            body += ('<p>The T7 conditions are configured in the normal task inventory; inference remains disabled.</p>'
                     if 'T7' in value['supported_capabilities'] else '<p>T7 enrollment is pending.</p>')
            body += charts.native_abi_workflow('T7' in value['supported_capabilities'])
        controls = {row['id']: row for row in value['controls']}
        if 'coordinator_incremental' in controls:
            body += ('<p><strong>Incremental coordinator acceptance:</strong> the original coordinator row retains '
                     'the historical 104-check baseline. The separate coordinator_incremental row counts current '
                     'focused checks for an explicitly reviewed, digest-bound change set. It does not mean all '
                     '104 checks were rerun or that their current action keys matched. Registration, analysis-context '
                     'and task-family admission remain separate.</p>')
        matched = self.matched_baseline_tasks()
        if 'matched_baselines' in controls:
            body += '<h3>Compiler and language baseline preparation</h3>'
            body += '<p>Eighteen additional stripped original points cover the same six families with Clang 20 at O2, Rust optimization level 2 and Go compiler-default optimization. Their controls compare exact status, length, output and preserved memory with the existing GCC originals and independent Python semantics. '
            body += (str(matched) + ' paired task definitions are configured in the existing coordinator: C17 and the original language each have independent recovery and full-semantics generation conditions. Their current selection, observations, token usage and cost are shown in the capability inventory and history.</p>'
                     if matched else 'These development points are outside the configured model inventory; they add no model cells, samples, tokens or cost.</p>')
            if controls['matched_baselines']['passed'] and controls['matched_baselines']['current']:
                body += '<p>The current matrix receipt verifies all 1,026 original/GCC/Python observations, C17 controls across all eighteen points and C++20 controls against the six Clang originals. A warm pass forbids compiler, original and candidate workers. Original-compiler conditions and candidate-language conditions remain distinct.</p>'
            else:
                body += '<p>Matched-matrix acceptance is pending or no longer current. Its preparation must not be treated as a completed comparison.</p>'
            for name, language, boundary in (
                    ('rust_buffers', 'Rust', 'Rust 2021 no_std, static linking and a C buffer boundary'),
                    ('go_buffers', 'Go', 'Go ABIInternal, supplied R14 context and argument spill slots')):
                row = controls.get(name, {})
                status = ('current offline acceptance passed' if row.get('passed') and row.get('current')
                          else 'offline acceptance pending or no longer current')
                body += '<p><strong>' + language + ' candidate preparation:</strong> ' + status + '. The restricted profile uses ' + boundary + '. The host owns returns and exact memory observations. Heap/GC use, runtime startup, syscalls and general native runtime support are outside these profiles. Successful source-bound controls cover 342 original-language gold observations, wrong semantics, memory effects, modified oracles and warm cache reuse; the table records whether that acceptance is available.</p>'
            body += charts.matched_baseline_workflow(bool(matched))
            remaining = ('Independent validity review, model ablations and release acceptance remain pending.' if matched else
                         'These preparations still require configured paired task enrollment, independent validity review, model ablations and release acceptance.')
            body += '<p>Compiler, one-call execution and aggregate grading use separate action identities. Private vectors and oracle changes preserve compilation; execution-source changes invalidate affected observations and grades. ' + remaining + '</p>'
        remaining = ('Independent validity review, broader type/ABI interfaces and the full release gates remain unfinished.' if matched else
                     'Matched baseline enrollment, broader type/ABI interfaces and the full release gates remain unfinished.')
        return body + '<p>Preparation controls are not model samples, token measurements or cost observations. No existing model score gains a capability score. ' + remaining + '</p></section>'

    def compilers_page(self):
        value = self.data['cpp_build_points']
        config = value['config']
        body = '<section><h2>Compiler and optimization are separate testing points</h2><p>A C++ build point records compiler family/version, language standard, optimization, standard library, string ABI, strip mode and extra flags. Case sources and independent behavior oracles stay the same. Each different build has a separate content fingerprint, binary and address hints. Existing case IDs and their recorded results are retained.</p><p>The added matrix uses <strong>GCC 13.3 and Clang 20.1.2</strong>, each at <strong>-O0, -O2 and -O3</strong>, with libstdc++ 13 held fixed. The original basic C++ case remains GCC -O1 with inlining disabled; it is not relabeled as -O2. Target compilation and reconstructed-source grading are separate: current C++ submissions use the existing common GCC grader.</p></section>'
        lookup = {(p['suite'], p['base_case'], p['compiler_id'], p['optimization']): p for p in value['points']}
        columns = [(c, level) for c in config['compilers'] for level in config['optimization_levels']]
        rows = []
        for family in config['families']:
            cells = []
            for compiler, level in columns:
                point = lookup.get((family['suite'], family['base_case'], compiler['id'], level))
                if not point:
                    cells.append('<span class="heat-cell heat-pending">Build planned<small>No model measurement</small></span>')
                    continue
                valid = point['inputs_current'] and point['status'] in ['locally_verified', 'reused_frozen_build']
                condition = self.data['conditions']['active_case_conditions'].get(family['suite'])
                measured = [m for m in point['measurements'] if m['condition'] == condition and m['passed'] is not None]
                label = 'Build unavailable / stale' if not valid else 'Frozen build reused' if point['status'] == 'reused_frozen_build' else 'Build verified'
                cells.append('<span class="heat-cell ' + ('heat-pass' if valid else 'heat-pending') + '">' + escape(label) + '<small>' + str(len(measured)) + '/' + str(len(self.data['models'])) + ' fixed model grades' + '</small></span>')
            rows.append([escape(family['suite'] + ' / ' + family['base_case']) + '<small>' + escape(family['standard']) + ' · ABI ' + str(family['string_abi']) + ' · strip ' + escape(family['strip_mode']) + '</small>', *cells])
        body += '<section><h2>Build coverage and model-measurement coverage</h2><p>Build verification executes only the trusted authoring fixtures against the existing oracle. It is not a model experiment or correctness score. Green cells mean verified binaries; the model-grade count is displayed separately. Unsupported or unmeasured points never receive a zero model score. If two builds produce identical target bytes and task text, a matching recorded-condition measurement can be shared; it is not an independent repeat or an additional charge. Every shared measurement keeps its original directory in the JSON export.</p>' + table(['Existing C++ fixture', *[c['family'] + ' / -' + level for c, level in columns]], rows) + '</section>'
        model_count = len(self.data['models'])
        body += '<section><h2>Pilot before the expanded matrix</h2><p>The existing six-handler C++ multipoint case is the pilot across two compilers and three optimization levels: six build points and ' + str(6 * model_count) + ' model cells across ' + str(model_count) + ' registered models. Its existing GCC -O2 measurements can be reused when input and environment identities match. The five additional build points would require ' + str(5 * model_count) + ' fresh model attempts; build validation alone does not launch them. The full six-fixture grid has ' + str(value['planned_build_points']) + ' build points, with the original basic -O1 case retained separately. Expansion beyond the pilot remains distinct from the current frozen rerun.</p><p>Later language-feature availability must be probed with the selected frontend and library. Clang 20 does not by itself establish C++23/26 library-container coverage. The existing C++11/14 fixtures retain their original source grammar.</p></section>'
        probes = value['language_feature_probes']['probes']
        if probes:
            features = sorted({p['feature'] for p in probes})
            feature_rows = []
            for feature in features:
                cells = []
                source = next(p['source'] for p in probes if p['feature'] == feature)
                export = 'code/feature-probes/' + Path(source).name
                self.write(export, self.references[source])
                for compiler in config['compilers']:
                    probe = next((p for p in probes if p['feature'] == feature and p['compiler_id'] == compiler['id']), None)
                    passed = probe and probe.get('inputs_current') and probe['status'] == 'compiled_and_local_control_passed'
                    cells.append('Compile + local control passed' if passed else 'Unsupported by recorded toolchain' if probe and probe.get('inputs_current') else 'Unverified')
                feature_rows.append(['<a href="' + escape(export) + '">' + escape(feature) + '</a>', *cells])
            body += '<section><h2>Newer language-feature availability</h2><p>These small source probes compile under strict language modes and execute an explicit local control. They establish availability for the recorded frontend/library combination, with <strong>zero model attempts</strong>. They are not decompilation benchmark scores. Library containers and compiler grammar have separate support requirements.</p>' + table(['Language feature', *[c['family'] for c in config['compilers']]], feature_rows) + '</section>'
        body += '<section><h2>Compiler diagnostic policy</h2><p>The original container source explicitly captures a constant named offset. Clang warns that the capture is unnecessary. Additional Clang container builds record <code>-Wno-error=unused-lambda-capture</code>, keeping that warning visible while preserving the source. Other strict warnings remain enabled. This is a build diagnostic exception; it changes no model prompt, grader result or original GCC target.</p></section>'
        measured_rows = [r for r in self.current_records() if r['case']['language'] == 'cpp']
        body += '<section><h2>Current C++ model results</h2><p>Compiler and optimization appear on each case page. Current frozen measurements are GCC builds; no Clang score is inferred from GCC results. Pending work and partial usage remain explicit.</p>' + self.case_table(measured_rows) + '</section>'
        body += '<section><h2>Reproduce build validation and reports</h2><p><code>make build-cpp-points</code> validates the compiler/optimization grid using cached builds where hashes match; it never calls a model. <code>make reports</code> reads the saved build receipts and recreates this page offline. <a href="data/cpp-build-points.json">Build identities and validation records</a>.</p></section>'
        self.page('compilers.html', 'Compilers and optimization levels', body)

    def coverage_page(self):
        rows = []
        stl = [r for r in self.current_records() if r['experiment'] == 'stl-rust-data-v2-20261019' and r['case']['language'] == 'cpp']
        for item in self.coverage['rows']:
            scores = []
            for model in self.data['models']:
                selected = [r for r in stl if r['model'] == model and r['grade']]
                groups = [r['grade'].get('points', {}).get(str(item['point'])) for r in selected] if item['status'] != 'unsupported_by_installed_toolchain' else []
                groups = [x for x in groups if x]
                if groups:
                    passed, total = sum(x['passed'] for x in groups), sum(x['total'] for x in groups)
                    css = 'heat-pass' if passed == total else 'heat-partial'
                    scores.append('<span class="heat-cell ' + css + '"><strong>' + f'{100 * passed / total:.1f}%' + '</strong><small>' + f'{passed}/{total}' + '</small></span>')
                else:
                    scores.append('<span class="heat-cell heat-pending">' + ('Excluded: unavailable' if item['status'] == 'unsupported_by_installed_toolchain' else 'Not graded') + '</span>')
            rows.append([escape(item['name']), escape(item['standard']), escape(item['operations']), *scores])
        body = '<section><h2>STL container coverage and exact checks</h2><p>All 16 C++11 container templates/adaptors are exercised, plus vector&lt;bool&gt;, basic_string, bitset and valarray. Counts aggregate the four dialect/string-ABI cases. Unsupported C++23/26 containers are excluded, never emulated.</p>' + table(
            ['Container / related type', 'Standard', 'Selected operations', *[m['label'] for m in self.data['models'].values()]], rows) + '</section>'
        body += '<section><h2>Grammar points</h2><p>C++11: variadic templates, scoped enums, captures, unique_ptr and moves, deleted copy, virtual override/final, constexpr/static_assert, noexcept, exceptions and observable cleanup order.</p><p>C++14: generic lambdas, move-only init capture, make_unique, deduced returns, decltype(auto), constexpr loops, binary literals, digit separators and transparent comparison. Its dedicated handler is an ERR:STANDARD control in C++11 and is excluded from C++11 active-point totals.</p></section>'
        body += '<section><h2>Languages, optimization and ABI</h2><p>The multipoint C++/Rust/Go cases cover text counts, ledgers, stable ranking, dependency scheduling, wide argument/aggregate return handling and policy dispatch. Targets are optimized with normal symbols stripped. C++ includes System V / Microsoft x64 boundaries and both libstdc++ string ABIs; Rust includes native/C; Go includes ABIInternal. Native reconstructions are behavior-tested, not certified as binary-compatible replacement libraries.</p><p>Coverage means selected behavior, not every API overload or specialization. Repeated handlers across ABIs and Rust tool profiles are correlated.</p></section>'
        body += '<section><h2>Planned work</h2><p>Expression-AST and cache/stream families remain unbuilt. The earlier 44-cell expansion plan is a plan, not an executed denominator. Six later-standard container fixtures remain unsupported by the installed toolchain. No experiment was launched to fill these planned cells during migration.</p></section>'
        self.page('coverage.html', 'Grammar, containers and ABI', body)

    def history_pages(self):
        rows = []
        for experiment in self.data['experiments']:
            selected = [r for r in self.data['records'] if r['experiment'] == experiment['id']]
            rows.append(['<a href="' + escape(experiment['id']) + '/index.html">' + escape(experiment['label']) + '</a>',
                         escape(experiment['role']), len(selected), sum(bool(r['grade']) for r in selected)])
            body = '<section><h2>Recorded protocol</h2><p>' + escape(experiment['role']) + ' experiment. Original manifests, model answers and grading receipts remain unchanged.</p>' + self.summary_table(selected) + '</section><section>' + self.case_table(selected, '../../') + '</section>'
            self.page('history/' + experiment['id'] + '/index.html', experiment['label'], body)
            self.write('history/' + experiment['id'] + '/comparison.json', json_text({'experiment': experiment, 'records': selected}))
            lines = ['# ' + experiment['label'], '', 'Generated from saved records with make reports.', '',
                     '| Model | Case | Status | Correct / checks | Known tokens |', '|---|---|---|---:|---:|']
            for row in selected:
                grade, receipt = row['grade'], row['receipt'] or {}
                lines.append('| ' + row['model_label'] + ' | ' + row['case']['label'] + ' | ' + STATUSES[row['status']] + ' | ' +
                             (f'{grade["passed"]}/{grade["total"]}' if grade else 'Not graded') + ' | ' +
                             str(receipt.get('reported_usage', {}).get('totalTokens', 'Unreported')) + ' |')
            self.write('history/' + experiment['id'] + '/report.md', '\n'.join(lines) + '\n')
        body = '<section><h2>All recorded experiments</h2>' + table(['Experiment', 'Role', 'Cells', 'Graded'], rows) + '</section>'
        body += '<section><h2>Historical distinctions</h2><p>The earlier six C-output reconstructions remain an output-language baseline. Two earlier C++ output-budget attempts remain transport diagnostics. Their usage and expenditure appear in the ledger but are excluded from matched native totals.</p><p><a href="stl-rust-data-v2-20261019/diagnosis.html">Cached DeepSeek failure investigation</a></p></section>'
        self.page('history/index.html', 'Benchmark history', body + self.condition_comparison())
        diagnosis = self.data['diagnosis']
        body = '<section><h2>Saved investigation</h2><p>This assessment used cached model outputs and evidence. It does not launch inference, repair candidates or change grading.</p><p class="notice">A later <a href="../../diagnostics.html#environment">cross-harness audit</a> found DSH tool-result spills with blocked recovery. Read this earlier semantic diagnosis alongside that environment evidence; it does not certify an environment-independent model ranking.</p><pre>' + escape(json_text(diagnosis)) + '</pre></section>'
        self.page('history/stl-rust-data-v2-20261019/diagnosis.html', 'Cached failure investigation', body)

    def methodology(self):
        body = '<section><h2>One reproducible report pipeline</h2><p><code>make reports</code> builds all global, case, history, JSON, CSV, source and ZIP artifacts from saved run records, frozen pricing, stored reviews and templates. It requires no API credentials, model requests, target execution or compiler runs. Generated files are not inputs.</p><p><code>make check</code> verifies preserved artifacts, source paths, offline cached-evidence replay, real-client loopback transports, local links and two clean report builds with identical file hashes. <code>make check-environment</code> runs only the evidence replay and transport checks. HTML timestamps come from recorded data; ZIP timestamps and ordering are deterministic.</p></section>'
        body += '<section><h2>One configured execution pipeline</h2><p><code>make benchmark</code> uses <code>benchmarks/config/benchmark.json</code> and the model, experiment and condition registries. All enabled models and active primary cases are selected by default. Budgets, language, ABI and target inputs remain frozen with each registered case. Compiler/optimization expansion remains separately build-verified and unmeasured by models until registered for inference.</p><p>Inference identity covers model/reasoning, binary, prompt, tool catalog, target manifest, limits and effective adapter code. Scheduling, display labels, reporting prices and report changes do not invalidate inference. Wallet enforcement rates and thresholds are frozen inference inputs. Every cache hit verifies its committed artifacts and original source profile. The reviewed legacy migration binds only exact recorded task hashes and preserves their original environment labels. Grade/source keys and the global report build ID are separate. Interrupted attempts and incompatible caches fail explicitly without automatic retry.</p><p><code>make check-runner</code> repeats the configured suite with provider dispatch blocked and tests a simulated missing cache. Historical per-suite runners and cohort-specific audit/review scripts are archived with original hashes. <a href="data/runner-validation.json">Acceptance receipt</a> · <a href="data/entrypoint-archive.json">Archive manifest</a>.</p></section>'
        body += '<section><h2>From binary to benchmark report</h2>' + charts.workflow() + '</section>'
        body += '<section><h2>Isolation and first attempts</h2><p>Task prompts, ELF hashes, tool catalogs and budgets are frozen by protocol. ZCode uses an isolated temporary workspace and data directory, a restricted REA gateway and a recorder that verifies model, tools and wire limits. Native shell, filesystem, web, workflow and agent tools are removed; skills, memory and plugins are disabled. API headers are excluded from saved model traffic.</p><p>Each saved first attempt remains immutable. Interrupted attempts do not restart automatically. Grading runs after inference without repair or model feedback. Candidate execution uses the existing isolated bwrap grader.</p></section>'
        body += '<section><h2>Correctness and readability</h2><p>Results use their recorded grading protocol. C++11/14 and the Rust data-tool cohort use the numbered byte-frame amendment; original line-based scores remain diagnostic. Compile errors, uncaught exceptions, missing returns and exact byte mismatches count as failures.</p><p>Readability is a saved coordinating-AI source review: formatting, names, decomposition, native idioms and explanatory comments, each scored 0–2. It is subjective and separate from correctness. Earlier reviews masked model labels; additional GLM reviews disclose that model identity was visible. Fixed-condition reviews use source files without model labels or grades in the review pack, but the coordinating reviewer has run context, so they are not an independent blind panel. They are not an independent human panel. Missing reviews remain pending; report generation does not ask a model to invent a score.</p></section>'
        body += '<section><h2>Model and harness interpretation</h2><p>GLM runs in ZCode, while DeepSeek/GPT run in DeepSeek Harness. System prompts, provider adapters, tokenizers, cache behavior and reasoning implementations differ. The <a href="diagnostics.html#environment">environment audit</a> also found model-visible DSH spill previews with recovery tools blocked by the benchmark allowlist. Behavioral grading remains reproducible for the submitted code, but model-only attribution is not certified. Equal tasks or a shared-case filter do not remove these confounds. Scores are never silently adjusted to guess the effect of missing evidence.</p></section>'
        body += '<section><h2>Current evidence delivery condition</h2><p>New DeepSeek/GLM launches use lean-evidence-v4: compact case-local references, pointer-only catalogs and exact cross-tool deduplication of fully delivered views. Original evidence remains addressable. Their wallet policy prices cached input separately and replaces the raw total-token stop; response, output, turn and time guards remain. Historical scores retain their recorded conditions.</p><p>The reused basic runs use <code>lossless-evidence-v1</code>; the eight harder cases use <code>paged-evidence-v2</code> for every model. Both disable DSH spill, prune and automatic compaction and apply the benchmark-only ZCode overlay. v2 adds common, read-only evidence paging with exact reassembly checks and a 24,000-character response bound. Task fingerprints include profile and adapter hashes. Old cache hits remain their recorded condition; replaying cached evidence does not change their scores or create a new model attempt. <a href="diagnostics.html#evidence-fixes">Implemented controls, exact replay counts and remaining limits</a>.</p><p>The saved validation receipt is checked against code, configuration and cached-input hashes during every report build. Missing or changed inputs make validation unverified. Client code and the network are never loaded by report generation.</p></section>'
        body += '<section><h2>Migration and provenance</h2><p>Historical run directories stay in place. A registry maps imported GLM attempts to existing experiment cases. Case sources, binaries and vectors are retained byte-for-byte; moved executable paths are recorded by the migration audit.</p><p><a href="data/migration.json">Migration record</a> · <a href="data/report-inputs.json">Complete report input hashes</a></p></section>'
        self.page('methodology.html', 'Methodology and reproducibility', body)

    def environment_diagnostics(self):
        audit = self.data['environment_audit']
        cases = audit['cases']
        dsh = [c for c in cases if c['model'] != 'glm-5.3']
        spills = sum(c.get('counts', {}).get('spilled', 0) for c in dsh)
        body = '<section id="environment"><h2>Environment fairness: not certified</h2><p><strong>The saved numbers measure model + agent setups, not isolated model capability.</strong> The recorded DSH and ZCode profiles both altered oversized tool results, using different policies. DSH’s recommended recovery tools were blocked by the benchmark allowlist. Original code grades remain valid exact behavioral observations, but their counterfactual scores with complete, equally delivered evidence are unknown. No cached score is adjusted or regraded by this audit.</p><p>Read-only auditing found ' + str(spills) + ' confirmed spill previews in ' + str(sum(c.get('counts', {}).get('spilled', 0) > 0 for c in dsh)) + '/' + str(len(dsh)) + ' DSH attempts. The previous GLM-only investigation did not establish DSH environment parity. This audit corrects that gap.</p><p><a href="data/environment-audit.json">Full historical audit receipt and per-tool hashes</a>. <code>make audit-environment</code> regenerates the DSH receipt from existing durable sessions offline; <code>make reports</code> validates the saved receipt against gateway evidence and rebuilds every page without requiring the installed clients or invoking models.</p></section>'
        body += self.evidence_fixes()
        body += '<section><h2>DSH has several different limits</h2>' + table(['Layer', 'Recorded/default limit', 'Effect on scoring'], [
            ['Headless JSON display', '8,192 UTF-8 bytes per string; 32,768 bytes per event. The final answer is exempt.', 'Shortens events.jsonl previews. This is not itself a model-input limit. Durable sessions retain the longer model-input messages.'],
            ['Model-facing spill policy', '12,500 estimated inline tokens, including its notice; oversized text becomes retained head + tail + a saved-file pointer.', str(spills) + ' changed model-input results were confirmed against raw gateway evidence. Read/grep file recovery is absent from the allowed tool catalog; narrower REA queries remain possible.'],
            ['Context-pressure tool pruner', 'Above 8,192 Unicode code points, keeps 4,096 at the head and 1,024 at the tail plus a marker, when compaction qualifies.', str(sum(c.get('compaction_events', 0) for c in dsh)) + ' observed compaction/pruning events in the audited DSH sessions. This potential limit did not cause the recorded spills.'],
            ['DSH MCP text projection', 'Uses MCP text content once; does not append the same structuredContent copy.', 'Different from ZCode’s duplicated text/structured projection. Both still carry any repetitions already inside REA Evidence.'],
            ['ZCode MCP projection', '50,000 UTF-8 bytes per model result, after combining text and structured content; keeps the head.', 'Different evidence loss and context overhead from DSH. Existing GLM diagnostics below quantify those effects.'],
        ]) + '<p>Observed DSH spill previews are often about 49,900 text characters in these ASCII-heavy fixtures; that is not a general 50,000-byte cap. Token estimates, Unicode characters and UTF-8 bytes are different units.</p></section>'
        summary_rows = []
        for model, info in self.data['models'].items():
            selected = [c for c in cases if c['model'] == model]
            summary_rows.append([escape(info['label']), len(selected),
                sum(c.get('counts', {}).get('spilled', 0) for c in selected),
                sum(c.get('blocked_spill_recovery', 0) for c in selected),
                sum(c.get('primary_cuts', 0) for c in selected),
                sum(c['status'] == 'unverified' for c in selected), 'Not certified'])
        body += '<section><h2>Audit coverage across existing setups</h2>' + table(
            ['Model / setup', 'Cached cases', 'Spilled results', 'Blocked spill notices', 'ZCode first-copy clips', 'Unverified cases', 'Pure model attribution'], summary_rows) + '<p>Spill and clip counts are observations of each model’s chosen queries, not equal semantic workloads or a ranking. DSH inputs were checked at the durable request surface and source conversion code; full historical HTTP payloads were not captured. ZCode was checked against cached HTTP request bodies. These evidence layers should not be described as equally strong wire verification.</p></section>'
        checks = audit['matched_input_checks']
        flag = lambda value: 'Yes' if value is True else 'No' if value is False else 'Unknown'
        body += '<section><h2>Matched-input checks</h2>' + table(
            ['Existing protocol / case', 'Same ELF across setups', 'Same task prompt across setups', 'DSH pair: same system text', 'DSH pair: same tool schemas'], [
                [escape(c['experiment'] + ' / ' + c['case']), flag(c['target_match']), flag(c['task_prompt_match']),
                 flag(c['dsh_system_match']), flag(c['dsh_tool_schemas_match'])] for c in checks]) + '<p>Unknown means a preserved interrupted receipt lacks the required field. Matching task hashes and tool schemas does not certify equal result projection, provider reasoning behavior, system prompts across different clients, or access to omitted data. No unknown field is counted as a successful parity check.</p></section>'
        rows = []
        for c in dsh:
            counts = c.get('counts', {})
            href = 'cases/' + c['experiment'] + '/' + c['case'] + '.html#' + c['model']
            rows.append(['<a href="' + escape(href) + '">' + escape(c['experiment'] + ' / ' + c['case']) + '</a>',
                         escape(self.data['models'][c['model']]['label']), counts.get('primary_complete', 0),
                         counts.get('spilled', 0), c.get('blocked_spill_recovery', 0),
                         c.get('log_shortened_results', 'Unknown'), c.get('compaction_events', 'Unknown'), escape(c['status'])])
        body += '<section><h2>DSH case evidence</h2>' + table(
            ['Protocol / case', 'Model', 'Full primary results', 'Spilled', 'Recovery blocked', 'Short log previews', 'Compaction events', 'Delivery status'], rows) + '<p class="chart-note">Each saved successful tool result was compared with its corresponding raw gateway text. Tool errors are tracked separately. Receipt inputs are hashed and revalidated at report build; a missing or changed input downgrades the audit to unverified. Long log previews are not counted as missing model evidence.</p></section>'
        body += '<section><h2>Controls required before a model-only benchmark</h2><ol><li>Use one agent harness and one bounded, shared system/task prompt for the model comparison. Keep a separate model × harness comparison when testing ZCode itself, using the existing cases and explicitly named conditions.</li><li>Keep one canonical Evidence payload with IDs and provenance. The implemented delivery profile preserves full results. If a paging policy becomes necessary for context limits, every client must have the same read-only paging tools and exact reassembly checks.</li><li>Keep spill, prune and automatic summarization disabled, as implemented in the current benchmark profile. Provider context-window overflow must fail explicitly; do not shorten evidence behind the model’s back.</li><li>Keep offline large-result replay passing through both adapters and record actual outgoing model inputs. The validation above covers cached bodies and loopback transports; it cannot establish live provider acceptance of arbitrary context sizes.</li><li>Record matching binaries, task/system text, schemas, call and wall limits, and effective provider options. Missing payload records or failed recovery must prevent a model-only certification.</li><li>After those checks, run an explicitly authorized small pilot on existing cases before a full suite. Preserve the current limited-environment scores and caches as their original condition; do not relabel them as corrected results.</li></ol><p><strong>The delivery fixes are implemented for future benchmark launches, with a separate environment fingerprint.</strong> Shared prompts and provider behavior remain distinct. Fixed-harness execution and completion status are shown in the global Overview; original audit findings remain unchanged.</p></section>'
        return body

    def evidence_fixes(self):
        if self.data.get('condition_records'):
            return self.paging_fixes()
        value = self.data['evidence_validation']
        state = 'Passed offline replay' if value.get('passed') else 'Unverified: replay missing, stale or failed'
        body = '<section id="evidence-fixes"><h2>Implemented evidence delivery fixes</h2><p><strong>' + escape(state) + '.</strong> Profile <code>lossless-evidence-v1</code> applies only to benchmark launches. It changes adapters and future cache fingerprints, while retaining the existing suites and historical receipts.</p>'
        body += table(['Component', 'Current benchmark behavior'], [
            ['DSH', 'spill-policy and tool-result-pruner disabled; compaction-basic.auto=false. Native filesystem, shell and source access remain blocked. Short JSON log previews are display-only and can still occur.'],
            ['ZCode / REA', 'A hash-checked in-memory overlay removes the 50,000-byte REA model-result cap. It removes structuredContent only when a text block parses to exactly the same value; primary text bytes, unique fields, errors and block order survive. Other MCP servers retain upstream behavior. The installed bundle is unchanged.'],
            ['Context pressure', 'Complete evidence is sent inline. Provider context limits still apply; overflow fails explicitly. No silent spill/prune/summarization fallback or new paging tool is introduced.'],
            ['Output ceiling and failures', 'ZCode may lower max_tokens to leave room for full input. The recorder accepts positive values at or below the frozen ceiling and records each actual cap. Setup, policy and provider failures stop before correctness grading; they cannot become zero model scores.'],
            ['Cache and scores', 'Future task identities include profile and adapter hashes. Legacy cache hits stay labeled legacy-recorded and are not treated as corrected-condition measurements. No historical code, grade, readability review, usage or cost receipt is rewritten.'],
        ])
        if value.get('passed'):
            dsh, zcode = value['dsh'], value['zcode']
            body += table(['Offline check', 'Result'], [
                ['Complete cached MCP responses replayed', str(zcode['results'])],
                ['DSH primary text projection', str(dsh['successful_results']) + ' exact matches; ' + str(dsh['error_results']) + ' errors preserved as errors'],
                ['ZCode baseline → corrected clips in replay', str(zcode['baseline_clipped']) + ' → ' + str(zcode['corrected_clipped'])],
                ['Proven redundant structured copies removed', str(zcode['proven_duplicate_copies_removed'])],
                ['Largest full primary payload', f'{zcode["max_primary_bytes"]:,} UTF-8 bytes'],
                ['Boundary and semantic checks', str(zcode['synthetic_cases']) + ' cases including Unicode, unique structured fields, errors and mixed blocks'],
                ['Real DSH / ZCode outgoing transport', str(value['dsh_wire_smoke']['tool_results']) + ' / ' + str(value['zcode_wire_smoke']['tool_results']) + ' cached large/data results matched exactly at loopback HTTP input'],
                ['ZCode output allowance observed in replay', ', '.join(str(n) for n in value['zcode_wire_smoke']['output_token_caps']) + ' tokens per request; configured ceiling ' + str(value['zcode_wire_smoke']['configured_output_ceiling'])],
                ['New provider tokens and cost', '0 model API calls; 0 billable tokens; $0 new API cost. Local fixture usage is excluded from benchmark totals.'],
            ]) + '<p>Baseline replay clip counts apply the old serializer to all saved responses; they differ from the historical wire-observed message counts below. Bulk replay uses installed DSH MCP code and the actual ZCode entry/serializer, with application boot disabled. Separate smoke tests launch both real clients against a local recorder and a cached-response MCP server; no Ghidra target or provider inference is started.</p>'
        else:
            body += '<p class="notice">' + escape(value.get('reason', 'Offline validation has not passed')) + '. No successful current validation is claimed.</p>'
        return body + '<p><code>make check-environment</code> reruns the offline cache replay and client transport checks. <code>make check</code> also checks immutable artifacts and two identical report builds. <code>make reports</code> only validates and consumes the saved <a href="data/evidence-validation.json">replay receipt and input hashes</a>; it does not run clients or tests. Passing replay repairs future delivery behavior; it does not establish improved model scores, latency or model-only parity.</p></section>'

    def paging_fixes(self):
        value = self.data['paging_validation']
        state = 'Passed offline replay' if value.get('passed') else 'Validation pending or stale'
        body = '<section id="evidence-fixes"><h2>Implemented evidence delivery fixes</h2><p><strong>' + state + '.</strong> Completed basic cases retain their verified inline v1 runs. The eight larger cases use paged-evidence-v2 for every model. This fixed-harness comparison retains the same 11 native cases.</p>'
        body += table(['Control', 'Effective behavior'], [
            ['DSH', 'Native spill-policy and tool-result-pruner disabled; automatic compaction disabled. Short console previews remain display-only.'],
            ['ZCode', 'Hash-checked benchmark overlay removes redundant structured copies and the native 50,000-byte REA cap. The installed CLI bundle is unchanged.'],
            ['Common explicit paging', 'The gateway retains each original result verbatim and references it by SHA-256. Large results include bounded model text, a JSON-pointer field catalog and a pseudocode view. The same read_evidence tool retrieves complete fields or contiguous Unicode pages; it cannot read source or arbitrary files.'],
            ['Response bound and scope', 'Model-facing projected results and retrieval pages are at most 24,000 Unicode characters. Exact full-text reassembly is tested. Each page consumes one call from the unchanged case budget. A complete large dossier can exceed what fits in that budget; narrower function queries and field views remain available. References are limited to evidence produced in that case.'],
            ['Context and scoring', 'Actual model context limits still apply. Setup/provider failures receive no correctness grade. The three v1 hard-case diagnostics and their costs are retained; completed basic attempts are reused.'],
        ])
        if value.get('passed'):
            body += table(['Offline validation', 'Result'], [
                ['Cached original responses', value['results']], ['Paged originals reassembled exactly', value['exact_reassemblies']],
                ['Largest original', f'{value["largest_original_bytes"]:,} UTF-8 bytes'],
                ['Largest projected result / retrieved page', str(value['max_projection_characters']) + ' / ' + str(value['max_page_characters']) + ' Unicode characters'],
                ['Real DSH / ZCode transport checks', '3 projected/retrieved responses each, with exact outgoing-message hashes'],
                ['Unicode, unique fields, errors and reference isolation', str(value['synthetic_cases']) + ' synthetic cases passed'],
                ['Model API calls for validation', '0; local fixtures are excluded from benchmark tokens and cost'],
            ])
        else:
            body += '<p class="notice">' + escape(value.get('reason', 'Current paging validation has not passed')) + '</p>'
        archives = self.data['environment_profile_archives']['profiles']
        if archives:
            body += '<h3>Exact source profiles retained</h3><p>Each saved task retains its original environment fingerprint. These archives contain the exact benchmark harness code and configuration bytes used by those profiles; historical profiles are not relabeled as current code.</p>' + table(['Profile', 'Harness', 'Saved tasks', 'Archived bytes'], [
                ['<a href="' + ('data/environment-profiles/' + escape(fingerprint) + '/profile.json' if 'data/environment-profiles/' + fingerprint + '/profile.json' in self.profile_files else 'data/environment-profile-archives.json') + '">' + escape(item['profile']['id']) + ' · ' + fingerprint[:12] + '</a>',
                 escape(item['profile']['harness']), len(item['attempt_tasks']),
                 ('Incomplete: ' + str(len(item['missing_files_sha256'])) + ' historical source files unavailable')
                 if item.get('missing_files_sha256') else ('Verified' if item['files_current'] else 'Unverified')]
                for fingerprint, item in sorted(archives.items())])
        inline = self.data['evidence_validation']
        inline_status = 'also passes against its recorded code and input hashes' if inline.get('passed') and inline.get('inputs_current') else 'is retained but does not certify the current code'
        return body + '<p><code>make check-paging</code> replays cached originals and validates both real clients against local fixtures. <code>make reports</code> consumes the saved <a href="data/evidence-paging-validation.json">current paging receipt</a> and validates its hashes without running a client, compiler or model. The separate <a href="data/evidence-validation.json">inline replay receipt</a> ' + inline_status + '. Complete data availability does not certify a pure model comparison or a particular accuracy gain.</p></section>'

    def runtime_storage_section(self):
        body = ('<section id="local-performance"><h2>Local startup and temporary runtime storage</h2>'
            '<p>The runtime launcher detects WSL workspaces on Windows-backed mounts. It extracts a pinned archive of public installed tools to Linux temporary storage, '
            'then runs the coordinator in a private mount namespace at the original workspace paths. Random temporary paths do not enter action identities. '
            'Results and the action cache stay in the workspace; credentials and raw traces are never packed.</p>'
            + table(['Reviewed bottleneck', 'Implemented change'], [
                ['Full 153-case inventory for a two-case selection', 'Run preparation validates selected cases only; full inventory remains an explicit preparation command.'],
                ['Repeated original admission and shared gates', 'One original-binding validation per inspection; shared kind/harness gates are admitted once per registration.'],
                ['Small-file runtime I/O on Windows mounts', 'Temporary native-Linux copies of REA, Ghidra, Node, DSH and ZCode, exposed at stable paths.'],
                ['Nested MCP setup reported executable EACCES', 'A private standard /dev permits /dev/null streams; real-client loopback controls run inside the same temporary namespace.'],
                ['Unused analysis startup in premises-only sessions', 'DSH omits and disables the REA connector when the allowed tool catalog is empty.'],
                ['Temporary-file lifetime', 'Context-manager cleanup on success, errors and signals; lock-protected stale cleanup after a forced kill.']])
            + '<p>Offline controls cover real bind mounts, attestation/action-key invariance across different temporary paths, mutation rejection and cleanup. '
            'The first run packs installed runtime dependencies; later runs reuse the verified archive. Refresh the archive explicitly after updating installed tools. '
            'Launcher source and storage configuration are runtime-attestation inputs; unrelated scripts and random temporary names remain outside those inputs. '
            'These are environment performance repairs, not new model observations or corrected historical scores. '
            'Model reasoning, output budgets and provider latency remain separate limitations.</p>')
        body += ('<p>Capability attempts use a stable logical trial in the inference action and an action-addressed result directory. '
            'A matching legacy attempt retains its original path and key. Changed runtime/provider inputs select a separate immutable attempt; '
            'the same stopped action remains blocked. Reports retain all actions and require explicit action selection when several share one configured cell; '
            'rendering never probes a provider to choose a current result.</p>')
        performance = self.data.get('runtime_performance', {})
        benchmark = performance.get('benchmark')
        if benchmark:
            state = {'offline_cache_miss': 'Offline: missing inference caches; provider launches refused',
                     'pre_dispatch_failure': 'Stopped before model dispatch', 'stopped': 'Interrupted; attempts preserved',
                     'incomplete': 'Incomplete; saved failures and attempts retained',
                     'running': 'Running; timing snapshot is partial', 'complete': 'Completed'}[benchmark['status']]
            body += '<h3>Latest benchmark command: measured stage timings</h3><p><strong>' + escape(state) + '</strong>. '
            body += 'Mode: ' + escape(benchmark['mode']) + '; recorded coordinator time: <strong>' + f'{benchmark["total_seconds"]:.3f} seconds</strong>. '
            body += f'Selected tasks: {benchmark["selected_tasks"]}; cache hits: {benchmark["cache_hits"]}; '
            body += f'failures: {benchmark["failures"]}; unprocessed cells: {benchmark["unprocessed_tasks"]}. '
            if 'queue_holds' in benchmark:
                body += f'Model queues held after a completed final: {benchmark["queue_holds"]}. '
            body += 'Harness launch markers: ' + str(benchmark['provider_launches']) + '; API requests: '
            body += ('unknown' if benchmark['model_api_calls'] is None else str(benchmark['model_api_calls'])) + '.</p>'
            body += '<p>Selection, preparation and binding measure startup; execute includes cache checks, model waiting and grading. '
            body += 'An offline cache miss checks refusal behavior and produces no new model result. A failed or interrupted command does not demonstrate model correctness. '
            body += 'These timings start at task selection and exclude launcher extraction and Python imports. '
            body += 'Harness launch markers do not establish the number of HTTP requests. Finalization measures report generation when enabled.</p>'
            body += table(['Benchmark stage', 'Status', 'Seconds'], [[escape(row['stage']), escape(row['status']),
                'in progress' if row['elapsed_seconds'] is None else f'{row["elapsed_seconds"]:.3f}']
                for row in benchmark['stages']])
        warm = performance.get('warm')
        body += ('<h3>Warm verification and cold regression are separate</h3><p><code>make check</code> or <code>make check-warm</code> '
            'checks accepted cache receipts, recomputes prepared action keys, verifies frozen packages and saved artifacts, '
            'and freshly seals dependency bytes. It performs no builds, grading, runtime inventory, or model calls. '
            'Missing, changed or corrupt inputs fail instead of silently starting cold work. '
            '<code>make check-cold</code> explicitly runs the full build/fixture/corruption/transport regression suite; '
            'it records each command duration separately. Fresh runtime/provider attestation still belongs to an actual model run.</p>')
        if warm:
            body += '<p>Saved warm measurement: <strong>' + f'{warm["total_seconds"]:.3f} seconds</strong> '
            body += '(' + escape(warm['measured_at']) + '). Dependency proof: <strong>' + ('current' if warm['current'] else 'stale; rerun warm verification') + '</strong>. '
            body += f'{warm["prepared_cases"]} prepared cases, {warm["native_saved_cases"]} saved native cases; '
            body += f'{warm["reusable_native_cases"]} previously accepted reusable native cases and {warm["retained_changed_input_cases"]} retained known input mismatches. '
            body += f'Budget: {warm["budget_seconds"]} seconds. Provider launches and model calls: 0.</p>'
            body += table(['Warm stage', 'Seconds'], [[escape(row['name']), f'{row["seconds"]:.3f}'] for row in warm['stages']])
            maximum = max((row['seconds'] for row in warm['stages']), default=1) or 1
            body += '<div aria-label="Measured warm verification stage durations">' + ''.join(
                '<div class="chart-row"><div class="chart-label">' + escape(row['name']) + '</div>'
                '<div class="stack-track"><div class="bar" style="width:' + f'{100 * row["seconds"] / maximum:.2f}'
                + '%;background:var(--accent,#4f78df);height:16px"></div></div></div>' for row in warm['stages']) + '</div>'
        else:
            body += '<p>No warm timing measurement is recorded in this summary.</p>'
        cold = performance.get('cold')
        if cold:
            body += '<h3>Explicit cold regression command timings</h3><p>' + ('Complete' if cold['complete'] else 'Partial') + ' command run, including the setup stage.</p>'
            body += table(['Cold command', 'Seconds', 'Exit code'], [[escape(row['name']), f'{row["seconds"]:.3f}', row['exit_code']] for row in cold['stages']])
        body += '<p>Broad regression tests construct and grade fixtures and exercise cold caches. This is a different workload from warm verification; '
        body += 'the times do not establish a model or Ghidra execution speedup. New action-cache entries remain in the persistent workspace cache while temporary runtimes and scratch files are removed.</p></section>'
        return body

    def diagnostics(self):
        diagnosis = self.data['glm_diagnosis']
        cases = diagnosis['cases']
        timed = [c for c in cases if c['timed_out']]
        clips = [entry for c in cases for entry in c['truncations']]
        primary_cuts = sum(entry.get('primary_cut') is True for entry in clips)
        matches = sum(c['reasoning_history_matches'] for c in cases)
        differences = sum(c['reasoning_history_differences'] for c in cases)
        body = self.environment_diagnostics() + self.runtime_storage_section()
        fixed_glm = [r for r in self.current_records() if r['model'] == 'glm-5.3' and r['grade']]
        if fixed_glm:
            fixed_timeouts = [r for r in fixed_glm if r['receipt'].get('timed_out')]
            audited_timeouts = sum(r.get('environment_delivery') == 'primary_complete' for r in fixed_timeouts)
            exhausted = sum(r['receipt'].get('tool_calls', 0) > r['case']['max_tool_calls'] for r in fixed_glm)
            timing_rows = []
            for row in fixed_glm:
                receipt, timing = row['receipt'], row.get('timing_evidence') or {}
                elapsed = charts.elapsed_seconds(receipt)
                tools = timing.get('tool_seconds_sum')
                caps = [q['max_tokens'] for q in receipt.get('requests', []) if type(q.get('max_tokens')) is int]
                cap_label = str(min(caps)) + ('–' + str(max(caps)) if min(caps) != max(caps) else '') if caps else 'Unreported'
                timing_rows.append([
                    '<a href="' + escape(self.case_link(row)) + '#glm-5.3">' + escape(row['case']['label']) + '</a><small>' + escape(row['experiment_label'] + ' · output ' + str(row.get('output_language') or row['case']['language'])) + '</small>',
                    f'{row["grade"]["passed"]}/{row["grade"]["total"]}',
                    'Timed out' if receipt.get('timed_out') else 'Finished',
                    f'{elapsed / 60:.2f} min' if elapsed is not None else 'Unreported',
                    f'{timing["completed_request_seconds"] / 60:.2f} min' if timing else 'Unreported',
                    timing.get('requests_without_end_time', 'Unreported'),
                    f'{tools:.2f} sec' if tools is not None else 'Unreported',
                    str(receipt.get('tool_calls', 'Unreported')) + ' / ' + str(row['case']['max_tool_calls']),
                    cap_label,
                    f'{receipt.get("reported_usage", {}).get("outputTokens", 0):,}',
                    escape(self.environment_label(row)),
                ])
            body += '<section><h2>Fixed-condition GLM timing</h2><p>' + str(len(fixed_timeouts)) + ' of ' + str(len(fixed_glm)) + ' completed fixed-condition attempts reached the deadline; ' + str(sum(not r['source'] for r in fixed_timeouts)) + ' of those have no accepted native source. Exact outgoing tool-message delivery is verified for ' + str(audited_timeouts) + '/' + str(len(fixed_timeouts)) + ' timed-out attempts. ' + str(exhausted) + ' completed attempts also issued calls beyond the common tool budget. These saved outcomes show that deadline failures remain under the fixed delivery controls; they do not isolate model compute, prompt behavior or provider latency.</p><p>API windows include network, provider queuing and streaming; summed tool durations can overlap and are not subtracted to infer model compute. Missing request end times and final usage remain unknown. Reported output tokens can include reasoning. Attempted tool-call counts include rejected calls; calls beyond the frozen limit receive an explicit budget-exhausted error.</p>' + table(
                ['Case', 'Exact checks', 'Deadline outcome', 'Elapsed', 'Completed API windows', 'Requests without end time', 'Summed tool service', 'Tool calls / limit', 'Actual request output caps', 'Known output tokens', 'Evidence delivery'], timing_rows) + '</section>'
        body += '<section><h2>Assessment from the original GLM traces</h2><p>The original high-reasoning run policy spent most hard-case time in API streams, with no complete source submitted in the ' + str(len(timed)) + ' timed-out attempts. The recorded ZCode path duplicated REA evidence and clipped some results. A completion-oriented system prompt and a hard process cutoff provided no enforced final-answer phase. These mechanisms interact; original traces alone cannot measure the causal effect of a delivery or reasoning-policy change. Fresh fixed-condition results and their coverage are compared on the <a href="index.html#conditions">global Overview</a>.</p><p>This historical analysis uses all ' + str(len(cases)) + ' original cached GLM attempts mapped to the existing suites, including earlier user-stopped attempts. It sends no model requests and leaves original prompts, answers, grades and usage receipts unchanged. Current adapter controls and their offline checks are listed separately above.</p><p><a href="data/glm-diagnosis.json">Download original-trace measurements and evidence hashes</a> · <a href="data/report-inputs.json">All source input hashes</a></p></section>'
        body += '<section><h2>Reasoning versus answer text</h2><figure class="chart"><figcaption>Saved stream characters · reasoning and visible response text</figcaption>'
        for c in cases:
            total = c['reasoning_chars'] + c['content_chars']
            body += '<div class="chart-row"><div class="chart-label"><strong>' + escape(c['experiment_label'] + ' / ' + c['label']) + '</strong><span>' + f'{c["reasoning_chars"]:,} reasoning / {c["content_chars"]:,} visible · ' + ('timed out' if c['timed_out'] else escape(c['status'])) + '</span></div><div class="stack-track">'
            for key, css, label in [('reasoning_chars', 'state-running', 'Reasoning'), ('content_chars', 'state-graded', 'Visible text')]:
                if total and c[key]:
                    body += '<span class="segment ' + css + '" style="width:' + f'{100*c[key]/total:.4f}' + '%" role="img" aria-label="' + escape(label + ': ' + str(c[key])) + '"><span class="tip">' + escape(label + ': ' + str(c[key])) + '</span></span>'
            body += '</div></div>'
        body += '<div class="legend"><span><i class="state-running"></i>Reasoning characters</span><span><i class="state-graded"></i>Visible response characters</span></div></figure><p class="chart-note">Character counts describe saved text, not billable tokens or throughput. Visible text includes progress notes and code; interrupted streams are partial. Raw reasoning text is not published. API time includes network and streaming, and cannot isolate model compute. Per-chunk timestamps were not recorded, so time to first reasoning or first answer cannot be reconstructed reliably.</p></section>'
        context = [{'model': 'glm-5.3', 'model_label': c['experiment_label'] + ' / ' + c['label'],
                    'bytes': c['last_request_bytes'], 'first': c['first_request_bytes']} for c in cases]
        body += '<section><h2>Growing context and duplicated evidence</h2>' + charts.metric(
            'Serialized request size · first versus last saved request', context, 'bytes',
            lambda c: f'{c["first"] / 1000:.1f} KB first → {c["bytes"] / 1000:.1f} KB last',
            max((c['bytes'] for c in context), default=1)) + '<p>The last-request histories contain ' + f'{sum(c["verified_duplicate_count"] for c in cases):,}' + ' verified duplicate tool payloads: both the text JSON and appended structured JSON parse to the same value. Their duplicate tails alone account for at least ' + f'{sum(c["verified_duplicate_chars"] for c in cases):,}' + ' characters across those final histories. This is an observed lower bound, not a tokenizer estimate or a simulated speedup. Compaction was disabled for the recorded runs; context growth alone is not evidence of a context-window overflow.</p></section>'
        body += '<section><h2>Confirmed findings and causal limits</h2>' + table(['Finding', 'Evidence', 'Interpretation'], [
            ['Long reasoning under a permissive request cap', 'Saved requests specify high reasoning and 128,000 maximum output tokens per request; streamed text is predominantly reasoning on the hard cases.', 'The output ceiling is not a task-wide token budget. No timed-out case has a length finish reason in the saved streams; they hit the wall deadline.'],
            ['Duplicate tool results and head truncation', f'{len(clips)} distinct clipped tool messages across {len(cases)} attempts; {primary_cuts} cut the first text copy. The remainder retain that copy and clip later duplication.', 'Installed ZCode formatMcpToolResult appends structuredContent after text, and ordinary MCP tools have a 50,000-byte model-result cap. Larger analyze_function responses are particularly wasteful.'],
            ['Decompiler evidence loss is narrower than the clipping count', 'In C++14 ABI1, one procedure_pseudo_code result cuts its first text copy. Rust’s two clipped pseudocode messages retain complete first copies.', 'Clipping can obscure evidence, but it does not explain every timeout: Rust times out without that particular semantic loss, and some clipped attempts succeed.'],
            ['No enforced final-answer phase', 'The adapter waits for a hard process deadline. Runtime configuration disables compaction; the system prompt encourages continued work until completion. The user prompt states total limits but supplies no live remaining-time feedback.', 'A plausible prompt/harness contribution to late submission. The caches cannot prove that replacing the prompt would fix the task.'],
            ['Ambiguous code/close ordering', str(sum(c['task_code_before_close'] and c['system_final_no_tools_after'] for c in cases)) + ' cached tasks request code followed by closing the binary, while ZCode requires deliverables in a final message with no tool calls afterward.', 'Clarify the contract: close first, then emit exactly one final native block. This is a prompt inconsistency, not proof of the timeout cause; both completed C++11 attempts closed before their final code.'],
            ['Thinking is sent back, preservation setting omitted', f'{matches} completed reasoning sequences match the next saved history byte-for-byte; {differences} differences. First saved requests set thinking.type=enabled but omit clear_thinking.', 'Client-side history loss is not observed. Z.ai documents explicit clear_thinking=false for preserved thinking on its standard API. The actual BigModel route’s server-side treatment is unobservable here; this remains a configuration hypothesis.'],
            ['No evidence of retry storms or provider rejection', f'{sum(c["identical_request_retries"] for c in cases)} identical HTTP request retries; {sum(c["http_errors"] for c in cases)} recorded HTTP errors.', 'Repeated successful tool queries are model choices, not identical HTTP retransmissions. Lack of errors does not exclude provider queuing or network latency.'],
            ['The source collector did not discard a completed timeout answer', 'All three timeout streams contain zero complete native code blocks; saved answer files agree on that absence.', 'The zero correctness scores represent missing submissions, not proof that every inferred function was wrong. Existing compiled mismatch/crash scores remain separate behavioral failures.'],
        ]) + '</section>'
        body += '<section><h2>Proposed fixes remaining — not benchmarked</h2>' + table(['Priority', 'Change', 'Validation before any new paid run'], [
            ['P0: delivery implemented', 'Full REA text and evidence fields now survive; proven duplicate structured data is removed in ZCode. DSH spill/prune and automatic compaction are disabled. The implemented status and cached replay results are above.', 'Keep exact body/hash and real-client transport checks passing. Context limits and cross-harness prompt parity still need separate control.'],
            ['P0', 'Add a bounded analysis phase and reserve time/tokens for exactly one complete native code block. Report remaining time and calls at request boundaries; make finalization explicit before the hard deadline. State one unambiguous order: close the binary, then emit the final code.', 'Check request/call budgets locally, including a long continuously streaming request. Avoid multiple fenced draft submissions, which violate the existing one-block contract. A socket idle timeout is not a total request deadline.'],
            ['P1', 'Explicitly test thinking.clear_thinking=false while preserving the exact reasoning sequence already sent. Verify support and defaults for the configured standard BigModel route.', 'Assert the intended payload with a local fixture first. Provider-side preservation and its latency/cost effects require a separately authorized ablation; they cannot be inferred from these caches.'],
            ['P1', 'Try reasoning_effort=low as a separate latency/quality condition. Then tune a per-request output ceiling with enough room for the final program.', 'GLM-5.3 requires reasoning enabled; do not send thinking.type=disabled. Lowering max_tokens alone can consume the cap in reasoning and starve the answer. Preserve original high-effort scores.'],
            ['P1', 'Record first/last chunk times, first answer time, reasoning/answer counters and usage incrementally, once per request, even when the stream is interrupted.', 'Replay partial SSE locally and verify that available usage is retained without double counting. Missing provider usage must remain unknown rather than treated as zero.'],
        ]) + '<p>Apply validated changes as an explicitly named harness/prompt condition on the existing cases, with cache keys including the revised prompt, tool projection and provider options. Preserve the original receipts and scores. Start with an authorized small pilot and compare correctness, readability, runtime, tokens and cost before any full-suite run; reuse the existing baseline rather than rerunning it.</p><p>This historical diagnostic section does not control execution. See the global Overview for the current fixed-harness batch.</p></section>'
        body += '<section><h2>Case evidence</h2>'
        for c in cases:
            anchor = c['experiment'] + '-' + c['case']
            total = c['reasoning_chars'] + c['content_chars']
            body += '<details id="' + escape(anchor) + '"><summary>' + escape(c['experiment_label'] + ' / ' + c['label']) + ' · ' + ('Timed out' if c['timed_out'] else escape(c['status'])) + '</summary><p><a href="cases/' + escape(c['experiment'] + '/' + c['case']) + '.html#glm-5.3">Open the unchanged source, grade and readability review</a></p>'
            body += table(['Measurement', 'Saved value'], [
                ['Elapsed / summed tools', (f'{c["elapsed_seconds"] / 60:.2f} min' if c['elapsed_seconds'] is not None else 'Unreported') + f' / {c["tool_seconds_sum"] / 60:.2f} min'],
                ['Reasoning share of saved text', f'{100*c["reasoning_chars"]/total:.2f}%' if total else 'No text'],
                ['Tools / errors / successful exact repeats', f'{c["tool_calls"]} / {c["tool_errors"]} / {c["repeated_successful_queries"]}'],
                ['Byte/string inspection calls', c['data_tool_calls']],
                ['Complete native blocks: wire / saved answer', f'{c["native_code_blocks"]} / {c["saved_native_blocks"]}'],
                ['Native blocks preserved by collector', str(c['native_blocks_match_wire'])],
                ['HTTP requests / completed reasoning histories matched', f'{c["request_count"]} / {c["reasoning_history_matches"]}'],
                ['Compaction / completion-pressure system wording', str(c['compaction_enabled']) + ' / ' + str(c['system_completion_pressure'])],
                ['Code-before-close task / no-tools-after-final system', str(c['task_code_before_close']) + ' / ' + str(c['system_final_no_tools_after'])],
            ])
            if c['truncations']:
                body += table(['Tool sequence', 'Tool', 'Original model bytes', 'First text copy cut?'], [
                    [x['sequence'], escape(x['tool']), f'{x["original_model_bytes"]:,}' if x['original_model_bytes'] else 'Unknown', str(x.get('primary_cut'))] for x in c['truncations']])
            longest = sorted(c['streams'], key=lambda s: s['seconds'] if s['seconds'] is not None else s.get('cutoff_window_seconds') or 0, reverse=True)[:5]
            body += '<h3>Longest saved request windows</h3>' + table(['Request', 'Window', 'Reasoning / visible chars', 'End marker / usage event'], [
                [s['sequence'], (f'{s["seconds"]:.1f} sec' if s['seconds'] is not None else f'{s["cutoff_window_seconds"]:.1f} sec to cutoff receipt' if s.get('cutoff_window_seconds') is not None else 'Unreported'),
                 f'{s["reasoning_chars"]:,} / {s["content_chars"]:,}', str(s['done']) + ' / ' + str(bool(s['usage_events']))] for s in longest]) + '</details>'
        body += '<p class="chart-note">Cutoff windows extend to receipt completion and can include shutdown overhead. Summed tool time can overlap; it is not subtracted to estimate pure model compute. Earlier user-stopped attempts have incomplete timing. Artifact paths and hashes are retained in the JSON evidence export; no raw reasoning text is exported.</p></section>'
        body += '<section><h2>Implementation and official references</h2><p>The local review inspected the installed ZCode Agent CLI 0.17.0: formatMcpToolResult, createMcpToolEntry and serializeOutput; and the workspace ZCode adapter’s provider mapping, process deadline, recorder and collect_wire implementation. The reviewed file hashes are recorded in the exported evidence receipt. Rebuilding needs that receipt and saved traces, not an installed ZCode runtime.</p><p>Official parameter documentation checked on ' + escape(diagnosis['sources_verified_on']) + ': ' + ' · '.join('<a href="' + escape(s['url']) + '">' + escape(s['title']) + '</a>' for s in diagnosis['sources']) + '. Documentation describes supported behavior; it does not establish the private provider’s handling of a cached request.</p></section>'
        self.page('diagnostics.html', 'Agent environment and GLM diagnostics', body)

    def exports(self):
        self.data['totals'] = summary([r for r in self.data['records'] if r['role'] == 'primary'], self.data['models'])
        self.data['condition_totals'] = {c['id']: summary([r for r in self.all_records() if r['role'] == 'primary' and r.get('condition') == c['id']], self.data['models']) for c in self.data['conditions']['conditions']}
        self.data['condition_totals']['active-fixed'] = summary(self.current_records(), self.data['models'])
        active = self.current_records()
        graded_models = {}
        for row in active:
            if row['grade']:
                graded_models.setdefault((row['experiment'], row['case']['id']), set()).add(row['model'])
        shared = {k for k, models in graded_models.items() if models == set(self.data['models'])}
        self.data['shared_case_comparison'] = {'cases': [list(k) for k in sorted(shared)], 'totals': summary([r for r in active if (r['experiment'], r['case']['id']) in shared], self.data['models'])}
        self.write('data/comparison.json', json_text(self.data))
        self.write('data/report-inputs.json', json_text({'build_id': self.build_id, 'files_sha256': self.data['input_files_sha256']}))
        self.write('data/capability-measurements.json', json_text(self.data['capability_measurements']))
        self.write('data/capability-registration.json', json_text(self.data['capability_registration']))
        self.write('data/migration.json', json_text(self.migration))
        for filename, key in [('benchmark-configuration', 'benchmark_configuration'),
                              ('benchmark-status', 'benchmark_status'), ('runner-validation', 'runner_validation'),
                              ('entrypoint-archive', 'entrypoint_archive')]:
            self.write('data/' + filename + '.json', json_text(self.data[key]))
        self.write('data/pricing-glm.json', json_text(self.data['glm_pricing']))
        self.write('data/pricing-gpt-baselines.json', json_text(self.data['supplemental_pricing']))
        self.write('data/glm-diagnosis.json', json_text(self.data['glm_diagnosis']))
        self.write('data/environment-audit.json', json_text(self.data['environment_audit']))
        self.write('data/evidence-validation.json', json_text(self.data['evidence_validation']))
        self.write('data/evidence-paging-validation.json', json_text(self.data['paging_validation']))
        self.write('data/condition-evidence-audit.json', json_text(self.data['condition_evidence_audit']))
        self.write('data/environment-profile-archives.json', json_text(self.data['environment_profile_archives']))
        for name, raw in self.profile_files.items():
            self.write(name, raw)
        self.write('data/cpp-build-points.json', json_text(self.data['cpp_build_points']))
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['experiment', 'condition', 'role', 'case', 'target_compiler', 'target_compiler_version',
                         'standard', 'optimization', 'string_abi', 'standard_library', 'strip_mode', 'model', 'harness', 'status', 'compiled', 'passed',
                         'checks', 'readability', 'known_tokens', 'cost_low_usd', 'cost_high_usd', 'usage_complete',
                         'environment_delivery', 'model_only_comparison_certified'])
        for row in self.all_records():
            grade, receipt = row['grade'] or {}, row['receipt'] or {}
            build = row.get('target_build', {})
            writer.writerow([row['experiment'], row.get('condition', 'legacy-recorded'), row['role'], row['case']['id'],
                *[build.get(k, '') for k in ['compiler_family', 'compiler_version', 'standard', 'optimization', 'string_abi', 'standard_library', 'strip_mode']],
                row['model'], row['harness'], row['status'],
                grade.get('compiled'), grade.get('passed'), grade.get('total'),
                row['readability']['total'] if row['readability'] else '', receipt.get('reported_usage', {}).get('totalTokens'),
                row['cost'].get('low_usd'), row['cost'].get('high_usd'), row['cost'].get('usage_complete'),
                self.environment_label(row), False])
        self.write('data/comparison.csv', output.getvalue())
        for source, target in self.aliases.items():
            if (self.output / source).exists():
                continue
            if not (self.output / target).is_file():
                # Source-only checkouts have no private historical result pages.
                target = ('history/index.html' if target.startswith('history/') else
                          'cases/index.html' if target.startswith('cases/') else 'index.html')
            relative = os.path.relpath(target, str(Path(source).parent))
            self.write(source, '<!doctype html><html lang="en"><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=' +
                escape(relative) + '"><title>Benchmark report moved</title><p><a href="' + escape(relative) + '">Open the generated benchmark report</a>.</p></html>\n')

    def build(self):
        self.write('assets/report.css', self.css)
        self.write('assets/report.js', self.js)
        for name, content in i18n_report.assets().items(): self.write(name, content)
        self.overview()
        self.case_pages()
        self.costs()
        self.measurement()
        self.capabilities_page()
        self.compilers_page()
        self.coverage_page()
        self.history_pages()
        self.methodology()
        self.diagnostics()
        self.exports()
        i18n_report.audit(self.output)
        self.write('.generated-report.json', json_text({'generator': 'benchmarks/scripts/report.py', 'build_id': self.build_id}))
        with zipfile.ZipFile(self.output / 'benchmark-report.zip', 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(self.output.rglob('*')):
                if not path.is_file() or path.name == 'benchmark-report.zip':
                    continue
                info = zipfile.ZipInfo(str(path.relative_to(self.output)), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes(), compresslevel=9)


def build(output=DEFAULT_OUTPUT):
    from report_io import snapshot, timed, metadata_snapshot
    from grading.metadata_admission import metadata_validation_cache
    stages = []
    started = time.perf_counter()
    output = Path(output).resolve()
    if output in [ROOT, BENCH] or output == ROOT / 'benchmarks/runs':
        raise ValueError('Refusing to replace source or run directories')
    output.parent.mkdir(parents=True, exist_ok=True)
    with snapshot(output / 'data/report-inputs.json') as inputs, tempfile.TemporaryDirectory(prefix='rea-report-') as temporary:
        stage = Path(temporary) / 'reports'
        stage.mkdir()
        with timed('collect_records', stages), metadata_snapshot():
            data = records.collect()
        with timed('collect_report_evidence', stages), metadata_snapshot(), metadata_validation_cache():
            report = Report(stage, data)
        action = action_cache.reporting(report.data)
        local = action_cache.Cache()
        with timed('render_private', stages):
            hit = local.lookup(action)
            if hit:
                local.restore(hit, stage)
            else:
                report.build()
                local.capture(action, stage)
        with timed('seal_inputs', stages):
            inputs.seal()
        with tempfile.TemporaryDirectory(prefix='.report-publish-', dir=output.parent) as publication:
            ready = Path(publication) / 'reports'
            shutil.copytree(stage, ready)
            if output.exists():
                if not (output / '.generated-report.json').exists() and output != DEFAULT_OUTPUT:
                    raise ValueError('Output directory is not marked as generated report output')
                output.rename(Path(publication) / 'previous')
            ready.rename(output)
    print('REPORTS BUILT:', output, '·', report.build_id[:12], '· no inference', flush=True)
    if output == DEFAULT_OUTPUT:
        import public_report
        with timed('public_and_pages', stages):
            public_report.refresh(report)
        timing = {'schema_version': 1, 'seconds': round(time.perf_counter() - started, 6),
                  'stages': stages, 'model_api_calls': 0}
        (BENCH / 'config/report-timings.json').write_text(json_text(timing))
        from report_io import publish_timing
        publish_timing()
    print(f'REPORT TOTAL: {time.perf_counter() - started:.3f}s', flush=True)
    return report.build_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['build', 'clean', 'public', 'pages'], default='build')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action in ['public', 'pages']:
        import public_report
        public_report.build(args.output or public_report.OUTPUT)
        if args.action == 'pages':
            public_report.stage_pages(args.output or public_report.OUTPUT)
        return
    args.output = args.output or DEFAULT_OUTPUT
    if args.action == 'clean':
        if args.output.exists():
            if not (args.output / '.generated-report.json').exists():
                raise SystemExit('Refusing to clean a directory without the generated-report marker')
            shutil.rmtree(args.output)
    else:
        build(args.output)


if __name__ == '__main__':
    main()
