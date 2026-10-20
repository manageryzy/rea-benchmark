"""Allowlisted result cache and HTML-only publication; never launch a provider."""
from contextlib import ExitStack
import datetime
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import os
import shutil
import socket
import subprocess
from unittest.mock import patch
from string import Template
import tempfile
from urllib.parse import unquote, urlsplit

from report import Report, ASSETS, escape, json_text, money, summary, table
import report_charts as charts

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / 'benchmarks/public/summary.json'
OUTPUT = ROOT / 'benchmarks/site'
SECRET_PATTERNS = [
    r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----',
    r'\bsk-[A-Za-z0-9_-]{24,}',
    r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
    r'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}',
    r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', r'\bAIza[0-9A-Za-z_-]{35}\b',
    r'/mnt/[a-z]/|/home/|file://|[A-Za-z]:\\Users\\',
]


def privacy_check(text):
    if any(re.search(pattern, text) for pattern in SECRET_PATTERNS):
        raise ValueError('Publication rejected: credential or private-path pattern')


def pick(value, names):
    return {name: value.get(name) for name in names.split()}


CASE = 'id language suite label tool_count max_tool_calls timeout_seconds max_output_tokens standard string_abi'
ROW = 'experiment experiment_label role model model_label harness status condition condition_label output_language points_total points_passed program_success'
BUILD = 'compiler_family compiler_version optimization standard string_abi standard_library strip_mode'
POINT = 'suite base_case compiler_family compiler_id compiler_version optimization standard string_abi strip_mode status inputs_current checks'
FEATURE = 'feature compiler_id compiler_version standard status inputs_current model_attempts'
USAGE = 'inputTokens cacheReadTokens cacheWriteTokens outputTokens totalTokens'
RUBRIC = ('names', 'decomposition', 'language_idioms', 'formatting', 'explanatory_comments')


def project_row(row):
    out = pick(row, ROW)
    out['case'] = pick(row['case'], CASE)
    out['source'] = bool(row.get('source'))  # Availability only; no path or code.
    out['environment_delivery'] = row.get('environment_delivery', 'unverified')
    out['target_build'] = pick(row['target_build'], BUILD) if row.get('target_build') else None
    out['receipt'] = None
    if row.get('receipt'):
        receipt = row['receipt']
        out['receipt'] = pick(receipt, 'elapsed_seconds elapsed_ms timed_out tool_calls tool_errors')
        out['receipt']['timed_out'] = bool(receipt.get('timed_out'))
        out['receipt']['reported_usage'] = {k: receipt.get('reported_usage', {}).get(k, 0) for k in USAGE.split()}
    out['cost'] = pick(row['cost'], 'low_usd high_usd usage_complete')
    out['grade'] = None
    if row.get('grade'):
        grade = row['grade']
        out['grade'] = pick(grade, 'compiled passed total')
        # Failure text may contain provider output. Publish categories only.
        out['grade']['failure'] = ('Expected single native block' if 'Expected' in (grade.get('failure') or '') else
                                  'Compilation failed' if not grade['compiled'] else
                                  'Behavior mismatch' if not row['program_success'] else '')
        out['grade']['points'] = {k: pick(v, 'passed total') for k, v in grade.get('points', {}).items()}
    review = row.get('readability')
    out['readability'] = {'total': review['total'], 'scores': {k: review['scores'][k] for k in RUBRIC}} if review else None
    return out


def project(report):
    data = report.data
    original = data['environment_audit'].get('cases', [])
    fixed = data['condition_evidence_audit'].get('cases', [])
    return {
        'schema_version': 1, 'data_as_of': data['data_as_of'],
        'source_report_build_id': report.build_id,
        'models': {k: pick(v, 'id label harness') for k, v in data['models'].items()},
        'experiments': [pick(e, 'id label role suite') for e in data['experiments']],
        'conditions': {'active_case_conditions': data['conditions']['active_case_conditions']},
        'records': [project_row(r) for r in data['records']],
        'condition_records': [project_row(r) for r in data['condition_records']],
        'audit': {
            'original_cases': len(original),
            'original_spilled_results': sum(c.get('counts', {}).get('spilled', 0) for c in original),
            'original_blocked_recoveries': sum(c.get('blocked_spill_recovery', 0) for c in original),
            'fixed_verified_cases': sum(c.get('inputs_current') and c.get('status') == 'primary_complete' for c in fixed),
            'fixed_audited_cases': len(fixed),
            'paging_offline_verified': bool(data['paging_validation'].get('passed') and data['paging_validation'].get('inputs_current')),
        },
        'glm_diagnostics': [pick(c, 'experiment case status timed_out elapsed_seconds reasoning_chars content_chars tool_calls request_count http_errors repeated_successful_queries tool_seconds_sum') for c in data['glm_diagnosis']['cases']],
        'compiler_points': [pick(p, POINT) for p in data['cpp_build_points']['points']],
        'feature_probes': [pick(p, FEATURE) for p in data['cpp_build_points']['language_feature_probes']['probes']],
    }


# A recursive schema rejects unknown fields before publication, including fields manually
# added to the public cache. Free-form answers, errors and provenance are absent.
NUMBER = (int, float, type(None))
TEXT = (str, type(None))
MAP = lambda schema: ('map', schema)
GRADE = {'compiled': bool, 'passed': int, 'total': int, 'failure': str,
         'points': MAP({'passed': int, 'total': int})}
ROW_SCHEMA = {**{k: TEXT for k in ROW.split()},
              'points_total': NUMBER, 'points_passed': NUMBER, 'program_success': bool,
              'case': {**{k: TEXT for k in CASE.split()}, **{k: NUMBER for k in 'tool_count max_tool_calls timeout_seconds max_output_tokens string_abi'.split()}},
              'source': bool, 'environment_delivery': str,
              'target_build': ({**{k: TEXT for k in BUILD.split()}, 'string_abi': NUMBER}, type(None)),
              'receipt': ({**{k: NUMBER for k in 'elapsed_seconds elapsed_ms tool_calls tool_errors'.split()}, 'timed_out': (bool, type(None)), 'reported_usage': {k: NUMBER for k in USAGE.split()}}, type(None)),
              'cost': {'low_usd': NUMBER, 'high_usd': NUMBER, 'usage_complete': bool},
              'grade': (GRADE, type(None)),
              'readability': ({'total': NUMBER, 'scores': {k: int for k in RUBRIC}}, type(None))}
SCHEMA = {
    'schema_version': int, 'data_as_of': TEXT, 'source_report_build_id': str,
    'models': MAP({k: str for k in 'id label harness'.split()}),
    'experiments': [{k: str for k in 'id label role suite'.split()}],
    'conditions': {'active_case_conditions': MAP(str)},
    'records': [ROW_SCHEMA], 'condition_records': [ROW_SCHEMA],
    'audit': {**{k: int for k in 'original_cases original_spilled_results original_blocked_recoveries fixed_verified_cases fixed_audited_cases'.split()}, 'paging_offline_verified': bool},
    'glm_diagnostics': [{**{k: NUMBER for k in 'elapsed_seconds reasoning_chars content_chars tool_calls request_count http_errors repeated_successful_queries tool_seconds_sum'.split()}, **{k: str for k in 'experiment case status'.split()}, 'timed_out': bool}],
    'compiler_points': [{**{k: TEXT for k in POINT.split()}, 'inputs_current': bool, 'checks': NUMBER, 'string_abi': NUMBER}],
    'feature_probes': [{**{k: TEXT for k in FEATURE.split()}, 'inputs_current': bool, 'model_attempts': int}],
}


def validate_value(value, schema):
    if isinstance(schema, tuple) and schema[0] == 'map':
        if not isinstance(value, dict):
            raise ValueError('Expected publication map')
        for key, item in value.items():
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._+/-]{0,100}', key):
                raise ValueError('Invalid publication map key')
            validate_value(item, schema[1])
    elif isinstance(schema, tuple):
        for option in schema:
            try:
                validate_value(value, option)
                return
            except (ValueError, TypeError):
                pass
        raise ValueError('Invalid nullable publication value')
    elif isinstance(schema, dict):
        if not isinstance(value, dict) or set(value) != set(schema):
            raise ValueError('Unexpected or missing publication fields')
        for k, spec in schema.items():
            validate_value(value[k], spec)
    elif isinstance(schema, list):
        if not isinstance(value, list):
            raise ValueError('Expected publication list')
        for item in value:
            validate_value(item, schema[0])
    elif type(value) is not schema:
        raise ValueError('Invalid publication scalar type')
    elif isinstance(value, str) and (len(value) > 200 or '\n' in value or '\r' in value):
        raise ValueError('Unbounded publication text')
    elif isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
        raise ValueError('Invalid publication number')


def validate(data):
    validate_value(data, SCHEMA)
    if data['schema_version'] != 1 or not re.fullmatch('[0-9a-f]{64}', data['source_report_build_id']):
        raise ValueError('Unknown public cache version or build identity')
    if data['data_as_of']:
        datetime.datetime.fromisoformat(data['data_as_of'])
    seen = set()
    for row in data['records'] + data['condition_records']:
        for key in ['experiment', 'model', 'condition']:
            if not re.fullmatch('[a-zA-Z0-9_.-]+', row[key]):
                raise ValueError('Unsafe publication identifier')
        if not re.fullmatch('[a-zA-Z0-9_.-]+', row['case']['id']):
            raise ValueError('Unsafe case identifier')
        key = (row['experiment'], row['case']['id'], row['model'], row['condition'])
        if key in seen:
            raise ValueError('Duplicate publication row')
        seen.add(key)
        if row['model'] not in data['models'] or row['status'] not in {'graded', 'not_started', 'interrupted', 'running', 'inference_saved_grading_pending', 'environment_failure'}:
            raise ValueError('Unknown model or result state')
        if row['grade'] and row['grade']['passed'] > row['grade']['total']:
            raise ValueError('Invalid behavioral score')
    privacy_check(json_text(data))
    return data


class PublicReport(Report):
    def __init__(self, output, data):
        self.output, self.data = output, validate(data)
        self.template = Template((ASSETS / 'page.html').read_text())
        coverage = ROOT / 'benchmarks/suites/stl-native-v2/config/coverage.json'
        self.coverage = json.loads(coverage.read_text())
        self.build_id = hashlib.sha256(json_text(data).encode() + coverage.read_bytes() + Path(__file__).read_bytes() + (ROOT / 'benchmarks/scripts/report.py').read_bytes() + (ASSETS / 'page.html').read_bytes() + (ASSETS / 'report.css').read_bytes() + (ASSETS / 'report.js').read_bytes() + (ROOT / 'benchmarks/scripts/report_charts.py').read_bytes()).hexdigest()
        stamp = data['data_as_of']
        self.data_date = datetime.datetime.fromisoformat(stamp).astimezone(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S JST') if stamp else 'No model measurements recorded'

    def page(self, name, title, body):
        notice = '<p class="notice">Public summary: credentials, raw requests, reasoning, answers and JSON downloads are excluded. Model + harness measurements; model-only attribution is not certified.</p>'
        super().page(name, title, notice + body)
        path = self.output / name
        html = path.read_text().replace('make reports</code>', 'make pages</code>')
        html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'self\'; style-src \'self\' \'unsafe-inline\'; img-src \'self\' data:; base-uri \'none\'; form-action \'none\'">')
        path.write_text(html)

    def environment_case(self, row):
        return {'status': row['environment_delivery']}

    def overview(self):
        active = self.current_records()
        groups = {}
        for row in active:
            if row['grade']:
                groups.setdefault((row['experiment'], row['case']['id']), set()).add(row['model'])
        shared = {k for k, models in groups.items() if models == set(self.data['models'])}
        matched = [r for r in active if (r['experiment'], r['case']['id']) in shared]
        body = '<section><h2>Global status</h2><p>' + str(sum(bool(r['grade']) for r in active)) + '/' + str(len(active)) + ' active comparison cells have saved grades. Missing and environment-failed attempts remain unscored. This site regenerates from sanitized cached results; it never launches models.</p><p>Cached data timestamp above is distinct from deployment time. Historical attempts and interrupted expenditure remain included.</p></section>'
        items = summary(active, self.data['models'])
        body += '<section><h2>Coverage before scores</h2>' + charts.completion(items) + charts.outcome_chart(active, self.data['models']) + '</section>'
        body += '<section><h2>Compare recorded setups</h2><p>Shared cases have grades for every configured model; a partial batch can favor easier cases. Readability has its own review count. Different system prompts and providers remain confounders.</p><div class="chart-tabs"><button data-chart-scope="matched" aria-pressed="true">Shared graded cases (' + str(len(shared)) + ')</button><button data-chart-scope="all" aria-pressed="false">All active cases</button></div>'
        for scope, rows in [('matched', matched), ('all', active)]:
            stats = summary(rows, self.data['models'])
            body += '<div data-chart-panel="' + scope + '"' + (' hidden' if scope == 'all' else '') + '>' + self.summary_table(rows) + charts.score_charts(stats) + charts.runtime_chart(stats) + charts.token_chart(rows, self.data['models']) + charts.cost_chart(stats) + '</div>'
        body += '</section>' + self.condition_comparison()
        self.page('index.html', 'Global benchmark results', body)

    def case_pages(self):
        groups = {}
        for row in self.all_records():
            groups.setdefault(self.case_link(row), []).append(row)
        for name, rows in groups.items():
            body = '<section><h2>Recorded scores</h2>' + self.case_table(rows, '../../') + '</section>'
            for r in rows:
                body += '<section id="' + escape(r['model']) + '"><h2>' + escape(r['model_label']) + '</h2><p>Condition: ' + escape(r['condition_label']) + '. Status: ' + escape(r['status']) + '.</p>'
                if r['target_build']:
                    body += table(['Target build', 'Value'], [[escape(k), escape(v)] for k, v in r['target_build'].items()])
                if r['grade']:
                    body += '<p>' + escape(r['grade']['failure'] or 'All checks passed') + '.</p>'
                    body += table(['Function point', 'Passed / total'], [[escape(k), str(v['passed']) + '/' + str(v['total'])] for k, v in r['grade']['points'].items()])
                if r['readability']:
                    body += table(['Readability dimension', 'Score / 2'], [[escape(k), v] for k, v in r['readability']['scores'].items()])
                body += '<p>Estimated API cost: ' + money(r['cost']) + '. Raw submissions and traces are kept local.</p></section>'
            self.page(name, rows[0]['case']['label'] + ' · ' + rows[0]['condition_label'], body)
        body = '<section><h2>Active native recovery</h2>' + charts.heatmap(self.current_records(), self.data['models'], '../').replace('Each cell opens the saved source and evidence;', 'Each cell opens sanitized scores;') + '</section><section><h2>All experiments and conditions</h2><label>Filter <input data-table-filter="all-cases" aria-label="Filter cases"></label>' + self.case_table(self.all_records(), '../', 'all-cases') + '</section>'
        self.page('cases/index.html', 'All cases and models', body)

    def costs(self):
        rows = self.all_records()
        body = '<section><h2>All recorded expenditure</h2><p>Includes historical, active, interrupted and environment-failed attempts with observed usage. Partial usage can increase the displayed totals. Published-rate estimates are not invoices; GPT amounts are Codex API equivalents. Frozen pricing and exchange rates are retained when summarizing the cached results.</p>' + self.summary_table(rows) + charts.token_chart(rows, self.data['models']) + charts.cost_chart(summary(rows, self.data['models'])) + '</section>'
        body += '<section><h2>Current comparison only</h2>' + self.summary_table(self.current_records()) + '</section>'
        self.page('costs.html', 'Tokens and estimated cost', body)

    def compilers_page(self):
        body = '<section><h2>Compiler and optimization are separate testing points</h2><p>The measured C++ inputs use GCC 13.3. GCC/Clang compiler version, language standard, optimization (-O0/-O2/-O3), standard library and ABI define distinct points. Build validation is not a model measurement; the additional inference matrix remains disabled.</p>'
        body += table(['Suite', 'Case', 'Compiler', 'Version', 'Optimization', 'Standard', 'ABI', 'Build status', 'Inputs verified at snapshot'], [[escape(p[k]) for k in ['suite', 'base_case', 'compiler_id', 'compiler_version', 'optimization', 'standard', 'string_abi', 'status', 'inputs_current']] for p in self.data['compiler_points']]) + '</section>'
        body += '<section><h2>Language feature probes</h2>' + table(['Feature', 'Compiler', 'Standard', 'Status', 'Model attempts'], [[escape(p[k]) for k in ['feature', 'compiler_id', 'standard', 'status', 'model_attempts']] for p in self.data['feature_probes']]) + '</section>'
        self.page('compilers.html', 'Compiler and optimization coverage', body)

    def coverage_page(self):
        # This shared view uses only committed fixture coverage and numeric scores.
        super().coverage_page()

    def history_pages(self):
        links = []
        for experiment in self.data['experiments']:
            selected = [r for r in self.data['records'] if r['experiment'] == experiment['id']]
            path = 'history/' + experiment['id'] + '/index.html'
            links.append(['<a href="' + escape(experiment['id']) + '/index.html">' + escape(experiment['label']) + '</a>', escape(experiment['role']), len(selected)])
            self.page(path, experiment['label'], '<section><h2>Preserved original condition</h2>' + self.summary_table(selected) + self.case_table(selected, '../../') + '</section>')
        self.page('history/index.html', 'Experiment history', '<section><h2>All registered experiments</h2><p>Original conditions and diagnostic attempts retain their scores and costs; no historical attempt is relabeled as a fixed-harness run.</p>' + table(['Experiment', 'Role', 'Cells'], links) + '</section>')

    def diagnostics(self):
        a = self.data['audit']
        body = '<section id="environment"><h2>Environment fairness</h2><p>Model-only attribution is not certified. Original DSH spilling/pruning and ZCode clipping can affect delivered evidence. Current conditions disable those controls and use explicit bounded paging; different system prompts, context limits and providers remain.</p>' + table(['Cached audit metric', 'Count / status'], [[escape(k), escape(v)] for k, v in a.items()]) + '</section>'
        body += '<section id="evidence-fixes"><h2>GLM cached-trace diagnostics</h2><p>Counts below summarize historical traces without publishing their text. Long request windows can include provider queuing; reasoning characters are not a direct measure of compute time. Deadline-aware finalization and reasoning-effort ablations remain proposals, not demonstrated fixes.</p>' + table(['Case', 'Status', 'Timed out', 'Elapsed seconds', 'Reasoning characters', 'Visible characters', 'Tool calls', 'HTTP requests'], [[escape(c[k]) for k in ['case', 'status', 'timed_out', 'elapsed_seconds', 'reasoning_chars', 'content_chars', 'tool_calls', 'request_count']] for c in self.data['glm_diagnostics']]) + '</section>'
        self.page('diagnostics.html', 'Harness and timing diagnostics', body)

    def methodology(self):
        body = '<section><h2>Benchmark and reporting workflow</h2>' + charts.workflow() + '<p>Models receive only the frozen public ELF, prompt and restricted read-only REA tools. Hidden sources, tests, execution, shell access and compiler feedback are unavailable. Unchanged submissions are compiled and independently checked offline. Correctness, whole-program success, function points and five-dimension source readability are separate measures. Coordinating-AI readability reviews are not an independent blind panel.</p><p>Cached attempts are validated against effective binary, prompt, tool, model and harness inputs. Missing measurements remain pending; environment failures are not zero model scores. Cases run sequentially within each model with up to three model queues sharing the host, which can affect runtime.</p></section><section><h2>Public report reproduction</h2><p>The normal local report command refreshes an allowlisted summary cache from saved records. Local rendering blocks network and process creation and needs no provider credentials, model runtime, raw traces or downloaded toolchain. The generated site is committed under master/docs for GitHub Pages. Only HTML, CSS and JavaScript are deployed; raw JSON, source exports, answers, archives and reasoning are absent.</p><p>Summary audit flags describe the saved local verification at snapshot time; the public build cannot independently replay private traces. Source report identity: <code>' + escape(self.data['source_report_build_id']) + '</code>. Public build identity appears in each footer.</p><p><a href="https://github.com/manageryzy/rea-benchmark">Source repository and detailed documentation</a></p></section>'
        self.page('methodology.html', 'Methods and publication policy', body)

    def build(self):
        for name in ['report.css', 'report.js']:
            self.write('assets/' + name, (ASSETS / name).read_bytes())
        self.overview()
        self.case_pages()
        self.costs()
        self.compilers_page()
        self.coverage_page()
        self.history_pages()
        self.diagnostics()
        self.methodology()
        self.write('.nojekyll', '')
        validate_site(self.output)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attributes):
        self.targets.extend(v for k, v in attributes if k in ('href', 'src') and v)


def validate_site(output):
    output = Path(output).resolve()
    pages, links = 0, 0
    for path in output.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symlinks cannot be published')
        if not path.is_file():
            continue
        if path.stat().st_nlink != 1:
            raise ValueError('Hard links cannot be published')
        if path.name == '.nojekyll' and path.stat().st_size:
            raise ValueError('Public marker must be empty')
        if path.name != '.nojekyll' and path.suffix not in ('.html', '.css', '.js'):
            raise ValueError('Non-public file type in deployment')
        text = path.read_text()
        privacy_check(text)
        if path.suffix == '.html':
            pages += 1
            parser = Links()
            parser.feed(text)
            for href in parser.targets:
                url = urlsplit(href)
                if url.scheme or url.netloc:
                    if url.scheme != 'https':
                        raise ValueError('Unexpected external URL')
                    continue
                target = (path.parent / unquote(url.path)).resolve() if url.path else path
                if not target.is_relative_to(output) or not target.is_file():
                    raise ValueError('Broken or escaping publication link')
                links += 1
    if not (output / 'index.html').is_file() or not pages:
        raise ValueError('Public report is incomplete')
    return {'pages': pages, 'local_links': links, 'model_calls': 0}


def denied(*args, **kwargs):
    raise RuntimeError('Public reporting forbids network access and process creation')


def build(output=OUTPUT, snapshot=SNAPSHOT):
    # Building is separate from Git publication. Rendering has no outbound
    # network or child-process path, even if provider credentials exist locally.
    with ExitStack() as guards:
        for obj, name in [(socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                          (socket, 'create_connection'), (socket, 'getaddrinfo'),
                          (subprocess, 'Popen'), (os, 'system'), (os, 'posix_spawn'),
                          (os, 'execv'), (os, 'execve')]:
            if hasattr(obj, name):
                guards.enter_context(patch.object(obj, name, denied))
        return _build(output, snapshot)


def _build(output=OUTPUT, snapshot=SNAPSHOT):
    data = validate(json.loads(Path(snapshot).read_text()))
    output = Path(output).resolve()
    if output in (ROOT, ROOT / 'benchmarks', ROOT / 'benchmarks/reports') or output.is_relative_to(ROOT / 'benchmarks/runs'):
        raise ValueError('Refusing to replace source or private report output')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.public-build-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'site'
        stage.mkdir()
        report = PublicReport(stage, data)
        report.build()
        if output.exists():
            if not (output / '.nojekyll').is_file():
                raise ValueError('Output is not marked as a generated public site')
            output.rename(Path(tmp) / 'previous')
        stage.rename(output)
    print('PUBLIC REPORT BUILT:', output, '·', report.build_id[:12], '· no model calls', flush=True)
    return report.build_id


def refresh(report):
    """Refresh from raw local caches only; incomplete clones keep the saved summary."""
    data = validate(project(report))
    # Publication may remove text and paths, never alter any metric or sample.
    for name in ['records', 'condition_records']:
        if summary(report.data[name], report.data['models']) != summary(data[name], data['models']):
            raise ValueError('Public projection changed measured results')
    if SNAPSHOT.exists():
        old = validate(json.loads(SNAPSHOT.read_text()))
        rows = {(r['experiment'], r['case']['id'], r['model'], r['condition']): r for r in data['records'] + data['condition_records']}
        complete = all(not r['receipt'] or (rows.get((r['experiment'], r['case']['id'], r['model'], r['condition']), {}).get('receipt') and (not r['grade'] or rows[(r['experiment'], r['case']['id'], r['model'], r['condition'])]['grade'])) for r in old['records'] + old['condition_records'])
        if not complete:
            print('PUBLIC CACHE RETAINED: local raw dataset is incomplete', flush=True)
            identity = build()
            stage_pages()
            return identity
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    raw = json_text(data)
    if not SNAPSHOT.exists() or SNAPSHOT.read_text() != raw:
        with tempfile.NamedTemporaryFile(mode='w', dir=SNAPSHOT.parent, delete=False) as handle:
            handle.write(raw)
            temporary = Path(handle.name)
        temporary.replace(SNAPSHOT)
    identity = build()
    stage_pages()
    return identity


def stage_pages(site=OUTPUT, destination=ROOT / 'docs'):
    """Copy validated local HTML into the branch-based Pages publishing tree."""
    site, destination = Path(site).resolve(), Path(destination).resolve()
    validate_site(site)
    if site.is_relative_to(destination) or destination.is_relative_to(site):
        raise ValueError('Pages source and destination must be separate')
    if destination.exists() and any(destination.iterdir()):
        validate_site(destination)  # Refuse to overwrite guides or other data.
    with tempfile.TemporaryDirectory(prefix='.pages-stage-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'site'
        shutil.copytree(site, stage)
        validate_site(stage)
        if destination.exists():
            destination.rename(Path(temporary) / 'previous')
        stage.rename(destination)
    print('LOCAL PAGES STAGED:', destination, '· static HTML only', flush=True)
