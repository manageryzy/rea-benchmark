"""Accessible, dependency-free charts generated only from saved report records."""
import html


def esc(value):
    return html.escape(str(value), quote=True)


def elapsed_seconds(receipt):
    if not receipt:
        return None
    value = receipt.get('elapsed_seconds')
    if value is None and receipt.get('elapsed_ms') is not None:
        value = receipt['elapsed_ms'] / 1000
    return value


COLORS = {'deepseek-flash': '#087f8c', 'gpt-6.1-sol': '#4f46b8', 'glm-5.3': '#ba6217',
          'gpt-6-luna': '#a12670', 'gpt-5.6-sol': '#426b23'}
STATES = [('graded', 'Graded'), ('inference_saved_grading_pending', 'Awaiting grade'),
          ('interrupted', 'Interrupted'), ('running', 'Running'), ('not_started', 'Not started'),
          ('environment_failure', 'Harness/provider failure')]


def metric(title, items, value_key, detail, maximum=100):
    content = '<figure class="chart"><figcaption>' + esc(title) + '</figcaption>'
    for item in items:
        value = item[value_key]
        label = detail(item)
        color = COLORS.get(item['model'], '#475569')
        content += '<div class="chart-row"><div class="chart-label"><strong>' + esc(item['model_label']) + '</strong><span>' + esc(label) + '</span></div>'
        if value is None:
            content += '<div class="bar-track pending"><span>Pending measurement</span></div>'
        else:
            width = min(100, max(0, value / maximum * 100)) if maximum else 0
            content += '<div class="bar-track" role="img" aria-label="' + esc(item['model_label'] + ': ' + label) + '"><span class="bar-fill" style="width:' + f'{width:.4f}' + '%;background:' + color + '"></span></div>'
        content += '</div>'
    return content + '</figure>'


def score_charts(items):
    values = []
    for item in items:
        row = dict(item)
        for key, numerator, denominator in [('accuracy', 'passed', 'checks'), ('program_rate', 'programs_passed', 'graded_attempts'),
                                             ('point_rate', 'points_passed', 'points_total')]:
            row[key] = row[numerator] / row[denominator] * 100 if row[denominator] else None
        values.append(row)
    def accuracy(item):
        return (f'{item["accuracy"]:.1f}% · {item["passed"]:,}/{item["checks"]:,} checks · '
                f'{item["graded_attempts"]}/{item["planned_cells"]} cases graded') if item['checks'] else 'No graded checks'
    charts = metric('Exact behavioral correctness', values, 'accuracy', accuracy)
    charts += metric('Whole programs passing every check', values, 'program_rate',
                     lambda r: f'{r["programs_passed"]}/{r["graded_attempts"]} graded programs · {r["planned_cells"]} planned')
    charts += metric('Function points passing every check', values, 'point_rate',
                     lambda r: f'{r["points_passed"]}/{r["points_total"]} points')
    charts += metric('Stored readability reviews', values, 'readability_mean',
                     lambda r: f'{r["readability_mean"]:.2f}/10 · n={r["readability_n"]}' if r['readability_n'] else 'Review pending · n=0', 10)
    return '<div class="chart-grid">' + charts + '</div>'


def runtime_chart(items):
    measured = [r['runtime_median_seconds'] for r in items if r['runtime_median_seconds'] is not None]
    chart = metric('Median saved runtime', items, 'runtime_median_seconds',
                   lambda r: (f'{r["runtime_median_seconds"] / 60:.1f} min · n={r["runtime_n"]} · '
                              f'{r["runtime_timeouts"]} timed out') if r['runtime_n'] else 'No measured runtime',
                   max(measured, default=1) or 1)
    return chart + '<p class="chart-note">Elapsed inference time includes provider/network latency and tool waiting; offline grading is excluded. Graded timeouts are included, not treated as fast successes. Unfinished or interrupted attempts without a saved duration are excluded. Hardware, harness and provider differences limit attribution to the model alone.</p>'


def completion(items):
    body = '<figure class="chart"><figcaption>Completion coverage across native cases</figcaption>'
    for item in items:
        body += '<div class="chart-row"><div class="chart-label"><strong>' + esc(item['model_label']) + '</strong><span>' + str(item['graded_attempts']) + '/' + str(item['planned_cells']) + ' graded</span></div><div class="stack-track">'
        for state, label in STATES:
            count = item['statuses'].get(state, 0)
            if count:
                description = label + ': ' + str(count)
                body += '<span class="segment state-' + state + '" style="width:' + f'{100 * count / item["planned_cells"]:.4f}' + '%" role="img" aria-label="' + description + '"><span>' + str(count) + '</span><span class="tip">' + description + '</span></span>'
        body += '</div></div>'
    body += '<div class="legend">' + ''.join('<span><i class="state-' + state + '"></i>' + label + '</span>' for state, label in STATES) + '</div></figure>'
    return body


def token_chart(rows, catalog):
    series = []
    keys = [('inputTokens', 'Fresh input', 'fresh'), ('cacheReadTokens', 'Cached input', 'cached'),
            ('cacheWriteTokens', 'Cache writes', 'writes'), ('outputTokens', 'Output', 'output')]
    for model, info in catalog.items():
        receipts = [r['receipt'] for r in rows if r['model'] == model and r['receipt']]
        counts = {key: sum(r.get('reported_usage', {}).get(key, 0) for r in receipts) for key, _, _ in keys}
        total = sum(r.get('reported_usage', {}).get('totalTokens', 0) for r in receipts)
        series.append((info['label'], counts, total, len(receipts)))
    maximum = max((sum(counts.values()) for _, counts, _, _ in series), default=1) or 1
    body = '<figure class="chart"><figcaption>Known token composition · common absolute scale</figcaption>'
    for label, counts, total, n in series:
        body += '<div class="chart-row"><div class="chart-label"><strong>' + esc(label) + '</strong><span>' + f'{total:,} reported tokens · {n} started attempts' + '</span></div><div class="stack-track">'
        for key, description, css in keys:
            if counts[key]:
                text = description + ': ' + f'{counts[key]:,}'
                body += '<span class="segment token-' + css + '" style="width:' + f'{100 * counts[key] / maximum:.5f}' + '%" role="img" aria-label="' + text + '"><span class="tip">' + text + '</span></span>'
        body += '</div></div>'
    body += '<div class="legend">' + ''.join('<span><i class="token-' + css + '"></i>' + label + '</span>' for _, label, css in keys) + '</div><p class="chart-note">Fresh input, cached input, reported cache writes and output are distinct categories. Missing categories are unknown; observed totals can omit interrupted requests. Reasoning is not added twice.</p></figure>'
    return body


def outcome_chart(rows, catalog):
    states = [('pass', 'All checks passed'), ('mismatch', 'Behavior mismatch'),
              ('compile', 'No compilable source'), ('timeout', 'Inference timed out'),
              ('pending', 'Not graded')]
    body = '<figure class="chart"><figcaption>Source outcomes across planned native cases</figcaption>'
    for model, info in catalog.items():
        selected = [r for r in rows if r['model'] == model]
        if not selected:
            continue
        counts = {key: 0 for key, _ in states}
        for row in selected:
            key = 'pending' if not row['grade'] else 'pass' if row['program_success'] else 'timeout' if (row['receipt'] or {}).get('timed_out') else 'mismatch' if row['grade']['compiled'] else 'compile'
            counts[key] += 1
        body += '<div class="chart-row"><div class="chart-label"><strong>' + esc(info['label']) + '</strong><span>' + str(len(selected)) + ' planned cases</span></div><div class="stack-track">'
        for key, label in states:
            if counts[key]:
                text = label + ': ' + str(counts[key])
                body += '<span class="segment outcome-' + key + '" style="width:' + f'{100 * counts[key] / len(selected):.4f}' + '%" role="img" aria-label="' + text + '"><span>' + str(counts[key]) + '</span><span class="tip">' + text + '</span></span>'
        body += '</div></div>'
    return body + '<div class="legend">' + ''.join('<span><i class="outcome-' + key + '"></i>' + label + '</span>' for key, label in states) + '</div><p class="chart-note">A graded failure caused by the inference deadline is separated from incorrect native behavior. Not graded includes user-stopped attempts and cases still pending. A complete source that passes every check is counted as passing; any later harness timeout remains disclosed on its case page.</p></figure>'


def cost_chart(items):
    maximum = max((r['cost']['high_usd'] or 0 for r in items), default=1) or 1
    body = '<figure class="chart"><figcaption>Observed API cost estimates · USD equivalent</figcaption>'
    for row in items:
        cost = row['cost']
        low, high = cost['low_usd'], cost['high_usd']
        if low is None:
            label = 'Not reported'
        else:
            label = '$' + f'{low:.4f}' + ('–$' + f'{high:.4f}' if high - low > 1e-10 else '')
            label += ' · ' + str(row['started_attempts']) + ' started attempts'
            if not cost['usage_complete']:
                label += ' · partial usage; additional cost unknown'
        body += '<div class="chart-row"><div class="chart-label"><strong>' + esc(row['model_label']) + '</strong><span>' + esc(label) + '</span></div>'
        body += '<div class="bar-track cost-track" role="img" aria-label="' + esc(row['model_label'] + ': ' + label) + '">'
        if low is not None:
            body += '<span class="bar-fill" style="width:' + f'{100 * low / maximum:.5f}' + '%;background:' + COLORS.get(row['model'], '#475569') + '"></span>'
            if high > low:
                body += '<span class="cost-range" style="left:' + f'{100 * low / maximum:.5f}' + '%;width:' + f'{100 * (high-low) / maximum:.5f}' + '%"></span>'
        body += '</div></div>'
    return body + '<p class="chart-note">Solid bars show the lower rate estimate; hatching shows the rate uncertainty up to the upper estimate. Partial usage has no known upper expenditure bound. GPT values are API equivalents, not subscription charges. Coverage differs; these are spending totals, not cost-efficiency rankings.</p></figure>'


def heatmap(rows, catalog, prefix=''):
    groups = {}
    for row in rows:
        groups.setdefault((row['experiment'], row['case']['id']), {})[row['model']] = row
    body = '<div class="scroll"><table class="heatmap"><caption>Native case × model · exact check pass rate</caption><thead><tr><th>Frozen case / protocol</th>'
    body += ''.join('<th>' + esc(info['label']) + '</th>' for info in catalog.values()) + '</tr></thead><tbody>'
    for (experiment, case), models in groups.items():
        first = next(iter(models.values()))
        body += '<tr><th scope="row">' + esc(first['case']['label']) + '<small>' + esc(first['experiment_label']) + '</small></th>'
        for model in catalog:
            row = models.get(model)
            if not row:
                body += '<td>Not configured</td>'
                continue
            grade = row['grade']
            if grade:
                rate = grade['passed'] / grade['total'] if grade['total'] else 0
                label = f'{100*rate:.1f}%'
                detail = f'{grade["passed"]:,}/{grade["total"]:,}'
                css = 'heat-pass' if row['program_success'] else 'heat-partial' if grade['compiled'] else 'heat-fail'
            else:
                label = dict(STATES).get(row['status'], row['status'])
                detail, css = 'No correctness score', 'heat-pending'
            condition = row.get('condition', 'legacy-recorded') if row else 'legacy-recorded'
            suffix = '' if condition == 'legacy-recorded' else '-' + condition
            href = prefix + 'cases/' + experiment + '/' + case + suffix + '.html#' + model
            evidence = {'delivery_limited': 'Evidence limited', 'primary_complete': 'Primary text complete',
                        'wrapper_differs': 'Wrapper differs', 'unverified': 'Evidence unverified'}.get(row.get('environment_delivery'), 'Not audited')
            body += '<td><a class="heat-cell ' + css + '" href="' + esc(href) + '"><strong>' + esc(label) + '</strong><small>' + esc(detail) + '</small><small>' + evidence + '</small></a></td>'
        body += '</tr>'
    return body + '</tbody></table></div><p class="chart-note">Green: whole program passes. Amber: compiled with mismatches. Red: no compilable source. Gray: no scored result. Each cell opens the saved source and evidence; time limits and missing answers are identified on the case page.</p>'


def workflow():
    steps = [('01', 'Frozen benchmark cases', 'Public ELF, language contract, prompt, tools and budgets'),
             ('02', 'Model + harness', 'DeepSeek / GPT in DeepSeek Harness; GLM in ZCode'),
             ('03', 'Ghidra evidence', 'Restricted functions, assembly, xrefs, strings and bytes'),
             ('04', 'Native grading', 'Unchanged source → isolated compiler → hidden exact checks'),
             ('05', 'Saved records', 'Answer, evidence, usage, grades, source reviews and pricing'),
             ('06', 'Global HTML report', 'make reports → charts, case pages, JSON, CSV and portable ZIP')]
    return '<ol class="workflow" aria-label="Benchmark and reporting workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol><p class="chart-note">Model inference ends before grading; hidden tests and compiler feedback never return to the model. Steps 01–04 run only through an explicitly authorized benchmark command. Report generation reads step 05 offline and never launches another experiment.</p>'
