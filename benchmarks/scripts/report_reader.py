"""Concise public views of saved results; no inference, grading or evidence mutation."""
from collections import Counter, defaultdict
from html import escape

import localization_report
import report_budget
import report_charts as charts
import report_views as views
from report_labels import route_label


FAMILIES = {
    'record_codec': 'Record codec', 'dispatch_registry': 'Dispatch registry',
    'callback_policy': 'Callback policy', 'session_state': 'Session state',
    'variable_objects': 'Variable objects', 'numeric_aggregation': 'Numeric aggregation',
}
CATEGORIES = dict((key, label) for key, label, _ in views.CAPABILITY_CATEGORIES)
STATES = {
    'not_started': 'Not started', 'running': 'Running', 'interrupted': 'Paused or interrupted',
    'budget_exhausted': 'Budget stopped', 'environment_failure': 'Execution failure',
    'inference_saved_grading_pending': 'Grading pending',
    'localization_format_review': 'Format needs review',
    'completed_over_budget': 'Grading pending', 'completed_queue_held': 'Grading pending',
}


def _table(headers, rows, identifier=None):
    from report import table
    return table(headers, rows, identifier)


def _money(value):
    from report import money
    return money(value)


def _route(report, model):
    return route_label(model, report.data['models'].get(model))


def model_label(report, model):
    return escape(report.data['models'].get(model, {}).get('label', model))


def _kind(row):
    category = views.typed_category(row)
    profile = row.get('profile_id') or ''
    if category == 'localization':
        return 'Optimized localization' if 'optimized' in profile or 'lto' in profile else 'Function localization'
    if category == 'reconstruction':
        return 'Code generation' if profile.endswith('codegen') else 'Source recovery'
    return 'Type and ABI recovery'


def _task(row):
    family = row.get('family_id') or row.get('task_id', 'Unknown')
    return escape(FAMILIES.get(family, family)) + '<small><span>' + escape(_kind(row)) + '</span> · <code>' + escape(row.get('profile_id') or '') + '</code></small>'


def _score(grade):
    return 'Not graded' if not grade else f'{grade["passed"]:,}/{grade["total"]:,}'


def _typed_state(row):
    if (row.get('budget_outcome') or {}).get('status') == 'deadline_exceeded':
        return 'Time limit reached / not passed'
    grade = row.get('grade')
    if not grade:
        return STATES.get(row.get('status'), 'Not graded')
    if not grade['artifact_valid']:
        return 'Submission format rejected'
    if grade['task_success']:
        return 'All checks passed'
    return 'Confirmed localization mismatch' if views.typed_category(row) == 'localization' else 'Checks not all passed'


def _budget_badge(row, native=False):
    outcome = (row.get('budget_condition') or {}).get('outcome') if native else row.get('budget_outcome')
    label = report_budget.outcome_label(outcome)
    if not label:
        return ''
    if label.startswith('Deadline'):
        return '<small class="outcome-note">Time limit reached</small>'
    return '<small class="outcome-note">Budget limit reached</small>'


def _limits(report, row, native=False):
    condition = row.get('budget_condition') if native else report.data.get('typed_budget_conditions', {}).get(row.get('attempt_id'))
    if not condition or condition.get('mode') == 'unknown':
        return '<p>Task limits were not recorded.</p>'
    values = []
    response = condition.get('per_response_output_tokens')
    if response is not None:
        values.append(['Provider response limit' if condition.get('provider_output_cap_enforced') else 'Observed response threshold', views.count(response)])
    for field, label in [('wall_seconds', 'Time limit (seconds)'), ('tool_calls', 'Tool-call limit'),
                         ('max_task_output_tokens', 'Cumulative output limit'), ('max_observed_total_tokens', 'Observed total token limit')]:
        if condition.get(field) is not None:
            values.append([label, views.count(condition[field])])
    values.append(['Turn limit', views.count(condition['max_model_turns']) if condition.get('max_model_turns') is not None else 'No turn-count limit'])
    wallet = condition.get('wallet')
    if wallet:
        from decimal import Decimal
        limit = wallet.get('limit_nano')
        adaptive = condition.get('adaptive') or {}
        bootstrap = adaptive.get('mode') == 'type_mean' and adaptive.get('sample_count') == 0 and adaptive.get('limit') is None
        amount = str(Decimal(limit) / Decimal(1000000000)) if limit is not None else 'No aggregate cost limit' if bootstrap else 'Unknown'
        values.append(['Estimated cost limit', escape(wallet.get('currency') or '') + ' ' + amount])
    return '<details class="saved-task-limits"><summary>Saved task limits</summary>' + _table(['Limit', 'Saved value'], values) + '</details>'


def _native_state(row):
    if ((row.get('budget_condition') or {}).get('outcome') or {}).get('status') == 'deadline_exceeded':
        return 'Time limit reached / not passed'
    grade = row.get('grade')
    if not grade:
        return STATES.get(row.get('status'), 'Not graded')
    if row.get('program_success'):
        return 'All checks passed'
    if 'Expected' in (grade.get('failure') or ''):
        return 'Submission format rejected'
    if not grade.get('compiled'):
        return 'Compile failed'
    return 'Checks not all passed'


def _duration(row):
    duration = charts.elapsed_seconds(row.get('receipt'))
    return 'Unreported' if duration is None else f'{duration / 60:.1f} min'


def _native_table(report, rows, prefix='', identifier=None):
    return _table(['Case', 'Model', 'Outcome', 'Passed / total', 'Elapsed', 'Estimated USD'], [
        ['<a href="' + escape(prefix + report.case_link(row) + '#' + report.case_anchor(row)) + '">' + escape(row['case']['label']) + '</a>',
         model_label(report, row['model']), escape(_native_state(row)) + _budget_badge(row, native=True),
         _score(row.get('grade')), _duration(row), _money(row['cost']) if row.get('receipt') else 'Unknown']
        for row in rows], identifier)


def case_pages(report):
    groups = defaultdict(list)
    for row in report.all_records():
        groups[report.case_link(row)].append(row)
    for name, rows in groups.items():
        body = '<section><h2>Recorded scores</h2><p>' + escape(views.case_identity(rows[0])) + '</p>'
        body += '<p><a href="../../cases/index.html">Native cases</a> · <a href="../../methodology.html#scoring">How scores are calculated</a></p>'
        body += _native_table(report, rows, '../../') + '</section>'
        for row in rows:
            anchor = report.case_anchor(row)
            body += '<section id="' + escape(anchor) + '"><h2>' + model_label(report, row['model']) + '</h2>'
            body += '<p><span>' + escape(_route(report, row['model'])) + '</span> · <strong>' + escape(_native_state(row)) + '</strong></p>'
            body += _budget_badge(row, native=True)
            grade = row.get('grade')
            if grade:
                body += '<p>' + _score(grade) + ' <span>checks passed</span>.</p>'
                if grade.get('failure'):
                    body += '<p><span>Recorded grading result</span>: <code>' + escape(grade['failure']) + '</code></p>'
                if grade.get('points'):
                    body += _table(['Function point', 'Passed / total'], [[escape(point), f'{value["passed"]}/{value["total"]}'] for point, value in grade['points'].items()])
            else:
                body += '<p>No correctness score is available for this attempt.</p>'
            body += _table(['Metric', 'Saved value'], [
                ['Elapsed', _duration(row)], ['Known tokens', views.count((row.get('receipt') or {}).get('reported_usage', {}).get('totalTokens'))],
                ['Estimated USD', _money(row['cost']) if row.get('receipt') else 'Unknown'],
                ['Condition', escape(row.get('condition_label') or row.get('condition', 'Unknown'))]])
            body += _limits(report, row, native=True)
            body += '<p><a href="../../methodology.html#limits">Task limits and comparison limits</a></p>'
            if row.get('readability'):
                body += '<details class="secondary-review"><summary>Historical source readability review</summary>'
                body += _table(['Readability dimension', 'Score / 2'], [[escape(key), value] for key, value in row['readability']['scores'].items()])
                body += '<p>Stored AI review; separate from correctness.</p></details>'
            body += '</section>'
        report.page(name, rows[0]['case']['label'] + ' · ' + rows[0]['condition_label'], body)
    current = report.current_records()
    body = '<section id="native"><h2>Native case results</h2><p>One result per model and configured case. Valid saved grades include reusable results from earlier runs.</p>'
    body += '<p><a href="../capabilities.html">Localization, reconstruction and type/ABI results</a></p>'
    body += charts.heatmap(current, report.data['models'], '../', scope='Configured native cases with valid saved grades', compact=True)
    body += '</section><section><h2>Native result list</h2>' + _native_table(report, current, '../', 'current-native-results') + '</section>'
    body += '<section><h2>Saved conditions</h2><details><summary>Browse earlier native attempts</summary>'
    body += _native_table(report, report.all_records(), '../', 'all-cases') + '</details></section>'
    report.page('cases/index.html', 'Task results', body)


def capabilities(report):
    cells = [cell for cell in views.aggregate_cells(report.data) if cell['category'] != 'native']
    stats = views.aggregate_stats(cells, report.data['models'])
    body = '<section id="status"><h2>Localization, reconstruction and type/ABI results</h2><p>One result per model and frozen task. Valid saved grades count once, including reusable cached results.</p>'
    body += _table(['Model', 'Graded / configured', 'Full marks', 'Below full marks', 'Ungraded'], [
        [model_label(report, s['model']), f'{s["graded_attempts"]}/{s["planned_cells"]}', s['passed_cells'], s['graded_attempts'] - s['passed_cells'], s['planned_cells'] - s['graded_attempts']] for s in stats])
    body += '<p><a href="cases/index.html">Native case results</a> · <a href="capability-inventory.html">Task catalogue</a> · <a href="methodology.html#scoring">Scoring methods</a></p></section>'
    body += '<section id="observations"><h2>Current task results</h2><p>Ungraded states describe why a score is missing. Localization format uncertainty is kept separate from confirmed localization errors.</p>'
    rows = []
    for cell in sorted(cells, key=lambda c: (c['category'], c['row'].get('family_id', ''), c['row'].get('profile_id', ''), c['model'])):
        row = cell['row']; attempt = row.get('attempt_id')
        title = _task(row)
        if attempt:
            title = '<a href="attempts/' + escape(attempt) + '.html">' + title + '</a>'
        rows.append([title, model_label(report, cell['model']), escape(CATEGORIES.get(cell['category'], cell['category'])),
                     escape(_typed_state({**row, 'grade': cell['grade'], 'status': cell['status']})) + _budget_badge(row),
                     _score(cell['grade'])])
    body += _table(['Task', 'Model', 'Capability', 'Outcome', 'Passed / total'], rows, 'typed-results') + '</section>'
    history_rows = localization_report.history(report.data)
    body += '<section id="history"><h2>Saved attempts</h2><details><summary>Browse earlier typed attempts</summary>'
    body += _table(['Task', 'Model', 'Outcome', 'Passed / total', 'Known tokens', 'Estimated USD'], [
        ['<a href="attempts/' + escape(row['attempt_id']) + '.html">' + _task(row) + '</a>', model_label(report, row['model']),
         escape(_typed_state(row)) + _budget_badge(row), _score(row.get('grade')), views.count(row['known_tokens'].get('totalTokens')), _money(row['cost'])]
        for row in history_rows], 'typed-history') + '</details></section>'
    report.page('capabilities.html', 'Typed task results', body)
    for row in history_rows:
        _attempt_page(report, row)
    capability_inventory(report)


def _attempt_page(report, row):
    grade = row.get('grade')
    body = '<section id="outcome"><h2>' + escape(_typed_state(row)) + '</h2><p>' + _task(row) + '</p>'
    body += '<p>' + model_label(report, row['model']) + ' · ' + escape(_route(report, row['model'])) + '</p>'
    body += '<p>' + _score(grade) + (' <span>checks passed</span>.' if grade else '') + '</p>' + _budget_badge(row)
    body += '<p><a href="../capabilities.html#observations">Current task results</a> · <a href="../methodology.html#scoring">Scoring methods</a></p></section>'
    if grade:
        body += '<section id="metrics"><h2>Capability scores</h2>'
        body += _table(['Capability', 'Score / 100'], [[escape(key), 'Unmeasured' if value['score'] is None else f'{100*value["score"]:.2f}'] for key, value in grade['scores'].items()]) + '</section>'
    if row.get('localization_revision'):
        body += '<section><h2>Offline localization regrade</h2><p>Exact JSON representations were normalized offline. Original answers and grades are preserved; target locations were not changed.</p></section>'
    body += '<section id="cost"><h2>Usage and task conditions</h2>'
    body += _table(['Category', 'Known tokens'], [[escape(label), views.count(row['known_tokens'].get(key))] for key, label in (
        ('inputTokens', 'Fresh input'), ('cacheReadTokens', 'Cached input'), ('cacheWriteTokens', 'Cache writes'), ('outputTokens', 'Output'), ('totalTokens', 'Reported total'))])
    body += '<p><span>Estimated USD</span>: <span>' + _money(row['cost']) + '</span>.</p>'
    body += _table(['Field', 'Saved value'], [
        ['Source language', escape(row.get('source_language') or 'Unknown')],
        ['Output language', escape(row.get('output_language') or 'Structured artifact')],
        ['Condition', '<code>' + escape(row.get('analysis_condition') or 'Unknown') + '</code>'],
        ['Usage completeness', escape(row.get('usage_status') or 'Unknown')]])
    body += _limits(report, row)
    reason = (row.get('budget_outcome') or {}).get('queue_stop_reason')
    if reason:
        body += '<p><span>Recorded stop reason</span>: <code>' + escape(reason) + '</code></p>'
    body += '<p><a href="../methodology.html#limits">Task limits and comparison limits</a> · <a href="../costs.html">Tokens and estimated cost</a></p></section>'
    report.page('attempts/' + row['attempt_id'] + '.html', FAMILIES.get(row['family_id'], row['family_id']) + ' · ' + _kind(row), body)


def capability_inventory(report):
    tasks = {}
    for cell in report.data['capability_measurements']['cells']:
        tasks.setdefault(cell['task_id'], cell)
    body = '<section id="typed-cells"><h2>Task catalogue</h2><p>Each task is counted once in this catalogue. Model outcomes are listed on the task-results page.</p>'
    body += _table(['Task', 'Capability', 'Source language', 'Output language'], [
        [_task(row), escape(CATEGORIES.get(views.typed_category(row), 'Unknown')), escape(row['source_language']), escape(row.get('output_language') or 'Structured artifact')]
        for _, row in sorted(tasks.items(), key=lambda pair: (pair[1]['family_id'], pair[1]['profile_id']))], 'task-catalogue')
    body += '<p><a href="capabilities.html">Current task results</a> · <a href="methodology.html#scope">Measurement scope</a></p></section>'
    report.page('capability-inventory.html', 'Task catalogue', body)


def methodology(report):
    body = '<section id="scoring"><h2>How scores are calculated</h2><p>Models receive the frozen binary, task instructions and read-only analysis tools. Hidden tests and compiler feedback are unavailable during inference.</p>'
    body += _table(['Capability', 'What is checked'], [
        ['Native languages', 'Submitted source is compiled and checked against hidden exact-output tests. Full marks require every check to pass.'],
        ['Localization', 'Function roots, instruction ranges, references and call chains are compared with frozen targets. L1 uses top-1 success, L2–L4 use F1, and L5 uses full-chain success.'],
        ['Reconstruction', 'Recovered source and code-generation controls are compiled and tested for exact values, memory effects and return behavior.'],
        ['Types / ABI', 'Submitted types, layouts and calling conventions generate probes against unchanged original callers and callees. Defined values and protected memory are checked.']])
    body += '<p>Each overview axis is the mean of graded case percentages, with equal weight per case. Sample counts are shown beside scores. The native comparison also reports a check-weighted rate; it is a different measure.</p>'
    body += '<p>Localization answers with unresolved format errors remain ungraded. A confirmed location mismatch requires an admitted answer. Other task kinds retain their saved artifact-admission rules.</p></section>'
    body += '<section id="scope"><h2>Measurement scope</h2><p>The catalogue contains 195 cases per model: 42 native language cases, 9 localization cases, 104 reconstruction cases and 40 type/ABI cases. Compiler and language variants are distinct cases. Unpacking is planned and contributes no score or denominator.</p>'
    body += '<p><a href="coverage.html">Languages, containers and build conditions</a> · <a href="capability-inventory.html">Typed task catalogue</a></p></section>'
    body += '<section id="cache"><h2>Saved results and missing scores</h2><p>Valid cached grades count once per model and configured task, regardless of run date. The saved selection is used; results are never chosen by highest score. A later failed or ungraded attempt does not erase an eligible grade.</p>'
    body += '<p>Not started, paused, budget-stopped and API-failed attempts remain ungraded. A saved deadline non-pass is a measured zero. Complete answers can retain a correctness grade even when they exceeded their spending limit.</p></section>'
    body += '<section id="limits"><h2>Task limits and comparison limits</h2><p>Limits belong to each saved attempt. Recent runs used a 65,536-token response allowance and adaptive limits based on twice the complete-answer mean for the same model and task type. Historical attempts retain their original limits. Codex OAuth records observed thresholds rather than a provider-enforced response cap.</p>'
    body += '<p>Models use Codex, DeepSeek Harness or ZCode routes. Prompts, budgets, providers and covered subsets can differ. Shared-case charts align task coverage; they do not establish equal-budget or model-only rankings. Concurrent work can affect elapsed time.</p>'
    body += '<p>These are bounded task observations. They do not certify arbitrary program recovery, every language feature, binary-compatible replacements or independent task-validity review.</p>'
    body += '<p>Historical source readability is a stored AI review and is separate from correctness. Missing reviews do not count as zero.</p></section>'
    body += '<section id="cost"><h2>Usage and estimated cost</h2><p>Usage includes unique attempts, including failures. Cache reuse and offline regrading add no model usage. Missing usage remains unknown; displayed costs are published-rate estimates. GPT costs are API equivalents, not measured subscription charges.</p></section>'
    report.page('methodology.html', 'Methods and measurement scope', body)


def scope(report):
    rows = report.current_records()
    build_groups = defaultdict(list)
    for row in rows:
        build = row.get('target_build') or {}
        key = (row['case']['suite'], row['case']['language'], build.get('compiler_family', 'Unknown'), build.get('compiler_version', 'Unknown'), build.get('optimization', 'Unknown'), build.get('standard', row['case'].get('standard') or 'Unknown'))
        build_groups[key].append(row)
    body = '<section id="builds"><h2>Languages and build conditions</h2><p>Build conditions below come from configured native cases and their saved metadata. A configured case is not proof of a completed model measurement.</p>'
    body += _table(['Suite', 'Language', 'Compiler', 'Version', 'Optimization', 'Standard', 'Graded / configured'], [
        [escape(value) for value in key] + [f'{sum(bool(r.get("grade")) for r in selected)}/{len(selected)}'] for key, selected in sorted(build_groups.items())]) + '</section>'
    stl = [row for row in rows if row['case']['suite'] == 'stl-native-v2' and row['case']['language'] == 'cpp']
    coverage_rows = []
    for item in report.coverage['rows']:
        values = []
        for model in report.data['models']:
            points = [(row.get('grade') or {}).get('points', {}).get(str(item['point'])) for row in stl if row['model'] == model]
            points = [point for point in points if point] if item['status'] != 'unsupported_by_installed_toolchain' else []
            passed, total = sum(p['passed'] for p in points), sum(p['total'] for p in points)
            values.append(f'{passed}/{total}' if total else 'Not graded')
        coverage_rows.append([escape(item['name']), escape(item['standard']), escape(item['operations']), *values])
    body += '<section id="containers"><h2>Container coverage</h2><p>Counts describe the tested operations and saved checks. They do not imply coverage of every overload or specialization.</p>'
    body += _table(['Container / related type', 'Standard', 'Selected operations', *[info['label'] for info in report.data['models'].values()]], coverage_rows) + '</section>'
    body += '<section id="boundaries"><h2>Language and ABI boundaries</h2><p>Native cases cover C++, Rust and Go. The typed tasks include separate source-recovery and code-generation conditions, object layout, pointers, variants and calling conventions. Success is limited to the supplied interface and tested behavior.</p>'
    body += '<p><a href="methodology.html#scoring">Scoring methods</a> · <a href="cases/index.html">Native case results</a> · <a href="capabilities.html">Typed task results</a></p></section>'
    report.page('coverage.html', 'Languages, containers and build conditions', body)
    report.page('compilers.html', 'Languages and build conditions', '<section><h2>Build conditions</h2><p><a href="coverage.html#builds">Languages, compilers and optimization coverage</a></p></section>')


def history(report):
    links = []
    for experiment in report.data['experiments']:
        selected = [row for row in report.data['records'] if row['experiment'] == experiment['id']]
        name = 'history/' + experiment['id'] + '/index.html'
        links.append(['<a href="' + escape(experiment['id']) + '/index.html">' + escape(experiment['label']) + '</a>', len(selected), sum(bool(row.get('grade')) for row in selected)])
        body = '<section><h2>Preserved native results</h2><p>Earlier records remain available for traceability. Eligible saved grades also contribute to the current overview.</p>'
        body += _native_table(report, selected, '../../') + '</section>'
        report.page(name, experiment['label'], body)
    body = '<section id="conditions"><h2>Earlier native experiments</h2><p>Historical records retain their original conditions. Valid reusable grades are included in the current summary; browsing history does not add another result or model charge.</p>'
    body += _table(['Experiment', 'Saved cells', 'Graded cells'], links)
    body += '<p><a href="../capabilities.html#history">Earlier typed attempts</a> · <a href="../index.html">Current overview</a></p></section>'
    report.page('history/index.html', 'Saved result history', body)
