"""Accessible, dependency-free charts generated only from saved report records."""
import html
import math
import textwrap
from report_states import STATES as STATE_LABELS
from report_labels import route_label


def esc(value):
    return html.escape(str(value), quote=True)


def capability_coverage(value):
    items = [('Configured cells', value['configured_cells']), ('Enabled configuration cells', value['scheduled_cells']),
             ('Unique saved attempts', value['saved_observations']),
             ('Current graded cells', sum(row['graded_cells'] for row in value['totals']))]
    content = '<figure class="chart"><figcaption>Typed capability inventory and measurement coverage</figcaption><dl class="coverage-cards">'
    for label, count in items:
        content += '<div class="coverage-card"><dt>' + esc(label) + '</dt><dd>' + f'{count:,}' + '</dd></div>'
    return content + '</dl><p class="chart-note">Configured cells are model × task pairs. Saved attempts include retired settings; they are not necessarily a subset of the current enabled cells. Disabled cells and missing observations are not correctness zeros.</p></figure>'


def capability_dimensions(groups, catalog, *, scope='Saved observations in the selected cohort', radar_first=False):
    """Render dimension-specific scores without joining incompatible measures.

    Scores are already aggregated by the caller; rendering never derives a score
    from missing data or combines dimensions into an overall model ranking.
    """
    def checked_score(value):
        score = value.get('score')
        if score is None:
            return None
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError('Capability dimension scores must be finite values on the 0–100 scale or None')
        return score

    def location(index, count, radius):
        angle = -math.pi / 2 + 2 * math.pi * index / count
        return 200 + math.cos(angle) * radius, 190 + math.sin(angle) * radius

    def coordinates(points):
        return ' '.join(f'{x:.3f},{y:.3f}' for x, y in points)

    def radar(axes, models, group):
        count = len(axes)
        patterns = ('', '7 3', '2 3', '10 3 2 3', '5 2 1 2', '12 4')
        result = '<figure class="dimension-radar dimension-radar-overlay" data-radar-group="' + esc(group['id']) + '"><figcaption>' + esc(group['label']) + ' · model comparison</figcaption>'
        result += '<div class="radar-interactive-layout"><div class="radar-plot"><svg viewBox="0 0 400 390" role="group" aria-label="' + esc(group['label'] + ' overlapping model profiles') + '">'
        result += '<title>' + esc(group['label'] + ' model comparison') + '</title><desc>Fixed radial scale from 0 at the center to 100 at the outer ring. Model checkboxes show or hide profiles. Focus a point for its exact score and sample count. Only complete profiles have polygons; missing axes remain unmeasured.</desc>'
        for level in (25, 50, 75, 100):
            result += '<polygon class="dimension-ring" points="' + coordinates(location(index, count, 104 * level / 100) for index in range(count)) + '" />'
        for index, axis_label in enumerate(axes):
            x, y = location(index, count, 104)
            result += f'<line class="dimension-spoke" x1="200" y1="190" x2="{x:.3f}" y2="{y:.3f}" />'
            x, y = location(index, count, 145)
            anchor, wrap = 'middle', 18
            if x > 310:
                x, anchor, wrap = 305, 'start', 9
            elif x < 90:
                x, anchor, wrap = 95, 'end', 9
            lines = textwrap.wrap(axis_label['label'], width=wrap, break_long_words=True, break_on_hyphens=False) or ['']
            result += f'<text class="dimension-axis-label" data-i18n-label="{esc(axis_label["label"])}" data-i18n-wrap="{wrap}" data-i18n-y="{y:.3f}" x="{x:.3f}" y="{y - (len(lines) - 1) * 9:.3f}" text-anchor="{anchor}">'
            result += ''.join(f'<tspan x="{x:.3f}" dy="{0 if index == 0 else 18}">' + esc(line) + '</tspan>' for index, line in enumerate(lines)) + '</text>'
        legend = '<fieldset class="radar-legend"><legend>Models to compare</legend><div class="radar-selection-buttons"><button type="button" data-radar-all>Show all</button><button type="button" data-radar-none>Hide all</button></div>'
        for model_index, row in enumerate(models):
            model = row['model']
            label = catalog.get(model, {}).get('label', model)
            scores = [checked_score(value) for value in row['values']]
            observed = sum(score is not None for score in scores)
            color = COLORS.get(model, '#475569')
            pattern = patterns[model_index % len(patterns)]
            result += '<g data-radar-series="' + esc(model) + '" data-observed-axes="' + str(observed) + '" aria-hidden="false" style="--dimension-color:' + color + ';--dimension-dash:' + (pattern or 'none') + '">'
            if observed == count:
                result += '<polygon class="dimension-profile" points="' + coordinates(location(index, count, 104 * score / 100) for index, score in enumerate(scores)) + '" />'
            for index, score in enumerate(scores):
                if score is not None:
                    x, y = location(index, count, 104 * score / 100)
                    tip = label + ' · ' + axes[index]['label'] + f': {score:g}/100 · n=' + str(row['values'][index]['n'])
                    result += f'<circle class="dimension-point" cx="{x:.3f}" cy="{y:.3f}" r="4.5" tabindex="0" role="img" aria-label="' + esc(tip) + '" data-radar-tip="' + esc(tip) + '"><title>' + esc(tip) + '</title></circle>'
            result += '</g>'
            legend += '<label class="radar-model"><input type="checkbox" data-radar-toggle value="' + esc(model) + '" checked><svg class="radar-swatch" viewBox="0 0 32 12" aria-hidden="true"><line x1="1" y1="6" x2="31" y2="6" stroke="' + color + '" stroke-width="3" stroke-dasharray="' + (pattern or 'none') + '" /></svg><span><strong>' + esc(label) + '</strong><small>' + str(observed) + '/' + str(count) + ' axes measured' + (' · dots only' if observed < count else '') + '</small></span></label>'
        for level in (0, 25, 50, 75, 100):
            result += f'<text class="dimension-scale-label" x="207" y="{190 - 104 * level / 100 + 5:.3f}">{level}</text>'
        result += '</svg><p class="radar-readout" data-radar-readout role="status" aria-live="polite">Hover or focus a point to inspect its score and sample count.</p></div>'
        result += legend + '</fieldset></div><p class="chart-note" data-radar-status>' + str(len(models)) + '/' + str(len(models)) + ' models shown. Selection changes chart visibility only; the scored cohort and tables stay unchanged.</p><p class="dimension-empty" data-radar-empty hidden>No models selected. Use the checkboxes or Show all to restore profiles.</p></figure>'
        return result

    body = '<div class="capability-dimensions">' + context(scope, 'Scores use a fixed 0–100 scale. Missing measurements are excluded, not scored zero.')
    if not groups:
        return body + '<p class="dimension-empty">No capability dimensions have recorded observations in this view.</p></div>'
    for group_index, group in enumerate(groups):
        axes = group['axes']
        if not axes:
            continue
        for row in group['models']:
            if len(row['values']) != len(axes):
                raise ValueError('Capability dimension values must match the declared axis order')
        body += '<section class="capability-dimension-group" data-dimension-group="' + esc(group['id']) + '"><h3>' + esc(group['label']) + '</h3>'
        body += '<p class="chart-note dimension-definition">' + esc(group['note']) + '</p>'
        if radar_first and 3 <= len(axes) <= 6 and group['models']:
            body += radar(axes, group['models'], group)
        body += '<div class="scroll dimension-matrix-region" tabindex="0" role="region" aria-label="' + esc(group['label'] + ' model scores') + '"><table class="dimension-matrix"><caption>' + esc(group['label']) + ' · score / 100 and sample count</caption><thead><tr><th scope="col">Model / client</th>'
        body += ''.join('<th scope="col">' + esc(axis_label['label']) + '</th>' for axis_label in axes) + '</tr></thead><tbody>'
        exact = []
        for row in group['models']:
            info = catalog.get(row['model'], {})
            label = info.get('label', row['model'])
            body += '<tr><th scope="row">' + esc(label) + '<small>' + esc(route_label(row['model'], info)) + '</small></th>'
            for axis_label, value in zip(axes, row['values']):
                score = checked_score(value)
                n = value.get('n', 0)
                if score is None:
                    body += '<td class="dimension-unmeasured"><span>Unmeasured</span><small>n=' + esc(n) + '</small></td>'
                else:
                    body += '<td class="dimension-measured" style="--dimension-score:' + f'{score:.4f}' + '%"><strong>' + f'{score:.1f}' + '<span class="sr-only"> out of 100</span></strong><small>n=' + esc(n) + '</small></td>'
                exact.append((label, axis_label['label'], 'Unmeasured' if score is None else str(score), n,
                              'Not available' if value.get('checks') is None else value['checks'],
                              len(value.get('members', []))))
            body += '</tr>'
        body += '</tbody></table></div><p class="chart-note">Each cell shows its score and sample count. Exact scores and check counts are available below.</p>'
        if not radar_first and 3 <= len(axes) <= 6 and group['models']:
            body += '<div class="dimension-radars"><p class="chart-note">Polygon area is not an aggregate score. Incomplete profiles show observed dots only, without lines through missing values.</p>'
            body += radar(axes, group['models'], group)
            body += '</div>'
        body += exact_table('Exact dimension scores and sample counts', ['Model', 'Dimension', 'Score / 100', 'n', 'Checks', 'Cases'], exact)
        body += '</section>'
    return body + '</div>'


def elapsed_seconds(receipt):
    if not receipt:
        return None
    value = receipt.get('elapsed_seconds')
    if value is None and receipt.get('elapsed_ms') is not None:
        value = receipt['elapsed_ms'] / 1000
    return value


COLORS = {'deepseek-flash': '#087f8c', 'deepseek-pro': '#2563a6', 'deepseek-v4-pro': '#2563a6', 'gpt-6.1-sol': '#4f46b8', 'glm-5.3': '#ba6217',
          'gpt-6-luna': '#a12670', 'gpt-5.6-sol': '#426b23'}
STATES = list(STATE_LABELS.items())


def exact_table(title, headers, rows):
    return ('<details class="chart-data"><summary>' + esc(title) + '</summary>'
            '<div class="scroll" tabindex="0" role="region" aria-label="' + esc(title) + '"><table><caption>' + esc(title) + '</caption><thead><tr>'
            + ''.join('<th scope="col">' + esc(h) + '</th>' for h in headers) + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join(('<th scope="row">' if index == 0 else '<td class="num">') + esc(value)
                + ('</th>' if index == 0 else '</td>') for index, value in enumerate(row)) + '</tr>' for row in rows)
            + '</tbody></table></div></details>')


def axis(maximum, unit):
    def display(value):
        return f'{value:.3g}' if value and abs(value) < 0.01 else f'{value:,.2f}'.rstrip('0').rstrip('.')
    return '<div class="chart-axis" aria-label="Linear scale"><span>0 ' + esc(unit) + '</span><span>' + esc(display(maximum / 2)) + '</span><span>' + esc(display(maximum)) + ' ' + esc(unit) + '</span></div>'


def context(scope, meaning):
    return '<p class="chart-note chart-context"><strong>Population:</strong> <span>' + esc(scope) + '</span>. <span>' + esc(meaning) + '</span></p>'


def metric(title, items, value_key, detail, maximum=100, *, scope='Saved native cases in the selected view', unit='%', note='Higher is better; ungraded outcomes are excluded.'):
    content = '<figure class="chart"><figcaption>' + esc(title) + '</figcaption>' + context(scope, note) + axis(maximum, unit)
    values = []
    for item in items:
        value = item[value_key]
        label = detail(item)
        values.append((item['model_label'], label))
        color = COLORS.get(item['model'], '#475569')
        content += '<div class="chart-row"><div class="chart-label"><strong>' + esc(item['model_label']) + '</strong><span>' + esc(label) + '</span></div>'
        if value is None:
            content += '<div class="bar-track pending"><span>Not measured</span></div>'
        else:
            width = min(100, max(0, value / maximum * 100)) if maximum else 0
            content += '<div class="bar-track" role="img" aria-label="' + esc(item['model_label'] + ': ' + label) + '"><span class="bar-fill" style="width:' + f'{width:.4f}' + '%;background:' + color + '"></span></div>'
        content += '</div>'
    return content + exact_table('Values and sample counts', ['Model', 'Value and denominator'], values) + '</figure>'


def score_charts(items, *, scope='Saved native cases in the selected view', structurally_single_point=False, include_readability=True):
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
    charts = metric('Exact behavioral correctness · check weighted', values, 'accuracy', accuracy, scope=scope,
                    note='Fixed scale 0–100%. Each check has equal weight, so cases with more checks contribute more. Only graded cases are scored; higher is better.')
    if values and all('case_equal_mean' in value for value in values):
        charts += metric('Case-equal behavioral correctness', values, 'case_equal_mean',
                         lambda r: f'{r["case_equal_mean"]:.1f}% · {r["graded_attempts"]} graded cases' if r['case_equal_mean'] is not None else 'No graded cases',
                         scope=scope, note='Fixed scale 0–100%. Mean of per-case check pass rates; each graded case has equal weight. Higher is better.')
    charts += metric('Whole programs / function points passing every check' if structurally_single_point else 'Whole programs passing every check', values, 'program_rate',
                     lambda r: f'{r["programs_passed"]}/{r["graded_attempts"]} graded programs · {r["planned_cells"]} planned', scope=scope,
                     note='Fixed scale 0–100%. One function point per basic program makes the program and point measures structurally identical; both original counts remain below.' if structurally_single_point else 'Fixed scale 0–100%. A program passes only when every check passes; denominator is graded programs.')
    if structurally_single_point:
        charts += '<div class="metric-equivalence">' + exact_table('Original program and function-point counts', ['Model', 'Programs', 'Function points'],
            [(r['model_label'], f'{r["programs_passed"]}/{r["graded_attempts"]}', f'{r["points_passed"]}/{r["points_total"]}') for r in values]) + '</div>'
    else:
        charts += metric('Function points passing every check', values, 'point_rate',
                         lambda r: f'{r["points_passed"]}/{r["points_total"]} points', scope=scope,
                         note='Fixed scale 0–100%. A point passes only when all its checks pass; denominator is graded function points.')
    if include_readability:
        charts += metric('Stored readability reviews', values, 'readability_mean',
                         lambda r: f'{r["readability_mean"]:.1f}/10 · n={r["readability_n"]}' if r['readability_n'] else 'Not reviewed · n=0', 10,
                         scope=scope, unit='points', note='Fixed rubric scale 0–10. Stored source reviews, not independent replication; sample count includes reviewed sources only. Higher is better.')
    return '<div class="chart-grid">' + charts + '</div>'


def runtime_chart(items, *, scope='Saved native cases in the selected view'):
    measured = [r['runtime_median_seconds'] for r in items if r['runtime_median_seconds'] is not None]
    chart = metric('Median inference runtime · graded attempts', items, 'runtime_median_seconds',
                   lambda r: (f'{r["runtime_median_seconds"] / 60:.1f} min · n={r["runtime_n"]} · '
                              f'{r["runtime_timeouts"]} timed out') if r['runtime_n'] else 'No measured runtime',
                   max(measured, default=1) or 1, scope=scope, unit='s',
                   note='Linear seconds scale ends at the largest displayed median; it changes with population. Only graded attempts with saved durations enter this median. Provider, network and tool waits are included; offline grading is excluded. Lower time alone is not higher quality.')
    samples = [(r['model_label'], ', '.join(f'{v:.3f}' for v in r.get('runtime_values_seconds', [])) or 'Not available') for r in items]
    return chart + exact_table('Individual graded-attempt durations (seconds)', ['Model', 'Saved elapsed seconds'], samples)


def completion(items, *, scope='All configured native cases in this view', title='Completion coverage across native cases'):
    body = '<figure class="chart"><figcaption>' + esc(title) + '</figcaption>' + context(scope, 'Counts are model × case cells; full bar is 100% of planned cells. Ungraded states are not correctness zeros.') + axis(100, '%')
    values = []
    for item in items:
        counts = dict(item['statuses'])
        planned = item['planned_cells']
        observed = sum(counts.values())
        unknown = sorted(set(counts) - set(STATE_LABELS))
        if observed != planned or unknown:
            body += '<p class="notice" role="alert">Completion data diagnostic for ' + esc(item['model_label']) + ': ' + esc(f'{observed} state counts / {planned} planned cells; unsupported states: {", ".join(unknown) or "none"}') + '.</p>'
        body += '<div class="chart-row"><div class="chart-label"><strong>' + esc(item['model_label']) + '</strong><span>' + str(item['graded_attempts']) + '/' + str(planned) + ' graded</span></div><div class="stack-track">'
        for state, count in counts.items():
            if count:
                label = STATE_LABELS.get(state, 'Unknown state: ' + state)
                description = label + ': ' + str(count)
                css = state if state in STATE_LABELS else 'unknown'
                body += '<span class="segment state-' + esc(css) + '" style="width:' + f'{100 * count / max(planned, observed, 1):.4f}' + '%" role="img" aria-label="' + esc(description) + '"><span>' + str(count) + '</span><span class="tip">' + esc(description) + '</span></span>'
                values.append((item['model_label'], label, count, planned))
        body += '</div></div>'
    body += '<div class="legend">' + ''.join('<span><i class="state-' + state + '"></i>' + esc(label) + '</span>' for state, label in STATES) + '</div>'
    return body + exact_table('Exact completion counts', ['Model', 'State', 'Count', 'Planned'], values) + '</figure>'


def token_chart(rows, catalog, *, scope='Saved attempts in the selected view'):
    series = []
    keys = [('inputTokens', 'Fresh input', 'fresh'), ('cacheReadTokens', 'Cached input', 'cached'),
            ('cacheWriteTokens', 'Cache writes', 'writes'), ('outputTokens', 'Output', 'output')]
    for model, info in catalog.items():
        receipts = [r['receipt'] for r in rows if r['model'] == model and r['receipt']]
        if not any(r['model'] == model for r in rows):
            continue
        def known(key):
            return [r.get('reported_usage', {}).get(key) if isinstance(r.get('reported_usage', {}).get(key), (int, float)) else 0
                    for r in receipts if isinstance(r.get('reported_usage', {}).get(key), (int, float))
                    or r.get('usage_coverage', {}).get('proven_zero_forwards') is True]
        counts = {key: sum(known(key)) for key, _, _ in keys}
        samples = {key: len(known(key)) for key, _, _ in keys}
        series.append((info['label'], counts, samples, sum(known('totalTokens')), len(known('totalTokens')), len(receipts)))
    maximum = max((sum(counts.values()) for _, counts, *_ in series), default=1) or 1
    body = '<figure class="chart"><figcaption>Known token composition · common absolute scale</figcaption>' + context(scope, 'Absolute observed token counts, not percentages or a cost-efficiency ranking. Missing categories are unknown, and observed usage can omit interrupted requests.') + axis(maximum, 'tokens')
    details = []
    for label, counts, samples, total, total_n, n in series:
        description = f'{total:,} observed reported-total tokens · {total_n}/{n} attempts report a total' if total_n else f'Total unknown · {n} attempts with receipts'
        body += '<div class="chart-row"><div class="chart-label"><strong>' + esc(label) + '</strong><span>' + description + '</span></div><div class="stack-track">'
        for key, description, css in keys:
            if counts[key]:
                text = description + ': ' + f'{counts[key]:,}'
                body += '<span class="segment token-' + css + '" style="width:' + f'{100 * counts[key] / maximum:.5f}' + '%" role="img" aria-label="' + text + '"><span class="tip">' + text + '</span></span>'
            details.append((label, description, f'{counts[key]:,}' if samples[key] else 'Unknown', f'{samples[key]}/{n}', n - samples[key]))
        body += '</div></div>'
    body += '<div class="legend">' + ''.join('<span><i class="token-' + css + '"></i>' + label + '</span>' for _, label, css in keys) + '</div><p class="chart-note">Fresh input, cached input, cache writes and output are distinct reported categories. A measured zero or proven zero provider forwards differs from an unknown category. Reasoning is not added twice. Bar components and reported totals may differ when reporting is incomplete.</p>'
    return body + exact_table('Exact token categories and reporting coverage', ['Model', 'Category', 'Observed tokens', 'Reporting attempts', 'Unknown attempts'], details) + '</figure>'


def outcome_chart(rows, catalog, *, scope='All configured native cases in this view'):
    states = [('pass', 'All checks passed'), ('mismatch', 'Behavior mismatch'),
              ('compile', 'No compilable source'), ('timeout', 'Inference timed out'),
              ('pending', 'Not graded')]
    body = '<figure class="chart"><figcaption>Source outcomes across planned native cases</figcaption>' + context(scope, 'Full bar is 100% of planned cells. Correctness is measured only for graded outcomes; not graded is a separate state.') + axis(100, '%')
    details = []
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
            details.append((info['label'], label, counts[key], len(selected)))
        body += '</div></div>'
    return body + '<div class="legend">' + ''.join('<span><i class="outcome-' + key + '"></i>' + label + '</span>' for key, label in states) + '</div><p class="chart-note">A graded inference timeout is separated from incorrect native behavior. Not graded includes incomplete budget stops, environment failures, user-stopped attempts and pending cells. Complete over-budget answers retain correctness grades with separate queue-hold labels. Passing source remains passing even if a later harness timeout is recorded.</p>' + exact_table('Exact source outcome counts', ['Model', 'Outcome', 'Count', 'Planned'], details) + '</figure>'


def usd(value):
    if value is None:
        return 'Unknown'
    if 0 < abs(value) < 0.0001:
        return '$' + f'{value:.8g}'
    return '$' + f'{value:.4f}'


def cost_chart(items, *, scope='Saved attempts in the selected view'):
    maximum = max((r['cost']['high_usd'] or 0 for r in items), default=1) or 1
    body = '<figure class="chart"><figcaption>Observed API cost estimates · USD equivalent</figcaption>' + context(scope, 'Spending totals across started attempts, not cost-efficiency rankings. Linear USD scale changes with the largest displayed rate estimate. Frozen published prices; estimates are not invoices.') + axis(maximum, 'USD')
    details = []
    for row in items:
        cost = row['cost']
        low, high = cost['low_usd'], cost['high_usd']
        if low is None:
            label = 'Not reported'
        else:
            label = usd(low) + ('–' + usd(high) if high - low > 1e-10 else '')
            label += ' · ' + str(row['started_attempts']) + ' attempts with receipts'
            if not cost['usage_complete']:
                label += ' · observed subtotal; total unknown'
        details.append((row['model_label'], 'Unknown' if low is None else str(low), 'Unknown' if high is None else str(high), row['started_attempts'], 'Complete' if cost['usage_complete'] else 'Partial or unknown'))
        body += '<div class="chart-row"><div class="chart-label"><strong>' + esc(row['model_label']) + '</strong><span>' + esc(label) + '</span></div>'
        body += '<div class="bar-track cost-track" role="img" aria-label="' + esc(row['model_label'] + ': ' + label) + '">'
        if low is not None:
            body += '<span class="bar-fill" style="width:' + f'{100 * low / maximum:.5f}' + '%;background:' + COLORS.get(row['model'], '#475569') + '"></span>'
            if high > low:
                body += '<span class="cost-range" style="left:' + f'{100 * low / maximum:.5f}' + '%;width:' + f'{100 * (high-low) / maximum:.5f}' + '%"></span>'
        body += '</div></div>'
    return body + '<p class="chart-note">Solid bars show the lower rate estimate; hatching shows rate uncertainty up to the upper estimate for known usage. Partial usage has no known upper expenditure bound. GPT values are API equivalents, not subscription charges. Exact USD values remain in the table.</p>' + exact_table('Exact cost estimates and usage coverage', ['Model', 'Lower USD', 'Upper rate estimate USD', 'Attempts with receipts', 'Usage'], details) + '</figure>'


def heatmap(rows, catalog, prefix='', *, scope='Active native cases', compact=False):
    groups = {}
    for row in rows:
        groups.setdefault((row['experiment'], row['case']['id']), {})[row['model']] = row
    body = context(scope, 'Cell numbers show exact check pass rates. Colors indicate outcome categories, not a continuous accuracy scale; two different percentages can share amber.')
    body += '<div class="scroll heatmap-region" tabindex="0" role="region" aria-label="Native cases and model outcomes"><table class="heatmap"><caption>Native case × model · exact check pass rate</caption><thead><tr><th scope="col">Frozen case / protocol</th>'
    body += ''.join('<th scope="col">' + esc(info['label']) + '</th>' for info in catalog.values()) + '</tr></thead><tbody>'
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
                rate = grade['passed'] / grade['total'] if grade['total'] else None
                label = f'{100*rate:.1f}%' if rate is not None else 'No graded checks'
                detail = f'{grade["passed"]:,}/{grade["total"]:,}'
                css = 'heat-pass' if row['program_success'] else 'heat-partial' if grade['compiled'] else 'heat-fail'
            else:
                label = STATE_LABELS.get(row['status'], 'Unknown state: ' + row['status'])
                detail, css = 'No correctness score', 'heat-pending'
            condition = row.get('condition', 'legacy-recorded')
            suffix = '' if condition == 'legacy-recorded' else '-' + condition
            anchor = model + (('-' + row['attempt_id'][:12]) if not row.get('attempt_current', True) and row.get('attempt_id') else '')
            href = prefix + 'cases/' + experiment + '/' + case + suffix + '.html#' + anchor
            evidence = {'delivery_limited': 'Evidence limited', 'primary_complete': 'Primary text complete',
                        'wrapper_differs': 'Wrapper differs', 'unverified': 'Evidence unverified'}.get(row.get('environment_delivery'), 'Not audited')
            body += '<td><a class="heat-cell ' + css + '" href="' + esc(href) + '"><strong>' + esc(label) + '</strong><small>' + esc(detail) + '</small>' + ('' if compact else '<small>' + evidence + '</small>') + '</a></td>'
        body += '</tr>'
    note = ('Green: all checks passed. Amber: checks not all passed. Red: no compilable source. Gray: not graded. Open a cell for its score and recorded outcome.' if compact else 'Green: whole program passes. Amber: compiled with mismatches. Red: no compilable source. Gray: no scored result. Each cell opens the saved source and evidence; time limits and missing answers are identified on the case page.')
    return body + '</tbody></table></div><p class="chart-note">' + note + '</p>'


def preparation_workflow():
    steps = [('01', 'Read-only cell plan', 'Keep ready, pending and invalid selected cells in the denominator'),
             ('02', 'Batch lock and stop gates', 'No preparation during planning or a stopped live command'),
             ('03', 'Preparation AC/CAS', 'Restore exact inputs or build the trusted original once per case'),
             ('04', 'Checked frozen inputs', 'Validate public package, private spec and unchanged configuration'),
             ('05', 'Inference cache or authorized call', 'Reuse valid observations; refuse missing inference in offline mode'),
             ('06', 'Versioned grades and report', 'Preserve old results and regenerate the global pages')]
    return '<ol class="workflow" aria-label="Automatic capability preparation and inference cache workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol><p class="chart-note">Preparation never calls a model. Corruption, configuration or target drift, cancellation and invalid prerequisites stop provider work. Preparation cache reuse is not a model sample.</p>'


def executor_workflow():
    steps = [('01', 'Pinned C compiler', 'Recovered C17 and public helper definitions'),
             ('02', 'ELF admission', 'Bounded static AMD64 code; no runtime relocations'),
             ('03', 'One guest invocation', 'Declared memory, callbacks and instruction budget'),
             ('04', 'Host-owned record', 'Checked return boundary, exact bytes and allowed writes')]
    return '<ol class="workflow" aria-label="Authenticated C execution workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol>'


def abi_workflow():
    return '''<figure class="chart"><figcaption>Two original-endpoint probe directions</figcaption>
<svg viewBox="0 0 840 340" role="img" aria-label="Type and ABI descriptions generate a replacement and bridge; each direction retains original compiled code" style="width:100%;height:auto">
<defs><marker id="abi-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#6b7280"/></marker></defs>
<g fill="none" stroke="#6b7280" stroke-width="2" marker-end="url(#abi-arrow)">
<path d="M420 60L650 110"/><path d="M420 60L190 190"/><path d="M310 140H530"/><path d="M310 220H530"/><path d="M190 250L370 285"/><path d="M70 140H30V310H230"/></g>
<g font-family="system-ui,sans-serif" text-anchor="middle" font-size="16">
<rect x="280" y="15" width="280" height="45" rx="8" fill="#ede9fe" stroke="#8b5cf6"/><text x="420" y="43" fill="#312e81">types.json / abi.json</text>
<rect x="70" y="110" width="240" height="60" rx="8" fill="#dbeafe" stroke="#3b82f6"/><text x="190" y="145" fill="#1e3a8a">Frozen original caller</text>
<rect x="530" y="110" width="240" height="60" rx="8" fill="#ede9fe" stroke="#8b5cf6"/><text x="650" y="145" fill="#312e81">Generated replacement</text>
<rect x="70" y="190" width="240" height="60" rx="8" fill="#ede9fe" stroke="#8b5cf6"/><text x="190" y="225" fill="#312e81">Generated call bridge</text>
<rect x="530" y="190" width="240" height="60" rx="8" fill="#dbeafe" stroke="#3b82f6"/><text x="650" y="225" fill="#1e3a8a">Frozen original callee</text>
<rect x="230" y="285" width="380" height="40" rx="8" fill="#dcfce7" stroke="#22c55e"/><text x="420" y="311" fill="#14532d">Host compares values, guards and reference</text>
</g></svg><p class="chart-note">The original object is compiled once. Description changes affect generated probes and grading identity; they do not rewrite the original endpoint or request another model attempt.</p></figure>'''


def abi_counterfactual_workflow():
    return '''<figure class="chart"><figcaption>Counterfactual type and layout controls</figcaption>
<svg viewBox="0 0 840 300" role="img" aria-label="Counterfactual type and layout targets share supplied facts and retain original call boundaries" style="width:100%;height:auto">
<defs><marker id="abi-counter-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="#64748b"/></marker></defs>
<g fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#abi-counter-arrow)"><path d="M420 54L220 95"/><path d="M420 54L620 95"/><path d="M220 165V190H340V210"/><path d="M620 165V190H500V210"/></g>
<g text-anchor="middle" font-family="system-ui,sans-serif" font-size="14">
<rect x="270" y="10" width="300" height="44" rx="8" fill="#ede9fe" stroke="#8b5cf6"/><text x="420" y="38" fill="#312e81">Same supplied premises, prompt and tools</text>
<rect x="70" y="95" width="300" height="70" rx="8" fill="#dbeafe" stroke="#3b82f6"/><text x="220" y="123" fill="#1e3a8a">Baseline original target</text><text x="220" y="148" fill="#1e3a8a">Frozen caller and callee</text>
<rect x="470" y="95" width="300" height="70" rx="8" fill="#dbeafe" stroke="#3b82f6"/><text x="620" y="123" fill="#1e3a8a">Counterfactual original target</text><text x="620" y="148" fill="#1e3a8a">Different scalar or physical layout</text>
<rect x="180" y="210" width="480" height="64" rx="8" fill="#dcfce7" stroke="#22c55e"/><text x="420" y="236" fill="#14532d">Check correct and cross-bound fixed descriptions</text><text x="420" y="259" fill="#14532d">Host records both original-endpoint probe directions</text>
</g></svg><p class="chart-note">These are offline program controls. They do not measure a model without binary evidence; premises-only model ablations remain pending.</p></figure>'''


def pointer_workflow(pointer_cells=False):
    steps = [('01', 'Same supplied pointer premises',
              'Four layout/pointer-path conditions; one lineage and identical prompts/tools' if pointer_cells else
              'Two physical layouts; one interface lineage and identical prompts/tools'),
             ('02', 'Submitted types.json', 'Offsets, pointer depth, access, extent roles and overlap claims'),
             ('03', 'Closed generated access probes', 'Actual loads/stores linked to unchanged original caller/callee objects'),
             ('04', 'Both original-endpoint directions', 'Original caller → replacement; bridge → original callee'),
             ('05', 'Host-owned observations',
              'Buffers, status, length, descriptor, separate pointer cells and guards' if pointer_cells else
              'Exact buffer bytes, status, produced length, descriptor and guards'),
             ('06', 'Independent controls',
              'Native/emulator/Python calibration; wrong depth, layout and cell-mutation controls' if pointer_cells else
              'Native/emulator/Python calibration; cross-layout guesses and alias defects')]
    return '<figure class="chart"><figcaption>Pointer and object-role recovery: T3 preparation</figcaption><ol class="workflow" aria-label="Pointer description and original-endpoint validation workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol><p class="chart-note">Unused allocation extent and erased ownership are outside this score. Offline controls add no model observations, tokens or cost; independent validity review and premises-only model ablations remain release gates.</p></figure>'


def variant_object_workflow():
    steps = [('01', 'Paired stripped C++ originals', 'Known signature and scalar roles; different tags, subobjects and union layout'),
             ('02', 'Submitted types.json', 'Discriminant mapping, base offsets, payload and active member offsets'),
             ('03', 'Closed compilation', 'Generate accesses from the submitted description; retain original object bytes'),
             ('04', 'Two interoperability directions', 'Original caller → replacement; generated bridge → original callee'),
             ('05', 'Host-owned return and memory', 'Status, active value, state, cookie, preserved tag, descriptors and guards'),
             ('06', 'Independent calibration and defects', 'Native/emulator/Python agreement; wrong tags, offsets and cross-layout guesses')]
    return '<figure class="chart"><figcaption>Tagged variants and subobjects: T4 preparation</figcaption><ol class="workflow" aria-label="Tagged variant description and original-endpoint validation workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol><p class="chart-note">Only defined active values and observable memory effects are scored. Nominal inheritance and exact unused allocation extent are outside this condition. Offline controls do not establish model performance or complete independent task-validity review.</p></figure>'


def indirect_call_workflow():
    steps = [('01', 'Complete supplied layout and ABI', 'Known pointer arrays and signatures; recover associations and dispatch relationships'),
             ('02', 'Submitted calls.json', 'Receiver, callback and context selections; virtual slot and signed this adjustment'),
             ('03', 'Actual compiler-generated thunks', 'Paired nonprimary bases and virtual method order; same original implementation endpoints'),
             ('04', 'Complementary call probes', 'Call the claimed vtable slot or adjust the receiver and call the supplied implementation'),
             ('05', 'Original caller and callee retained', 'Original caller invokes the replacement; generated root invokes the original callee'),
             ('06', 'Host-owned state and return checks', 'Both objects, contexts, callback counters and protected bytes; native/emulator/Python calibration')]
    return '<figure class="chart"><figcaption>Indirect calls, receivers and thunks: T6 preparation</figcaption><ol class="workflow" aria-label="Indirect call and receiver adjustment validation workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol><p class="chart-note">The supplied root layout and call signatures are premises, not T2/T5 scores. Passing offline controls does not establish model performance or replace independent validity and premises-only model ablations.</p></figure>'


def optimized_location_workflow(enrolled=False):
    steps = [('01', 'Separate pinned build actions', 'GCC O3 and cross-unit LTO; existing compiler identities preserved'),
             ('02', 'Real compiled phenomena', 'Inline bodies, specialization clones, hot/cold partitions and receiver thunks'),
             ('03', 'Same-build source provenance', 'Bounded DWARF 4/5 ranges identify physical instances without certifying semantics'),
             ('04', 'Final instruction review', 'Check arithmetic bytes, conditional targets, adapters and calling premises'),
             ('05', 'Diagnostic instance matching', 'One-to-one matching; missing paths lose coverage and near-misses lose precision'),
             ('06', 'Independent acceptance still pending', 'Not enrolled in model inventory; no model observations or release scores')]
    if enrolled:
        steps[-1] = ('06', 'Configured diagnostic conditions', 'Normal preparation, grading and cache paths; inference disabled and independent review pending')
    return '<figure class="chart"><figcaption>Optimized realizations: L6 diagnostic preparation</figcaption><ol class="workflow" aria-label="Optimized location validation workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol><p class="chart-note">Compiler-history categories are private analysis factors. A solver supplies code ranges, without guessing original symbols or unobservable transformation labels. Offline controls do not replace independent validity review.</p></figure>'


def native_abi_workflow(enrolled=False):
    steps = [('01', 'Pinned native originals', 'Rust-native and Go ABIInternal; C FFI is a separately labelled negative control'),
             ('02', 'Independent original calibration', 'Native execution, bounded emulation and Python compare defined fields'),
             ('03', 'Submitted physical transfer', 'Known record layouts and semantics; bounded register, indirect and stack claims'),
             ('04', 'Two complementary probes', 'Original caller to generated replacement; generated bridge to unchanged original callee'),
             ('05', 'Host-owned observation', 'Protected code, context and return slot; read actual result registers or buffer bytes'),
             ('06', 'Aggregate both directions', 'Whole-task success requires every check; matching cached observations are reused')]
    return '<figure class="chart"><figcaption>Native language boundaries: T7 diagnostic preparation</figcaption><ol class="workflow" aria-label="Native Rust and Go ABI validation workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol><p class="chart-note">These are authored positive, equivalent and defective controls, not model observations. ' + ('Configured conditions remain globally disabled; ' if enrolled else 'The development condition is outside the configured model inventory; ') + 'independent validity and measurement release remain open.</p></figure>'


def cpp_pairing_workflow():
    return '''<figure class="chart"><h3>Paired C and original-language C++</h3>
<svg viewBox="0 0 840 300" role="img" aria-label="Paired C and C++ conditions use one binary and the same host-owned comparisons" style="width:100%;height:auto">
<defs><marker id="cpp-pair-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="#64748b"/></marker></defs>
<g fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#cpp-pair-arrow)">
<path d="M420 49L220 90"/><path d="M420 49L620 90"/><path d="M220 156V180H340V200"/><path d="M620 156V180H500V200"/>
</g><g text-anchor="middle" font-family="system-ui,sans-serif" font-size="14">
<rect x="290" y="5" width="260" height="44" rx="8" fill="#dbeafe" stroke="#3b82f6"/><text x="420" y="33" fill="#1e3a8a">Same frozen original ELF and entry</text>
<rect x="70" y="90" width="300" height="66" rx="8" fill="#ede9fe" stroke="#8b5cf6"/><text x="220" y="116" fill="#312e81">Independent C17 reconstruction</text><text x="220" y="140" fill="#312e81">solution.c → pinned C compiler</text>
<rect x="470" y="90" width="300" height="66" rx="8" fill="#ede9fe" stroke="#8b5cf6"/><text x="620" y="116" fill="#312e81">Independent C++20 reconstruction</text><text x="620" y="140" fill="#312e81">solution.cpp → pinned C++ compiler</text>
<rect x="200" y="200" width="440" height="66" rx="8" fill="#dcfce7" stroke="#22c55e"/><text x="420" y="227" fill="#14532d">Same host-owned return and memory comparisons</text><text x="420" y="250" fill="#14532d" font-size="12">Identical vectors, original oracle, status, length and preserved bytes</text>
</g></svg><p class="chart-note">Fresh sessions keep answers separate. Full-semantics generation controls are paired in both languages. Preparation and compiler/guest cache reuse are not model measurements.</p></figure>'''


def matched_baseline_workflow(enrolled=False):
    steps = [('01', 'Original compiler conditions', 'Six families × Clang 20, Rust and Go; separate GCC controls'),
             ('02', 'Exact original observations', 'Status, length, arena bytes and independent Python semantics'),
             ('03', 'Separate source-language controls', 'C17 / C++20 / restricted Rust / restricted Go'),
             ('04', 'Host-owned execution', 'One invocation, declared memory, return-slot and runtime-context guards'),
             ('05', 'Independent cache stages', 'Compile actions, execution observations and aggregate grades'),
             ('06', 'Configured tasks; inference disabled' if enrolled else 'Enrollment and release gates',
              'Paired recovery and generation conditions; independent validity and release pending' if enrolled else
              'Development controls add no configured model samples or scores')]
    return '<ol class="workflow" aria-label="Matched compiler and language baseline workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol>'


def workflow():
    steps = [('01', 'Frozen benchmark cases', 'Public ELF, language contract, prompt, tools and budgets'),
             ('02', 'Model + client', 'DeepSeek Harness; GPT in Codex; GLM in ZCode'),
             ('03', 'Ghidra evidence', 'Restricted functions, assembly, xrefs, strings and bytes'),
             ('04', 'Native grading', 'Unchanged source → isolated compiler → hidden exact checks'),
             ('05', 'Saved records', 'Answer, evidence, usage, grades, source reviews and pricing'),
             ('06', 'Global HTML report', 'make reports → charts, case pages, JSON, CSV and portable ZIP')]
    return '<ol class="workflow" aria-label="Benchmark and reporting workflow">' + ''.join(
        '<li><span>' + number + '</span><strong>' + title + '</strong><p>' + esc(description) + '</p></li>'
        for number, title, description in steps) + '</ol><p class="chart-note">Model inference ends before grading; hidden tests and compiler feedback never return to the model. Steps 01–04 run only through an explicitly authorized benchmark command. Report generation reads step 05 offline and never launches another experiment.</p>'
