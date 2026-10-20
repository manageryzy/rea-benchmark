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
from pathlib import Path
import shutil
from statistics import median
from string import Template
import tempfile
import zipfile

import records
import report_charts as charts
import glm_diagnostics
import audit_environment
import evidence_validation
import cpp_points

ROOT, BENCH = records.ROOT, records.BENCH
DEFAULT_OUTPUT = BENCH / 'reports'
ASSETS = Path(__file__).parent / 'report_assets'
escape = lambda value: html.escape(str(value), quote=True)
STATUSES = {'graded': 'Graded', 'inference_saved_grading_pending': 'Inference saved; grading pending',
            'interrupted': 'Interrupted; preserved', 'not_started': 'Not started', 'running': 'Running',
            'environment_failure': 'Harness/provider failure; not correctness-graded'}


def json_text(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n'


def money(cost):
    low, high = cost.get('low_usd'), cost.get('high_usd')
    if low is None:
        return 'Not reported'
    value = '$' + f'{low:.5f}'
    if abs(high - low) > 1e-10:
        value += '–$' + f'{high:.5f}'
    return value + (' + unreported usage' if not cost.get('usage_complete', True) else '')


def table(headers, rows, identifier=None):
    attrs = ' id="' + escape(identifier) + '"' if identifier else ''
    return '<div class="scroll"><table' + attrs + '><thead><tr>' + ''.join(
        '<th>' + escape(x) + '</th>' for x in headers) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join('<td>' + str(x) + '</td>' for x in row) + '</tr>' for row in rows) + '</tbody></table></div>'


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
            'points_passed': sum(r['points_passed'] for r in graded),
            'points_total': sum(r['points_total'] for r in graded),
            'readability_mean': sum(reviews) / len(reviews) if reviews else None,
            'readability_n': len(reviews),
            'accepted_source_n': sum(bool(r['source']) for r in graded),
            'runtime_median_seconds': median(t for t, _ in timed) if timed else None,
            'runtime_n': len(timed), 'runtime_timeouts': sum(timeout for _, timeout in timed),
            'total_tokens': sum(r['receipt'].get('reported_usage', {}).get('totalTokens', 0) for r in receipts),
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
        for path in [Path(__file__), Path(records.__file__), Path(charts.__file__), BENCH / 'scripts/pricing.py']:
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
        self.data['benchmark_configuration'] = records.read(BENCH / 'config/benchmark.json')
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
            entry['inputs_current'] = audit_environment.receipt_current(entry, records.read_bytes) and live_audit.get('auditor_sha256') == auditor_hash and implementation_current
            if not entry['inputs_current']:
                entry['status'] = 'unverified'
        self.data['condition_evidence_audit'] = live_audit
        archives = records.read(BENCH / 'config/environment-profile-archives.json', {'profiles': {}})
        self.profile_files = {}
        for fingerprint, item in archives['profiles'].items():
            item['files_current'] = True
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
        parent = Path(name).parent
        link = lambda target: escape(os.path.relpath(target, str(parent)))
        navigation = ''.join('<a href="' + link(target) + '">' + label + '</a>' for target, label in [
            ('index.html', 'Overview'), ('cases/index.html', 'All cases'), ('coverage.html', 'Coverage'),
            ('compilers.html', 'Compilers & optimization'), ('costs.html', 'Tokens & cost'), ('diagnostics.html', 'Diagnostics'),
            ('methodology.html', 'Methodology'), ('history/index.html', 'History')])
        self.write(name, self.template.substitute(title=escape(title), stylesheet=link('assets/report.css'),
            script=link('assets/report.js'), navigation=navigation, body=body,
            data_date=escape(self.data_date), build_id=self.build_id[:12]))

    def summary_table(self, rows):
        return table(['Model / harness', 'Graded / planned', 'Programs passed', 'Exact checks',
                      'Complete points', 'AI readability', 'Median runtime', 'Known tokens', 'Estimated USD equivalent'], [
            [escape(item['model_label']) + '<small>' + escape(item['harness']) + '</small>',
             f'{item["graded_attempts"]}/{item["planned_cells"]}',
             f'{item["programs_passed"]}/{item["graded_attempts"]}',
             f'{item["passed"]:,}/{item["checks"]:,}', f'{item["points_passed"]}/{item["points_total"]}',
             f'{item["readability_mean"]:.2f}/10<small>n={item["readability_n"]}</small>' if item['readability_n'] else 'No accepted source<small>n=0</small>' if item['graded_attempts'] and not item['accepted_source_n'] else 'Pending review',
             f'{item["runtime_median_seconds"] / 60:.1f} min<small>n={item["runtime_n"]}; {item["runtime_timeouts"]} timed out</small>' if item['runtime_n'] else 'Unreported',
             f'{item["total_tokens"]:,}', money(item['cost'])]
            for item in summary(rows, self.data['models'])])

    def case_link(self, row):
        suffix = '' if row.get('condition', 'legacy-recorded') == 'legacy-recorded' else '-' + row['condition']
        return 'cases/' + row['experiment'] + '/' + row['case']['id'] + suffix + '.html'

    def all_records(self):
        return self.data['records'] + self.data.get('condition_records', [])

    def current_records(self):
        corrected = records.active_condition_rows(self.data)
        return corrected or [r for r in self.data['records'] if r['role'] == 'primary']

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
        diagnostics=[r for r in self.data.get('condition_records',[]) if (r['experiment'],r['case']['id'],r['model'],r['condition']) not in active_ids and r['receipt']]
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
                         'Compile failed' if not grade['compiled'] else 'Mismatch / stopped')
            if (row['receipt'] or {}).get('timed_out'):
                state += '; inference timed out'
            elapsed = charts.elapsed_seconds(row['receipt'])
            result.append(['<a href="' + escape(prefix + self.case_link(row)) + '">' + escape(row['case']['label']) + '</a>',
                escape(row['model_label']) + '<small>' + escape(row.get('condition_label', 'Original harnesses')) + '</small><small>Evidence: ' + escape(self.environment_label(row)) + '</small>', escape(state),
                f'{grade["passed"]:,}/{grade["total"]:,}' if grade else 'Not graded',
                f'{row["points_passed"]}/{row["points_total"]}' if grade else '—',
                str(row['readability']['total']) + '/10' if row['readability'] else 'No accepted native source' if grade and not row['source'] else 'Pending review',
                f'{elapsed / 60:.1f} min' if elapsed is not None else 'Unreported',
                f'{row["receipt"].get("reported_usage", {}).get("totalTokens", 0):,}' if row['receipt'] else '—',
                money(row['cost']) if row['receipt'] else '—'])
        return table(['Case', 'Model', 'Status', 'Exact checks', 'Points', 'Readability', 'Elapsed', 'Known tokens', 'API estimate'], result, identifier)

    def overview(self):
        primary = self.current_records()
        glm = [r for r in primary if r['harness'] == 'zcode']
        stopped = self.data['execution']['experiments_stopped']
        cards = [(len({(r['experiment'], r['case']['id']) for r in primary}), 'Existing native protocol cases'),
                 (sum(bool(r['grade']) for r in primary), 'Saved native grades'),
                 (sum(bool(r['grade']) for r in glm), 'GLM cases graded'),
                 (sum(bool(r['grade']) for r in primary if r['harness'] != 'zcode'), 'DeepSeek / GPT cases graded')]
        finished = all(r['grade'] for r in primary)
        status = 'The fixed-harness comparison is complete; all ' + str(len(primary)) + ' cells have saved grades. No experiments are running.' if stopped and finished else 'Experiments are stopped. No automatic restart or retry is enabled.' if stopped else 'Execution is enabled only through an explicit run command.'
        body = '<section><h2>Global status</h2><div class="cards">' + ''.join(
            '<div class="card"><strong>' + str(value) + '</strong>' + label + '</div>' for value, label in cards) + '</div><p><strong>' + (
            status) + '</strong></p>'
        body += '<p>This is the global report across all recorded experiments and harness conditions. The main charts show the current fixed-harness condition. Original results remain in the before/after tables, case pages and complete cost ledger. Completed, interrupted, ungraded and unstarted cells remain visible; missing usage can add further cost.</p></section>'
        body += self.condition_comparison()
        active_ids={(r['experiment'],r['case']['id'],r['model'],r['condition']) for r in primary}
        measured = [c for c in self.data['condition_evidence_audit']['cases'] if (c['experiment'],c['case'],c['model'],c['condition']) in active_ids]
        body += '<section><h2>Fixed-condition delivery checks</h2><p>' + str(sum(c.get('status') == 'primary_complete' and c.get('inputs_current') for c in measured)) + '/' + str(len(primary)) + ' planned cells have exact model-facing tool results at the audited layer. DSH is checked against durable model-input records; ZCode against outgoing cached HTTP messages. For paged cases, original hashes, field catalogs and every retrieved slice are also checked. Unrequested pages are available for retrieval and are not claimed as read. Missing or changed audit inputs remain unverified. <a href="data/condition-evidence-audit.json">Per-case delivery evidence</a>.</p></section>'
        constraints = [(c, e) for c in measured if c.get('inputs_current') for e in c.get('paging', {}).get('backend_resource_constraints', [])]
        if constraints:
            body += '<section><h2>Remaining analysis-service limits</h2><p>' + str(len(constraints)) + ' returned tool errors reached REA’s own transport/resource boundary before common paging. These errors were delivered explicitly; they are distinct from silent client clipping. Narrower pseudocode or assembly queries remain available. A complete evidence-delivery audit verifies the returned error and does not certify that an oversized backend result was available through the benchmark catalog.</p>' + table(['Case', 'Model', 'Query', 'Error'], [
                [escape(c['case']), escape(self.data['models'][c['model']]['label']), escape(e['tool']), escape(e.get('code', 'resource constraint'))] for c, e in constraints]) + '</section>'
        audit = self.data['environment_audit']
        dsh = [c for c in audit['cases'] if c['model'] != 'glm-5.3']
        body += '<div class="notice"><strong>Environment fairness: model-only comparison is not certified.</strong> The original audit found ' + str(sum(c.get('counts', {}).get('spilled', 0) > 0 for c in dsh)) + '/' + str(len(dsh)) + ' DSH attempts with spilled evidence and blocked suggested recovery, and a different ZCode clipping policy. The fixed conditions remove those client controls, but system prompts, providers and context limits still differ. Scores below describe model + harness setups. <a href="diagnostics.html#environment">Read the original cross-harness audit and required controls</a>.</div>'
        validation = self.data['paging_validation'] if self.data.get('condition_records') else self.data['evidence_validation']
        state = 'Passed offline replay' if validation.get('passed') else 'Validation pending or stale'
        body += '<section><h2>Evidence delivery fixes: ' + state + '</h2><p>The current benchmark profile disables DSH spilling, pruning and automatic compaction. A benchmark-only ZCode overlay removes proven duplicate structured data. Both clients share explicit, bounded evidence paging on the harder cases, retaining returned originals by hash. <a href="diagnostics.html#evidence-fixes">See validation and implementation status</a>. Historical scores stay unchanged; fresh attempts are reported separately under the fixed condition.</p></section>'
        diagnosis = self.data['glm_diagnosis']['cases']
        timeouts = sum(c['timed_out'] for c in diagnosis)
        body += '<section><h2>Original GLM cached-trace investigation</h2><p>' + str(timeouts) + ' original GLM attempts reached their wall limit. Their traces show extended reasoning, duplicated tool evidence, clipped results and missing deadline-aware finalization. <a href="diagnostics.html">Read the original evidence and implemented fixes</a>. The fixed condition is a new measurement of those same cases, not a revision of the original scores.</p></section>'
        native_summary = summary(primary, self.data['models'])
        body += '<section><h2>Coverage before scores</h2>' + charts.completion(native_summary) + charts.outcome_chart(primary, self.data['models']) + '</section>'
        completed = {}
        for row in primary:
            if row['grade']:
                completed.setdefault((row['experiment'], row['case']['id']), set()).add(row['model'])
        shared = {key for key, models in completed.items() if models == set(self.data['models'])}
        matched = [r for r in primary if (r['experiment'], r['case']['id']) in shared]
        body += '<section><h2>Compare recovery quality of recorded setups</h2><p>Shared graded cases provide equal coverage across all registered models. Older and lighter GPT baselines use the same existing C++ / Rust / Go cases; pending cells remain visible in the coverage tables. The shared view is selected by default when coverage differs. This does not certify equal evidence delivery or a pure model comparison. Exact checks weight large fixtures more heavily; whole-program and function-point charts show complementary measures.</p><div class="chart-tabs" role="group" aria-label="Comparison scope"><button type="button" data-chart-scope="all" aria-pressed="false">All native cases</button><button type="button" data-chart-scope="matched" aria-pressed="true">Shared graded cases (' + str(len(shared)) + ')</button></div>'
        body += '<div data-chart-panel="all" hidden><p class="chart-note">Each system uses its own graded subset. Check coverage above before comparing percentages.</p>' + charts.score_charts(native_summary) + charts.runtime_chart(native_summary) + '</div>'
        body += '<div data-chart-panel="matched"><p class="chart-note">Same ' + str(len(shared)) + ' frozen cases with saved grades for every system; graded inference failures are included. Readability can have fewer samples when no source was returned. This subset can favor easier cases while a batch is incomplete.</p>' + (charts.score_charts(summary(matched, self.data['models'])) + charts.runtime_chart(summary(matched, self.data['models'])) + '<h3>Same-case tokens, cost and runtime</h3>' + charts.token_chart(matched, self.data['models']) + charts.cost_chart(summary(matched, self.data['models'])) + self.summary_table(matched) + '<p class="chart-note">These totals cover this shared set only. Other historical and interrupted charges remain visible in the global expenditure ledger.</p>' if matched else '<p>No cases have grades across every system yet.</p>') + '</div></section>'
        body += '<section><h2>Current frozen native cases</h2><p>Additional compiler/optimization builds and frontend feature probes are tracked separately on the <a href="compilers.html">Compilers &amp; optimization page</a>; build validation is not a model measurement.</p>' + self.summary_table(primary) + '</section>'
        for experiment in self.data['experiments']:
            if experiment['role'] == 'primary':
                body += '<section><h2>' + escape(experiment['label']) + '</h2>' + self.summary_table(
                    [r for r in primary if r['experiment'] == experiment['id']]) + '</section>'
        validation = self.data['runner_validation']
        status = self.data['benchmark_status']
        body += '<section><h2>Configured benchmark and cache reuse</h2><p><code>make benchmark</code> selects every enabled model and active primary case from configuration, validates reusable inference, runs only unstarted cells, grades, audits and rebuilds this global report. Historical results remain included for every selection. <code>make benchmark BENCHMARK_FLAGS=--offline</code> refuses missing inference before provider access.</p>'
        if validation.get('passed'):
            body += '<p><strong>Offline coordinator verification passed:</strong> ' + str(validation['repeat_runs']) + ' repeated runs × ' + str(validation['cache_hits_per_run']) + ' validated cache hits; zero provider launches. One isolated simulated cache miss dispatched once and was reused on the second call. Changed inputs, corruption and automatic retries were rejected.</p>'
        else:
            body += '<p>Coordinator/cache validation is missing or stale; run <code>make check-runner</code>.</p>'
        if status:
            body += '<p>Latest configured execution: ' + str(status.get('cache_hits', 0)) + '/' + str(status.get('selected_tasks', 0)) + ' cache hits, ' + str(status.get('provider_launches', 0)) + ' provider launches, ' + str(len(status.get('failures', []))) + ' failures. Correctness, readability, tokens and cost above remain global recorded measurements.</p>'
        body += '<p><a href="data/benchmark-configuration.json">Selection configuration</a> · <a href="data/benchmark-status.json">Execution receipt</a> · <a href="data/runner-validation.json">Offline acceptance evidence</a> · <a href="data/entrypoint-archive.json">Archived entrypoints</a></p></section>'
        body += '<section><h2>Rebuild and download</h2><p><code>make reports</code> recreates every page, table, source export and ZIP offline. Reports use no generated reports as inputs.</p><p><a href="data/comparison.json">Global JSON</a> · <a href="data/comparison.csv">CSV</a> · <a href="data/report-inputs.json">Input hashes</a> · <a href="benchmark-report.zip">Portable report ZIP</a></p></section>'
        self.page('index.html', 'Global benchmark results', body)

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
            body += '<section><p>Harness condition: <strong>' + escape(rows[0].get('condition_label', condition)) + '</strong>. <a href="../../index.html#conditions">Global before/after comparison</a>.</p></section>'
            for row in rows:
                body += '<section id="' + escape(row['model']) + '"><h2>' + escape(row['model_label']) + '</h2><p>' + escape(STATUSES[row['status']]) + ' · ' + escape(row['harness']) + '</p>'
                body += '<p class="notice">Environment audit: ' + escape(self.environment_label(row)) + '. This is an observed setup result, not an environment-independent model score. <a href="../../diagnostics.html#environment">Audit details</a>.</p>'
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
            self.page(self.case_link(rows[0]), info['label'] + ' · ' + rows[0].get('condition_label', condition), body)
        primary = self.current_records()
        body = '<section><h2>At a glance: native recovery</h2><p class="notice">The matrix shows the fixed harnesses. Original results remain in the full table below. <a href="../diagnostics.html#environment">Model-only attribution is not certified</a>; matching cases does not remove system-prompt and provider differences.</p>' + charts.heatmap(primary, self.data['models'], '../') + '</section>'
        body += '<section><h2>Every recorded comparison cell and condition</h2><label>Filter by case, model or status <input data-table-filter="all-cases" aria-label="Filter cases"></label>' + self.case_table(self.all_records(), '../', 'all-cases') + '</section>'
        self.page('cases/index.html', 'All cases and models', body)

    def costs(self):
        all_rows = self.all_records()
        p = self.data['glm_pricing']
        body = '<section><h2>Price and currency basis</h2><p>GLM standard prepaid pricing is CNY ' + str(p['input']) + ' per million fresh input tokens, CNY ' + str(p['cache_read']) + ' per million cached input tokens, and CNY ' + str(p['output']) + ' per million output tokens. Cache storage is listed as temporarily free in the saved snapshot. <a href="' + escape(p['source']) + '">Official BigModel pricing</a>.</p>'
        body += '<p>USD equivalents use the saved ECB reference cross-rate: ' + f'{p["fx"]["cny_per_usd"]:.6f}' + ' CNY per USD, dated ' + escape(p['fx']['date']) + '. This is a display conversion. <a href="' + escape(p['fx']['source']) + '">ECB reference source</a>.</p><p>DeepSeek and GPT retain each experiment’s frozen published-rate snapshot and pricing-period rules. GPT dollar amounts are Codex API equivalents. Actual invoice debits, prepaid deductions and subscription-credit charges remain unavailable.</p></section>'
        for model_id, snapshot in self.data['supplemental_pricing'].items():
            spec = snapshot[model_id]
            if 'peak_utc_hours' in spec:
                body += '<section><h2>' + escape(self.data['models'][model_id]['label']) + ' pricing snapshot</h2>' + table(
                    ['USD per million tokens', 'Fresh input', 'Cached input', 'Cache writes', 'Output'], [
                        [escape(period.replace('_', ' ').title()), *[spec[period][key] for key in ['input', 'cache_read', 'cache_write', 'output']]]
                        for period in ['off_peak', 'peak']]) + '<p>Weekday peak UTC hours: ' + escape(', '.join(str(a) + ':00–' + str(b) + ':00' for a, b in spec['peak_utc_hours'])) + '. ' + escape(spec.get('holidays_note', '')) + ' Frozen on ' + escape(snapshot['verified_at_utc']) + '. <a href="' + escape(spec['source']) + '">Official model pricing</a>.</p><p>' + escape(spec.get('selection_note', '')) + ' Published-rate estimates; actual API invoice debit unavailable. Existing experiment snapshots remain unchanged.</p></section>'
                continue
            rates = spec['short']
            body += '<section><h2>' + escape(self.data['models'][model_id]['label']) + ' pricing snapshot</h2><p>Standard USD per million tokens: fresh input ' + str(rates['input']) + ', cached input ' + str(rates['cache_read']) + ', cache writes ' + str(rates['cache_write']) + ', output ' + str(rates['output']) + '. Requests above ' + str(spec['short_context_max_input']) + ' input tokens use the recorded long-context multipliers. Missing cache-write counts produce a cost range. Frozen on ' + escape(snapshot['verified_at_utc']) + '. <a href="' + escape(spec['source']) + '">Official model documentation</a>. ' + escape(spec.get('promotion_note', '')) + '</p><p>Existing experiment snapshots remain unchanged. These GPT amounts are API-equivalent estimates for the Codex plan route; they are not measured subscription or credit debits.</p></section>'
        body += '<section><h2>All observed expenditure, including all harness conditions, history and interrupted attempts</h2>' + self.summary_table(all_rows) + '</section>'
        body += '<section><h2>Spending and token mix</h2>' + charts.token_chart(all_rows, self.data['models']) + charts.cost_chart(summary(all_rows, self.data['models'])) + '</section>'
        timings = []
        for row in all_rows:
            evidence = row.get('timing_evidence')
            elapsed = charts.elapsed_seconds(row['receipt'])
            if not evidence or elapsed is None:
                continue
            timings.append(['<a href="' + escape(self.case_link(row)) + '#' + escape(row['model']) + '">' + escape(row['case']['label']) + '</a>',
                            escape(row['model_label']), f'{elapsed / 60:.2f} min',
                            f'{evidence["completed_request_seconds"] / 60:.2f} min',
                            f'{evidence["tool_seconds_sum"] / 60:.2f} min' if evidence['tool_seconds_sum'] is not None else 'Unreported',
                            str(evidence['requests_without_end_time']),
                            'Timed out' if row['receipt'].get('timed_out') else escape(STATUSES[row['status']])])
        if timings:
            body += '<section><h2>Where runtime is recorded</h2><p>Saved request ledgers distinguish API waiting from Ghidra tool time. API durations include streaming and network latency, so they do not isolate model compute. Interrupted requests without an end timestamp are omitted from that subtotal; tool durations are summed and can overlap when calls run in parallel. These columns are evidence, not an additive wall-time decomposition. Attempts without a request ledger remain in the other comparisons.</p>' + table(
                ['Case', 'Model', 'Elapsed inference', 'Completed API request time', 'Summed tool time', 'Requests without end time', 'Status'], timings) + '</section>'
        entries = []
        for row in all_rows:
            if not row['receipt']:
                continue
            usage = row['receipt'].get('reported_usage', {})
            cost = row['cost']
            cny = ('CNY ' + f'{cost["low_cny"]:.5f}' + ('–' + f'{cost["high_cny"]:.5f}' if cost['low_cny'] != cost['high_cny'] else '')) if cost.get('low_cny') is not None else '—'
            entries.append([escape(row['case']['label']) + '<small>' + escape(row.get('condition_label', 'Original harnesses')) + '</small>', escape(row['model_label']), escape(STATUSES[row['status']]),
                f'{usage.get("inputTokens", 0):,}', f'{usage.get("cacheReadTokens", 0):,}',
                f'{usage["cacheWriteTokens"]:,}' if 'cacheWriteTokens' in usage else 'Unreported',
                f'{usage.get("outputTokens", 0):,}', f'{usage.get("totalTokens", 0):,}', cny, money(cost)])
        body += '<section><h2>Provider-reported categories</h2>' + table(
            ['Case', 'Model', 'Status', 'Fresh input', 'Cached input', 'Cache writes', 'Output', 'Known total', 'CNY estimate', 'USD equivalent'], entries) + '</section>'
        body += '<section><h2>Accounting rules</h2><p>Token counts use each provider’s tokenizer and include repeated and cached context; they are not counts of unique source-code tokens. Reported output includes reasoning when the provider includes it; reasoning is not added again. Raw GLM stream usage is counted once per HTTP request. Missing cache details produce a rate range. Interrupted streams may omit final usage; displayed amounts then cover observed usage only. Synthetic local fixtures are excluded. Estimates exclude local compute, setup and this coordinating conversation.</p></section>'
        self.page('costs.html', 'Tokens, prices and cost', body)

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
        self.page('history/index.html', 'Benchmark history', body)
        diagnosis = self.data['diagnosis']
        body = '<section><h2>Saved investigation</h2><p>This assessment used cached model outputs and evidence. It does not launch inference, repair candidates or change grading.</p><p class="notice">A later <a href="../../diagnostics.html#environment">cross-harness audit</a> found DSH tool-result spills with blocked recovery. Read this earlier semantic diagnosis alongside that environment evidence; it does not certify an environment-independent model ranking.</p><pre>' + escape(json_text(diagnosis)) + '</pre></section>'
        self.page('history/stl-rust-data-v2-20261019/diagnosis.html', 'Cached failure investigation', body)

    def methodology(self):
        body = '<section><h2>One reproducible report pipeline</h2><p><code>make reports</code> builds all global, case, history, JSON, CSV, source and ZIP artifacts from saved run records, frozen pricing, stored reviews and templates. It requires no API credentials, model requests, target execution or compiler runs. Generated files are not inputs.</p><p><code>make check</code> verifies preserved artifacts, source paths, offline cached-evidence replay, real-client loopback transports, local links and two clean report builds with identical file hashes. <code>make check-environment</code> runs only the evidence replay and transport checks. HTML timestamps come from recorded data; ZIP timestamps and ordering are deterministic.</p></section>'
        body += '<section><h2>One configured execution pipeline</h2><p><code>make benchmark</code> uses <code>benchmarks/config/benchmark.json</code> and the model, experiment and condition registries. All enabled models and active primary cases are selected by default. Budgets, language, ABI and target inputs remain frozen with each registered case. Compiler/optimization expansion remains separately build-verified and unmeasured by models until registered for inference.</p><p>Inference identity covers model/reasoning, binary, prompt, tool catalog, target manifest, limits and effective adapter code. Scheduling, display labels, pricing and report changes do not invalidate inference. Every cache hit verifies its committed artifacts and original source profile. The reviewed legacy migration binds only exact recorded task hashes and preserves their original environment labels. Grade/source keys and the global report build ID are separate. Interrupted attempts and incompatible caches fail explicitly without automatic retry.</p><p><code>make check-runner</code> repeats the configured suite with provider dispatch blocked and tests a simulated missing cache. Historical per-suite runners and cohort-specific audit/review scripts are archived with original hashes. <a href="data/runner-validation.json">Acceptance receipt</a> · <a href="data/entrypoint-archive.json">Archive manifest</a>.</p></section>'
        body += '<section><h2>From binary to benchmark report</h2>' + charts.workflow() + '</section>'
        body += '<section><h2>Isolation and first attempts</h2><p>Task prompts, ELF hashes, tool catalogs and budgets are frozen by protocol. ZCode uses an isolated temporary workspace and data directory, a restricted REA gateway and a recorder that verifies model, tools and wire limits. Native shell, filesystem, web, workflow and agent tools are removed; skills, memory and plugins are disabled. API headers are excluded from saved model traffic.</p><p>Each saved first attempt remains immutable. Interrupted attempts do not restart automatically. Grading runs after inference without repair or model feedback. Candidate execution uses the existing isolated bwrap grader.</p></section>'
        body += '<section><h2>Correctness and readability</h2><p>Results use their recorded grading protocol. C++11/14 and the Rust data-tool cohort use the numbered byte-frame amendment; original line-based scores remain diagnostic. Compile errors, uncaught exceptions, missing returns and exact byte mismatches count as failures.</p><p>Readability is a saved coordinating-AI source review: formatting, names, decomposition, native idioms and explanatory comments, each scored 0–2. It is subjective and separate from correctness. Earlier reviews masked model labels; additional GLM reviews disclose that model identity was visible. Fixed-condition reviews use source files without model labels or grades in the review pack, but the coordinating reviewer has run context, so they are not an independent blind panel. They are not an independent human panel. Missing reviews remain pending; report generation does not ask a model to invent a score.</p></section>'
        body += '<section><h2>Model and harness interpretation</h2><p>GLM runs in ZCode, while DeepSeek/GPT run in DeepSeek Harness. System prompts, provider adapters, tokenizers, cache behavior and reasoning implementations differ. The <a href="diagnostics.html#environment">environment audit</a> also found model-visible DSH spill previews with recovery tools blocked by the benchmark allowlist. Behavioral grading remains reproducible for the submitted code, but model-only attribution is not certified. Equal tasks or a shared-case filter do not remove these confounds. Scores are never silently adjusted to guess the effect of missing evidence.</p></section>'
        body += '<section><h2>Current evidence delivery condition</h2><p>The reused basic runs use <code>lossless-evidence-v1</code>; the eight harder cases use <code>paged-evidence-v2</code> for every model. Both disable DSH spill, prune and automatic compaction and apply the benchmark-only ZCode overlay. v2 adds common, read-only evidence paging with exact reassembly checks and a 24,000-character response bound. Task fingerprints include profile and adapter hashes. Old cache hits remain their recorded condition; replaying cached evidence does not change their scores or create a new model attempt. <a href="diagnostics.html#evidence-fixes">Implemented controls, exact replay counts and remaining limits</a>.</p><p>The saved validation receipt is checked against code, configuration and cached-input hashes during every report build. Missing or changed inputs make validation unverified. Client code and the network are never loaded by report generation.</p></section>'
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
                 escape(item['profile']['harness']), len(item['attempt_tasks']), 'Verified' if item['files_current'] else 'Unverified']
                for fingerprint, item in sorted(archives.items())])
        inline = self.data['evidence_validation']
        inline_status = 'also passes against its recorded code and input hashes' if inline.get('passed') and inline.get('inputs_current') else 'is retained but does not certify the current code'
        return body + '<p><code>make check-paging</code> replays cached originals and validates both real clients against local fixtures. <code>make reports</code> consumes the saved <a href="data/evidence-paging-validation.json">current paging receipt</a> and validates its hashes without running a client, compiler or model. The separate <a href="data/evidence-validation.json">inline replay receipt</a> ' + inline_status + '. Complete data availability does not certify a pure model comparison or a particular accuracy gain.</p></section>'

    def diagnostics(self):
        diagnosis = self.data['glm_diagnosis']
        cases = diagnosis['cases']
        timed = [c for c in cases if c['timed_out']]
        clips = [entry for c in cases for entry in c['truncations']]
        primary_cuts = sum(entry.get('primary_cut') is True for entry in clips)
        matches = sum(c['reasoning_history_matches'] for c in cases)
        differences = sum(c['reasoning_history_differences'] for c in cases)
        body = self.environment_diagnostics()
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
                    '<a href="' + escape(self.case_link(row)) + '#glm-5.3">' + escape(row['case']['label']) + '</a>',
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
        self.overview()
        self.case_pages()
        self.costs()
        self.compilers_page()
        self.coverage_page()
        self.history_pages()
        self.methodology()
        self.diagnostics()
        self.exports()
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
    output = Path(output).resolve()
    if output in [ROOT, BENCH] or output == ROOT / 'benchmarks/runs':
        raise ValueError('Refusing to replace source or run directories')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.report-build-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'reports'
        stage.mkdir()
        report = Report(stage, records.collect())
        report.build()
        if output.exists():
            if not (output / '.generated-report.json').exists() and output != DEFAULT_OUTPUT:
                raise ValueError('Output directory is not marked as generated report output')
            previous = Path(temporary) / 'previous'
            output.rename(previous)
        stage.rename(output)
    print('REPORTS BUILT:', output, '·', report.build_id[:12], '· no inference', flush=True)
    return report.build_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['build', 'clean'], default='build')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.action == 'clean':
        if args.output.exists():
            if not (args.output / '.generated-report.json').exists():
                raise SystemExit('Refusing to clean a directory without the generated-report marker')
            shutil.rmtree(args.output)
    else:
        build(args.output)


if __name__ == '__main__':
    main()
