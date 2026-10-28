"""Shared reader-facing views of saved, sanitized benchmark measurements.

No file discovery, inference, regrading or private evidence is performed here.
"""
from collections import Counter
from statistics import median
from html import escape
import math
import report_budget
import localization_report

import report_charts as charts


# Categories describe tasks, not providers or execution generations. Add future
# families here; an empty category is a roadmap entry, never a measured zero.
CAPABILITY_CATEGORIES = (
    ('native', 'Native languages', 'Whole-program C++, Rust and Go behavior, including compiler/container variants.'),
    ('localization', 'Localization', 'Function and entry-point localization against frozen targets.'),
    ('reconstruction', 'Reconstruction', 'C/C++/Rust/Go reconstruction and matched code-generation controls.'),
    ('types', 'Types / ABI', 'Scalar types, object layout, pointers, variants and calling conventions.'),
    ('unpacking', 'Unpacking', 'Planned category; no configured cases or measured scores yet.'),
)
GRADED_STATES = {'graded', 'completed_over_budget', 'completed_queue_held'}


def valid_grade(row):
    """Use admitted saved grades, including genuine zero scores, never partial output."""
    grade = row.get('grade')
    if (not grade or row.get('status') not in GRADED_STATES
            or row.get('record_valid') is False or row.get('failure_receipt_malformed')):
        return False
    passed, total = grade.get('passed'), grade.get('total')
    return (type(passed) in (int, float) and type(total) in (int, float)
            and math.isfinite(passed) and math.isfinite(total) and 0 <= passed <= total and total > 0)


def aggregate_native_rows(data):
    """One saved result per configured primary cell, across admitted conditions.

    The report collector has already checked source/grade bindings. Prefer the
    selected grade in the active condition, then the saved condition order and
    its selected attempt. Never choose by score. A later ungraded failure does
    not erase an earlier valid grade. Retired/diagnostic cases stay out of scope.
    """
    routing = data.get('conditions', {}).get('active_case_conditions', {})
    alternate = data.get('condition_records', [])
    current = [r for r in alternate if r.get('attempt_current', True)
               and r.get('condition') == routing.get(r['case']['suite'], 'lossless-evidence-v1')]
    planned = current or [r for r in data.get('records', []) if r.get('role') == 'primary']
    key = lambda r: (r['model'], r['experiment'], r['case']['id'])
    cells = {key(r): r for r in planned if r.get('role') == 'primary'}
    order = {condition: i for i, condition in enumerate(dict.fromkeys(r.get('condition') for r in alternate))}
    candidates = {}
    for row in data.get('records', []) + alternate:
        identity = key(row)
        if identity not in cells or row.get('role') != 'primary':
            continue
        # Compiler variants are separate cells. Do not reuse a grade whose
        # recorded target/output contract differs from that cell.
        reference = cells[identity]
        if (row['case']['suite'] != reference['case']['suite']
                or row['case']['language'] != reference['case']['language']
                or row.get('output_language') != reference.get('output_language')
                or row.get('target_build') != reference.get('target_build')):
            continue
        if valid_grade(row) and row.get('receipt'):
            candidates.setdefault(identity, []).append(row)
    result = []
    for identity, reference in cells.items():
        values = candidates.get(identity, [])
        rank = lambda r: (r.get('condition') == reference.get('condition'),
                          order.get(r.get('condition'), -1), r.get('attempt_current', True),
                          r.get('attempt_id') or '')
        chosen = max(values, key=rank) if values else reference
        # Invalid grade data must not leak into downstream summary() calculations.
        result.append(chosen if valid_grade(chosen) else {**chosen, 'grade': None, 'program_success': False})
    return result


def typed_category(cell):
    prefixes = {str(cap)[:1] for cap in cell.get('capability_ids', [])}
    return {frozenset({'L'}): 'localization', frozenset({'C'}): 'reconstruction',
            frozenset({'T'}): 'types', frozenset({'U'}): 'unpacking'}.get(frozenset(prefixes), 'other')


def aggregate_cells(data):
    """Merge valid caches without changing their evidence or runtime identity."""
    cells = []
    for row in aggregate_native_rows(data):
        cells.append({'key': (row['model'], row['experiment'], row['case']['id']),
                      'model': row['model'], 'category': 'native', 'row': row,
                      'label': row['experiment_label'] + ' / ' + row['case']['label'],
                      'grade': row.get('grade'), 'status': row['status'],
                      'success': bool(row.get('grade') and row.get('program_success'))})
    value = data.get('capability_measurements', {})
    registered = {r['attempt_id']: r.get('registered_at', '')
                  for r in data.get('capability_registration', {}).get('observations', [])}
    # Evidence packaging and runtime may evolve; the frozen target and task
    # contract must still match. Missing or changed identities are not cache hits.
    fields = ('model', 'task_id', 'family_id', 'profile_id', 'build_id', 'binary_sha256',
              'public_package_sha256', 'source_language', 'output_language', 'hint_profile', 'capability_ids')
    identity = lambda r: tuple(tuple(r.get(k, [])) if k == 'capability_ids' else r.get(k) for k in fields)
    history = {}
    for row in localization_report.history(data):
        if row.get('record_valid'):
            history.setdefault(identity(row), {})[row['attempt_id']] = row
    seen = set()
    for cell in value.get('cells', []):
        key = (cell['model'], 'capabilities', cell['task_id'])
        if key in seen:
            continue
        seen.add(key)
        current = cell.get('observation')
        available = list(history.get(identity(cell), {}).values()) if all(cell.get(k) for k in
            ('task_id', 'build_id', 'binary_sha256', 'public_package_sha256')) else []
        eligible = [r for r in available if valid_grade(r)]
        rank = lambda r: (bool(current and current['attempt_id'] == r['attempt_id']),
                          registered.get(r['attempt_id'], ''), r['attempt_id'])
        selected = max(eligible or available, key=rank) if eligible or available else None
        grade = selected['grade'] if selected and valid_grade(selected) else None
        cells.append({'key': key, 'model': cell['model'], 'category': typed_category(cell),
                      'row': selected or cell, 'label': cell['family_id'] + ' / ' + cell['profile_id'],
                      'grade': grade, 'status': selected['status'] if selected else 'not_started',
                      'success': bool(grade and grade['task_success'])})
    return cells


def aggregate_stats(cells, catalog):
    result = []
    for model, info in catalog.items():
        selected = [c for c in cells if c['model'] == model]
        graded = [c for c in selected if c['grade']]
        states = Counter('graded' if c['grade'] else c['status'] for c in selected)
        result.append({'model': model, 'model_label': info['label'], 'planned_cells': len(selected),
                       'graded_attempts': len(graded), 'passed_cells': sum(c['success'] for c in graded),
                       'graded_over_budget': sum(c['status'] == 'completed_over_budget' for c in graded),
                       'statuses': states,
                       'score': sum(100 * c['grade']['passed'] / c['grade']['total'] for c in graded) / len(graded) if graded else None})
    return result


def capability_view(data):
    """Derived presentation only; preserve the serialized original observations."""
    from capabilities import measurements, metrics
    source = data['capability_measurements']
    chosen = {c['key']: c for c in aggregate_cells(data) if c['category'] != 'native'}
    cells = []
    for original in source['cells']:
        cell = dict(original)
        selected = chosen.get((cell['model'], 'capabilities', cell['task_id']))
        if selected and 'attempt_id' in selected['row']:
            cell.update(observation=selected['row'], status=selected['status'])
        cells.append(cell)
    saved = localization_report.history(data)
    return {**source, 'cells': cells, 'history': saved, 'saved_observations': len(saved),
            'statuses': dict(Counter(c['status'] for c in cells)),
            'totals': measurements.totals(cells, saved),
            'macro_vectors': metrics.macro_vectors(cells, data['models'])}


def aggregate_dimensions(cells, catalog):
    categories = [category for category in CAPABILITY_CATEGORIES if any(c['category'] == category[0] for c in cells)]
    models = []
    for model in catalog:
        values = []
        for category, _, _ in categories:
            selected = [c for c in cells if c['model'] == model and c['category'] == category and c['grade']]
            values.append({'score': sum(100 * c['grade']['passed'] / c['grade']['total'] for c in selected) / len(selected) if selected else None,
                           'n': len(selected), 'checks': sum(c['grade']['total'] for c in selected),
                           'members': [c['label'] + ' [' + str(c['key'][-1]) + ']' for c in selected]})
        models.append({'model': model, 'values': values})
    return [{'id': 'capability-overview', 'label': 'Capability overview',
             'note': 'Each axis is the case-equal mean of saved correctness scores (0–100). n counts unique graded cases. Valid cached grades contribute once regardless of run date. Models may cover different subsets and budgets; this is descriptive coverage, not a controlled ranking. Unmeasured axes are not zeros.',
             'axes': [{'id': key, 'label': label} for key, label, _ in categories], 'models': models}]


def text(value):
    return escape(str(value), quote=True)


def count(value):
    return 'Unknown' if value is None else f'{value:,}'


def known_sum(values):
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def case_identity(row):
    case = row['case']
    build = row.get('target_build') or {}
    parts = [row['experiment_label'], case['label'], case.get('language', ''),
             'output ' + str(row.get('output_language') or case.get('language') or 'unknown')]
    for field in ('compiler_family', 'compiler_version', 'optimization', 'standard', 'string_abi'):
        if build.get(field) is not None:
            parts.append(field.replace('_', ' ') + ': ' + str(build[field]))
    if not build:
        parts.append('original compiler metadata unavailable')
    return ' · '.join(parts)


def readability_note():
    return ('<p class="chart-note">Readability is a stored coordinating-AI assessment, not an independent blind panel. '
            'Five dimensions score 0–2 each: names, decomposition, language idioms, formatting and comments. '
            'Only reviewed accepted source contributes; no confidence interval or causal ranking is claimed. '
            'Structured type/localization artifacts: not applicable.</p>')


def cohorts(rows, catalog):
    """Explicit small model groups, avoiding a combinatorial hidden-page matrix."""
    models = [m for m in catalog if any(r['model'] == m for r in rows)]
    result = [('all-models', 'All configured models', models)]
    coverage = {m: sum(bool(r['grade']) for r in rows if r['model'] == m) for m in models}
    maximum = max(coverage.values(), default=0)
    fullest = [m for m in models if coverage[m] == maximum]
    if maximum and len(fullest) >= 2 and fullest != models:
        result.append(('maximum-coverage', 'Models with the largest graded coverage', fullest))
    for harness in sorted({catalog[m]['harness'] for m in models}):
        members = [m for m in models if catalog[m]['harness'] == harness]
        if len(members) >= 2 and members != models and members != fullest:
            result.append((harness, harness + ' models', members))
    return result


def dimension_groups(rows, catalog, compiler_points=()):
    """Descriptive profiles from saved grades, with a separate denominator per axis."""
    def metadata(row, key):
        return (row.get('target_build') or {}).get(key, row['case'].get(key))

    def observation(row, point=None):
        grade = row.get('grade')
        if point is not None:
            grade = (grade or {}).get('points', {}).get(point)
        if not grade or not grade.get('total'):
            return None
        return 100 * grade['passed'] / grade['total'], grade['total']

    def member(row):
        return str(row.get('experiment') or 'unknown') + ' / ' + str(row['case']['id']) + ' / ' + str(row.get('condition') or 'unknown condition')

    groups = []
    def add(key, label, axes, note, measure=observation):
        models = []
        for model in catalog:
            values = []
            for _, _, select in axes:
                samples = [(row, measure(row)) for row in rows if row['model'] == model and select(row)]
                samples = [(row, value) for row, value in samples if value is not None]
                values.append({'score': sum(value[0] for _, value in samples) / len(samples) if samples else None,
                               'n': len(samples), 'checks': sum(value[1] for _, value in samples) if samples and all(value[1] is not None for _, value in samples) else None,
                               'members': [member(row) for row, _ in samples]})
            models.append({'model': model, 'values': values})
        groups.append({'id': key, 'label': label, 'note': note,
                       'axes': [{'id': key, 'label': name} for key, name, _ in axes], 'models': models})

    correctness = '0–100% case-equal mean of saved check pass rates; n is graded cases on each axis. Checks are shown for context, not used as weights. Missing grades are excluded, not scored zero.'
    add('language', 'Source-language recovery',
        [(key, label, lambda r, key=key: r['case']['language'] == key) for key, label in [('cpp', 'C++'), ('rust', 'Rust'), ('go', 'Go')]],
        correctness + ' Languages have different case mixes; this describes the sampled workloads.')
    suites = {'record-parser': 'Basic parser', 'complex-native-v1': 'Multipoint programs', 'stl-native-v2': 'STL + Rust byte inspection'}
    suites.update({r['case']['suite']: r['case']['suite'] for r in rows if r['case']['suite'] not in suites})
    add('task-family', 'Task families',
        [(key, label, lambda r, key=key: r['case']['suite'] == key) for key, label in suites.items()], correctness)
    # Each handler's own checks, not the surrounding program's total score.
    behavior = []
    for key, label in [('P0', 'Text counts'), ('P1', 'Stateful ledger'), ('P2', 'Stable ranking'),
                       ('P3', 'Dependencies'), ('P4', 'ABI behavior'), ('P5', 'Policy dispatch')]:
        point_group_start = len(groups)
        add('point', label, [(key, label, lambda r: r['case']['suite'] == 'complex-native-v1' or
            (r['case']['suite'] == 'stl-native-v2' and r['case']['language'] == 'rust'))], '',
            measure=lambda r, key=key: observation(r, key))
        behavior.append(groups.pop(point_group_start))
    groups.append({'id': 'behavior', 'label': 'Behavioral subskills',
                   'note': '0–100% mean of each handler’s saved check pass rate, equally weighted per case. n is observed handlers. Multipoint-suite and Rust byte-inspection P0–P5 checks contribute. Whole-program failures without a saved handler breakdown are unmeasured on these axes; axis coverage can differ even in the shared-case cohort. Repeated Rust/tool and language conditions are correlated. Behavior scores do not prove recovery of the original data structures or binary-compatible ABI.',
                   'axes': [g['axes'][0] for g in behavior],
                   'models': [{'model': model, 'values': [next(m for m in g['models'] if m['model'] == model)['values'][0] for g in behavior]} for model in catalog]})
    add('cpp-standard-abi', 'C++ dialect and string ABI',
        [(key, label, lambda r, field=field, value=value: r['case']['language'] == 'cpp' and metadata(r, field) == value)
         for key, label, field, value in [('c++11', 'C++11', 'standard', 'c++11'), ('c++14', 'C++14', 'standard', 'c++14'),
                                          ('abi0', 'String ABI 0', 'string_abi', 0), ('abi1', 'String ABI 1', 'string_abi', 1)]],
        correctness + ' Axes overlap. These are behavior-tested conditions, not binary-compatibility certification.')
    builds = [r.get('target_build') or {} for r in rows] + list(compiler_points)
    families = sorted({b['compiler_family'] for b in builds if b.get('compiler_family')})
    opts = sorted({b['optimization'] for b in builds if b.get('optimization')})
    add('build-conditions', 'Compiler and optimization conditions',
        [('compiler:' + family, family, lambda r, family=family: metadata(r, 'compiler_family') == family) for family in families]
        + [('optimization:' + opt, '-' + opt, lambda r, opt=opt: metadata(r, 'optimization') == opt) for opt in opts],
        correctness + ' Only explicit build metadata contributes. Configured build points can be unmeasured. Family-level aggregates may mix versions, language standards and programs; this is not an isolated compiler or optimization effect.')
    # A readability review is a separate stored judgment, independent of whether
    # every behavioral check passed; never impute a missing criterion as zero.
    readability = []
    for key, label in [('names', 'Names'), ('decomposition', 'Decomposition'), ('language_idioms', 'Idioms'),
                       ('formatting', 'Formatting'), ('explanatory_comments', 'Comments')]:
        add('review', label, [(key, label, lambda r: True)], '',
            measure=lambda r, key=key: (50 * r['readability']['scores'][key], None)
            if (r.get('readability') or {}).get('scores', {}).get(key) is not None else None)
        readability.append(groups.pop())
    groups.append({'id': 'readability', 'label': 'Readability criteria',
                   'note': 'Each stored 0–2 criterion is scaled to 0–100%; n is reviews with that criterion. Coordinating-AI assessments, not an independent blind panel. These are source-quality judgments, separate from correctness.',
                   'axes': [g['axes'][0] for g in readability],
                   'models': [{'model': model, 'values': [next(m for m in g['models'] if m['model'] == model)['values'][0] for g in readability]} for model in catalog]})
    return groups


def comparison(report, dimensions_only=False):
    from report import summary, table
    rows = report.current_records()
    catalog = report.data['models']
    all_cases = {(r['experiment'], r['case']['id']): r for r in rows}
    groups = cohorts(rows, catalog)
    body = '<section id="comparison"><h2>' + ('Model capability scores across dimensions' if dimensions_only else 'Native reconstruction: compare model + harness setups') + '</h2>'
    body += '<p>Configured frozen cases with valid saved grades from all runs, deduplicated per model × case. Different harnesses, budgets and providers limit model-only attribution.</p>'
    default_group = next((key for key, _, _ in groups if key == 'maximum-coverage'), 'all-models') if dimensions_only else 'all-models'
    body += '<label>Model group <select data-cohort-selection>' + ''.join(
        '<option value="' + text(key) + '"' + (' selected' if key == default_group else '') + '>' + text(label) + '</option>' for key, label, _ in groups) + '</select></label>'
    for index, (key, label, members) in enumerate(groups):
        selected = [r for r in rows if r['model'] in members]
        sets = {m: {(r['experiment'], r['case']['id']) for r in selected if r['model'] == m and r['grade']} for m in members}
        shared = set.intersection(*sets.values()) if sets else set()
        matched = [r for r in selected if (r['experiment'], r['case']['id']) in shared]
        cohort = {m: catalog[m] for m in members}
        scope = f'{len(members)} models · shared {len(shared)}/{len(all_cases)} native cases'
        names = ', '.join(catalog[m]['label'] for m in members)
        body += '<div data-cohort-panel="' + text(key) + '"' + (' hidden' if key != default_group else '') + '>'
        body += '<h3>' + text(scope) + '</h3><p>' + text(names) + '</p>'
        body += '<div class="chart-tabs" role="group" aria-label="Comparison scope"><button type="button" data-chart-scope="matched" aria-pressed="true">Shared graded cases (' + str(len(shared)) + ')</button><button type="button" data-chart-scope="all" aria-pressed="false">Each model’s own graded subset</button></div>'
        for name, current in [('matched', matched), ('all', selected)]:
            context = scope if name == 'matched' else label + ' · unequal graded subsets; not a common-case ranking'
            context += ' · request budgets not matched'
            keys = shared if name == 'matched' else set(all_cases)
            body += '<div data-chart-panel="' + name + '"' + (' hidden' if name == 'all' else '') + '>'
            if not current:
                body += '<p class="notice">No common graded cases for this model group. Choose another model group or explicitly view each model’s own subset. No zero-valued comparison is inferred.</p>'
                body += table(['Model', 'Graded cases / configured cases'], [[text(catalog[m]['label']), f'{len(sets[m])}/{len(all_cases)}'] for m in members])
            else:
                labels = [all_cases[k]['experiment_label'] + ' / ' + all_cases[k]['case']['label'] for k in sorted(keys)]
                context += ' · ' + ('; '.join(labels) if len(labels) <= 3 else str(len(labels)) + ' cases; membership below')
                context += ' · snapshot ' + str(report.data.get('data_as_of') or 'unknown') + ' · build ' + str(getattr(report, 'build_id', 'unknown'))[:12]
                body += report.summary_table(current)
                body += report_budget.comparison_note(current, cohort)
                body += '<p class="chart-note">Fixed-test pass rate weights by check count. Case-equal mean weights each graded case equally. These are distinct descriptions of the same saved grades; checks are not independent trials.</p>'
                if dimensions_only:
                    body += charts.capability_dimensions(dimension_groups(current, cohort, report.data.get('compiler_points', [])), cohort, scope=context)
                    body += '<p class="chart-note">These are native-language details. The <a href="index.html#capability-radar">overview radar</a> also includes observed localization, reconstruction and type/ABI correctness. See <a href="capabilities.html">typed task evidence and measurement gaps</a>.</p>'
                else:
                    body += '<p><a href="dimensions.html?cohort=' + text(key) + '&amp;scope=' + name + '#comparison">Capability diagrams by language, behavior, build conditions and readability →</a></p>'
                body += '<details><summary>Included cases, exclusions and check weights</summary>'
                body += table(['Case', 'Included', 'Checks per graded attempt'], [[text(all_cases[k]['experiment_label'] + ' / ' + all_cases[k]['case']['label']), 'Yes' if k in keys else 'No shared grade',
                    text(', '.join(sorted({str(r['grade']['total']) for r in selected if (r['experiment'], r['case']['id']) == k and r['grade']})) or 'Not graded')]
                    for k in sorted(all_cases)]) + '</details>'
                if dimensions_only:
                    body += '</div>'
                    continue
                stats = summary(current, cohort)
                body += '<details class="comparison-charts"><summary>Correctness, readability, runtime, tokens and cost charts</summary>'
                body += charts.score_charts(stats, scope=context,
                    structurally_single_point=all(r['points_total'] == 1 for r in current if r['grade']))
                body += readability_note() + charts.runtime_chart(stats, scope=context)
                body += charts.token_chart(current, cohort, scope=context) + charts.cost_chart(stats, scope=context) + '</details>'
            body += '</div>'
        body += '</div>'
    return body + '</section>'


def overview(report):
    from report import table
    catalog = report.data['models']
    cells = aggregate_cells(report.data)
    stats = aggregate_stats(cells, catalog)
    graded = sum(s['graded_attempts'] for s in stats)
    passed = sum(s['passed_cells'] for s in stats)
    unique = len({c['key'][1:] for c in cells})
    body = '<section id="status"><h2>Benchmark overview</h2><p>All configured models and capability categories. Valid saved grades and caches count once per model × case, regardless of when they were produced.</p><div class="cards">'
    for value, label in [(str(unique), 'Unique configured cases'),
                         (f'{graded}/{len(cells)}', 'Graded / planned cells'),
                         (str(passed), 'All correctness checks passed'),
                         (str(graded - passed), 'Graded below full marks'),
                         (str(len(cells) - graded), 'Ungraded cells')]:
        body += '<div class="card"><strong>' + text(value) + '</strong>' + text(label) + '</div>'
    body += '</div><p>' + str(unique) + ' cases × ' + str(len(catalog)) + ' configured models = ' + str(len(cells)) + ' planned cells. The latest command is only a subset of this catalogue. Ungraded cells are not correctness zeros; complete answers that exceeded a budget still retain their correctness score.</p></section>'
    body += '<section id="capability-radar"><h2>Capability radar</h2>' + localization_note(report.data)
    body += charts.capability_dimensions(aggregate_dimensions(cells, catalog), catalog,
        scope='All valid saved grades · one result per model × case · no date-based split', radar_first=True)
    body += '<div class="cards category-cards">'
    for category, label, note in CAPABILITY_CATEGORIES:
        selected = [c for c in cells if c['category'] == category]
        n = sum(bool(c['grade']) for c in selected)
        size = len({c['key'][1:] for c in selected})
        body += '<div class="card"><h3>' + text(label) + '</h3><strong>' + (f'{n}/{len(selected)}' if selected else 'Unmeasured') + '</strong><p>' + (str(size) + ' cases per model · graded / planned cells' if selected else 'No cases configured; excluded from score and coverage denominators.') + '</p><small>' + text(note) + '</small></div>'
    body += '</div><p><a href="dimensions.html">Language, behavior and compiler detail →</a> · <a href="capabilities.html">Typed task scores and evidence →</a></p></section>'
    body += '<section id="coverage"><h2>Model coverage</h2>'
    body += charts.completion(stats, scope='Native languages + localization + reconstruction + types / ABI', title='Completion coverage across all capability categories')
    body += table(['Model', 'Graded / planned', 'Full marks', 'Below full marks', 'Ungraded', 'Graded over budget¹', 'Case-equal correctness'], [
        [text(s['model_label']), f'{s["graded_attempts"]}/{s["planned_cells"]}', s['passed_cells'],
         s['graded_attempts'] - s['passed_cells'], s['planned_cells'] - s['graded_attempts'],
         s['graded_over_budget'],
         'Unmeasured' if s['score'] is None else f'{s["score"]:.1f}%'] for s in stats])
    body += '<p class="chart-note">¹ Already included in graded counts; complete-answer correctness and spending compliance are separate.</p>'
    body += '<details id="aggregation-policy"><summary>How valid caches are counted</summary><p>Only grades admitted by the saved report collector contribute. Native results must match the configured case, language, output and target build. Typed results must match the frozen binary, package and task contract and have a valid saved record. Invalid records, incomplete answers, diagnostic-only experiments and duplicate copies cannot add a grade.</p><p>One grade per model × case: prefer its selected grade; otherwise use an eligible saved grade, ordered by registered condition/attempt evidence, never by highest score. An ungraded later attempt does not erase a valid grade. Conditions, budgets and execution identities remain available in the evidence pages; inclusion does not claim identical budgets or certified model-only rankings. Real deadline non-passes and format rejections remain measured zeros. Expenditure counts all unique attempts separately, including failed ones.</p></details></section>'
    timing = report.data.get('runtime_performance', {}).get('benchmark')
    if timing:
        body += '<section id="latest-run"><h2>Latest command</h2><p>' + text(timing['status'].replace('_', ' ')) + ' · ' + str(timing['selected_tasks']) + ' selected cells · ' + str(timing['provider_launches']) + ' task launches (not API request count) · ' + f'{timing["total_seconds"]:.1f}' + ' seconds elapsed.</p><p>Command-level API calls: ' + count(timing.get('model_api_calls')) + '. Saved request usage is listed under <a href="costs.html">Tokens &amp; cost</a>.</p><p><a href="diagnostics.html#local-performance">Per-stage timings and scope</a></p></section>'
    body += '<section><h2>Explore the evidence</h2><p><a href="cases/index.html">Native cases and outcomes</a> · <a href="capabilities.html">Typed capability attempts</a> · <a href="costs.html">Both tracks’ known expenditure</a> · <a href="history/index.html#conditions">Original and fixed harness history</a></p><p><a href="measurement.html#task-budgets">Task spending limits</a> · <a href="diagnostics.html#environment">Harness fairness and diagnostics</a> · <a href="methodology.html">Methods and measurement limits</a></p></section>'
    report.page('index.html', 'Global benchmark summary', body)
    report.page('comparison.html', 'Native model comparison', comparison(report))
    report.page('dimensions.html', 'Model capability profiles', comparison(report, dimensions_only=True))


def typed_outcome(row):
    grade = row.get('grade')
    if not grade:
        state = {'localization_format_review': 'Localization format needs review; semantic result ungraded',
                 'budget_exhausted': 'Budget stopped; ungraded', 'environment_failure': 'Environment failure; ungraded',
                 'completed_over_budget': 'Complete answer; budget exceeded / queue held; grading pending',
                 'not_started': 'Not started', 'interrupted': 'Interrupted; ungraded'}.get(row['status'], row['status'].replace('_', ' '))
        return state, 'No recorded semantic result', 'Not graded (n=0)'
    if not grade['artifact_valid']:
        state = 'Submission format rejected'
    elif typed_category(row) == 'localization':
        state = 'All localization checks passed' if grade['task_success'] else 'Confirmed localization mismatch'
    else:
        state = 'All checks passed' if grade['task_success'] else 'Valid artifact; checks not all passed'
    semantic = 'Not executed' if grade['return_provenance'] == 'not_executed' else 'Recorded return evidence: ' + grade['return_provenance'].replace('_', ' ')
    budget_label = report_budget.outcome_label(row.get('budget_outcome'))
    if budget_label:
        state += '; ' + budget_label
    return state, semantic, f'{grade["passed"]}/{grade["total"]} strict task points'


def capabilities(report):
    from report import table, money
    value = capability_view(report.data)
    registration = report.data['capability_registration']
    relations = {r['attempt_id']: r for r in registration['observations']}
    history = sorted(localization_report.history(report.data), key=lambda r: (relations.get(r['attempt_id'], {}).get('registered_at', ''), r['attempt_id']), reverse=True)
    latest = max(registration['schedules'], key=lambda r: r['registered_at'], default=None)
    cells = [cell for cell in aggregate_cells(report.data) if cell['category'] != 'native']
    stats = aggregate_stats(cells, report.data['models'])
    body = '<section id="status"><h2>Typed capability outcomes</h2><p>All valid saved grades count once per model × frozen task, including reusable results from earlier runs. Preparation status is not a correctness score.</p>'
    body += localization_note(report.data)
    body += '<dl class="coverage-cards">' + ''.join('<div class="coverage-card"><dt>' + text(label) + '</dt><dd>' + str(number) + '</dd></div>'
        for label, number in [('Configured cells', len(cells)), ('Graded cells', sum(s['graded_attempts'] for s in stats)),
                              ('All checks passed', sum(s['passed_cells'] for s in stats)),
                              ('Ungraded cells', sum(s['planned_cells'] - s['graded_attempts'] for s in stats))]) + '</dl>'
    body += table(['Model', 'Graded / configured', 'Full marks', 'Below full marks', 'Ungraded'], [
        [text(s['model_label']), f'{s["graded_attempts"]}/{s["planned_cells"]}', s['passed_cells'],
         s['graded_attempts'] - s['passed_cells'], s['planned_cells'] - s['graded_attempts']] for s in stats])
    body += '<p>These are observed correctness results, not a certified model-only ranking. <a href="index.html#capability-radar">Overall capability radar</a> · <a href="#latest-registration">Latest registration</a> · <a href="#observations">Attempt evidence</a> · <a href="capability-inventory.html">Search all configured cells, contracts and build evidence</a></p></section>'
    body += '<p class="notice">Request-limit conditions are reported per attempt. Codex OAuth observations have no provider response cap; their observed token thresholds are not equivalent to capped routes. Unidentified historical budgets remain unknown. See <a href="measurement.html#task-budgets">route-specific task limits</a>.</p>'
    body += '<p>Correctness scores can include complete final answers that exceeded an aggregate budget. Those attempts remain labelled budget exceeded / queue held; a correctness grade does not certify budget compliance.</p>'
    if latest:
        selected = [r for r in history if relations.get(r['attempt_id'], {}).get('registration_id') == latest['registration_id']]
        body += '<section id="latest-registration"><h2>Latest saved registration</h2><p>' + text(latest['registered_at']) + ' · ' + str(latest['scheduled_cells']) + ' registered cells · ' + str(len(selected)) + ' saved observations associated with this registration. Dispatch intents do not by themselves prove an API request.</p><p><a href="capability-inventory.html#registration">Registration and binding evidence</a>. Earlier or pre-registration command failures are shown separately on <a href="diagnostics.html#local-performance">Timings</a>.</p></section>'
    body += '<section id="observations"><h2>Attempt evidence</h2><p>Newest registration first; registration time is not inferred completion time. Multiple attempts for one task contribute only one grade to the overview. Strict task points and semantic execution are separate. Open an attempt for its condition, evidence and accounting.</p>'
    rows = []
    for row in history:
        state, semantic, score = typed_outcome(row)
        path = 'attempts/' + row['attempt_id'] + '.html'
        rows.append(['<a href="' + path + '">' + text(row['attempt_id'][:12]) + '</a><small>' + text(row['task_id']) + '</small>',
                     text(report.data['models'].get(row['model'], {}).get('label', row['model'])), text(state),
                     text(score) + '<small>Semantic checks: ' + text(semantic) + '</small>',
                     count(row['known_tokens'].get('totalTokens')), money(row['cost'])])
        relationship = relations.get(row['attempt_id'], {})
        details = '<section id="outcome"><h2>' + text(state) + '</h2><p>' + text(score) + '. Semantic checks: <strong>' + text(semantic) + '</strong>.</p><p><a href="../capabilities.html#observations">All saved attempts</a> · <a href="../capability-inventory.html#typed-cells">Configured task catalogue</a></p></section>'
        revision = row.get('localization_revision')
        if revision:
            details += '<section><h2>Offline localization regrade</h2><p>Original answer and original grade are preserved. Only exact numeric representations were normalized; addresses and expected locations were not repaired. Format uncertainty is excluded from correctness scores.</p>'
            details += table(['Field', 'Saved value'], [[text(label), text(revision[key])] for label, key in (
                ('Original answer SHA256', 'answer_sha256'), ('Original grading revision', 'original_grading_id'),
                ('Offline grading revision', 'revision_id'), ('Exact representation changes', 'transforms'))]) + '</section>'
        details += '<section id="identity"><h2>Task, condition and provenance</h2>' + table(['Field', 'Saved value'], [[text(label), text(row.get(key) or 'Unknown')] for key, label in (
            ('attempt_id', 'Full attempt identity'), ('model', 'Model'), ('task_id', 'Public task'), ('profile_id', 'Task condition'),
            ('analysis_condition', 'Evidence condition'), ('source_language', 'Source language'), ('output_language', 'Output language (empty means structured artifact)'),
            ('runtime_status', 'Runtime identity'), ('budget_status', 'Request-limit evidence'), ('usage_status', 'Usage completeness'))])
        details += report_budget.detail(report.data.get('typed_budget_conditions', {}).get(row['attempt_id']))
        details += table(['Registration field', 'Saved value'], [[text(k.replace('_', ' ')), text(v or 'Unknown')] for k, v in relationship.items() if k != 'attempt_id']) + '</section>'
        budget_reason = (row.get('budget_outcome') or {}).get('queue_stop_reason')
        stop_note = ' Saved queue-stop reason: ' + text(budget_reason) + '.' if budget_reason else ' Detailed stop reason and remaining budget are not recorded in this sanitized snapshot and are not inferred.'
        details += '<section id="cost"><h2>Observed usage and cost</h2>' + table(['Category', 'Known tokens'], [[text(k), count(v)] for k, v in row['known_tokens'].items()]) + '<p>' + money(row['cost']) + '. Published-rate estimate; actual billed amount unknown.' + stop_note + '</p></section>'
        review = row['readability']
        details += '<section><h2>Readability</h2><p>' + ('Not applicable to this structured artifact.' if not review['applicable'] else 'Not reviewed.' if review['total'] is None else text(review['total']) + '/10.') + ' Provenance: ' + text(review['provenance']) + '</p>' + readability_note() + '</section>'
        if row['grade']:
            details += '<section id="metrics"><h2>Capability scores</h2>' + table(['Capability', 'Strict score', 'Semantic execution'], [[text(k), 'Unmeasured' if v['score'] is None else f'{100*v["score"]:.2f}%', text(semantic)] for k, v in row['grade']['scores'].items()]) + '</section>'
        report.page(path, 'Typed attempt ' + row['attempt_id'][:12], details)
    body += table(['Attempt / task', 'Model', 'Outcome', 'Strict score and execution', 'Known tokens', 'Estimated USD'], rows, 'typed-history') + '</section>'
    measured = [r for r in value['macro_vectors'] if r['metric_observations']]
    body += '<section id="metrics"><h2>Current capability metrics</h2><p>' + (str(len(measured)) + ' macro rows have metric observations; see the catalogue for definitions and denominators.' if measured else 'No current metric observations. Unmeasured capability macros are not shown as hundreds of zero scores.') + '</p></section>'
    body += report.capability_control_section(value)
    report.page('capabilities.html', 'Typed capability outcomes', body)
    report.capability_inventory_page()


def localization_note(data):
    overlay = data.get('localization_regrades')
    if not overlay:
        return '<p>Localization format rejections are ungraded; only admitted answers contribute semantic correctness scores.</p>'
    return ('<p>Localization format rejections are ungraded; only admitted answers contribute semantic correctness scores.</p>'
            '<p>Offline localization regrade updated at <code>' + text(overlay['updated_at']) + '</code>. '
            'The original report snapshot time is unchanged. Original answers and grades remain preserved.</p>')


def expenditure(report):
    """One entry per saved native cell/typed attempt; grade revisions add none."""
    entries = {}
    active = {(r['experiment'], r['case']['id'], r['model'], r['condition']) for r in report.current_records()}
    for r in report.all_records():
        if r['receipt']:
            key = (r['experiment'], r['case']['id'], r['model'], r['condition'])
            entries[('native',) + key] = {'model': r['model'], 'track': 'Native reconstruction', 'current': key in active,
                'tokens': r['receipt'].get('reported_usage', {}).get('totalTokens'), 'cost': r['cost']}
    for r in localization_report.history(report.data):
        entries[('typed', r['attempt_id'])] = {'model': r['model'], 'track': 'Typed capabilities', 'current': False,
            'tokens': r['known_tokens'].get('totalTokens'), 'cost': r['cost']}
    return list(entries.values())


def cost_sum(rows):
    known = [r['cost'] for r in rows if r['cost'].get('low_usd') is not None]
    return {'low_usd': sum(c['low_usd'] for c in known) if known else None,
            'high_usd': sum(c['high_usd'] for c in known) if known else None,
            'usage_complete': bool(rows) and all(r['cost'].get('usage_complete') for r in rows)}


def costs(report):
    from report import table, money
    import report_prices
    ledger = expenditure(report)
    body = '<section id="expenditure"><h2>Known expenditure across both tracks</h2><p>Cumulative saved native observations and unique typed attempts, including historical conditions, interrupted and failed attempts. Native identity is the experiment/case/model/condition cell; typed identity is the saved attempt ID. Repeated rows, reused caches and grading revisions add no charges. Estimates use published rates; <strong>actual billed amounts are unknown</strong>.</p>'
    totals = []
    for model, spec in report.data['models'].items():
        rows = [r for r in ledger if r['model'] == model]
        if not rows:
            continue
        native = [r for r in rows if r['track'] == 'Native reconstruction']
        typed = [r for r in rows if r['track'] == 'Typed capabilities']
        totals.append([text(spec['label']), f'{len(native)} / {len(typed)}', money(cost_sum(native)), money(cost_sum(typed)), money(cost_sum(rows)), count(known_sum(r['tokens'] for r in rows)) + '<small>' + str(sum(r['tokens'] is not None for r in rows)) + '/' + str(len(rows)) + ' totals reported</small>', str(sum(not r['cost'].get('usage_complete') for r in rows))])
    body += table(['Model', 'Native / typed attempts', 'Native estimate', 'Typed estimate', 'Combined known estimate', 'Known tokens', 'Incomplete attempts'], totals) + '<p>Combined known estimate: <strong>' + money(cost_sum(ledger)) + '</strong>. Unknown request usage can add cost beyond either end of a displayed rate range.</p></section>'
    body += '<section id="track-ledger"><h2>Reconcile track subtotals</h2>' + table(['Scope', 'Attempts', 'Known tokens', 'Known estimate'], [[label, len(rows), count(known_sum(r['tokens'] for r in rows)), money(cost_sum(rows))] for label, rows in [
        ('Native · current selected observations', [r for r in ledger if r['track'] == 'Native reconstruction' and r['current']]),
        ('Native · other historical observations', [r for r in ledger if r['track'] == 'Native reconstruction' and not r['current']]),
        ('Typed · all historical observations', [r for r in ledger if r['track'] == 'Typed capabilities'])]]) + '</section>'
    native = report.all_records()
    body += '<section id="usage"><h2>Native attempt usage</h2><label>Filter usage <input data-table-filter="native-usage" aria-label="Filter native usage"></label>'
    body += table(['Attempt', 'Model', 'Fresh input', 'Cached input', 'Cache writes', 'Output', 'Reported total', 'Estimate'], [[
        '<a href="' + report.case_link(r) + '#' + text(r['model']) + '">' + text(r['experiment_label'] + ' / ' + r['case']['label']) + '</a><small>' + text(r['condition_label']) + '</small>', text(r['model_label']),
        *[count(r['receipt'].get('reported_usage', {}).get(k)) for k in ('inputTokens', 'cacheReadTokens', 'cacheWriteTokens', 'outputTokens', 'totalTokens')], money(r['cost'])] for r in native if r['receipt']], 'native-usage') + '</section>'
    body += report.capability_costs()
    body += '<section id="runtime"><h2>All native attempts with recorded durations</h2><p>Harness duration includes provider and tool waiting. Preparation, attestation, grading and report time are outside it. Unrecorded durations remain unknown. Typed durations are unavailable in the sanitized summary. <a href="diagnostics.html#local-performance">Command wall time and measured stages</a>.</p>'
    timings = []
    for model, spec in report.data['models'].items():
        for state in sorted({r['status'] for r in native if r['model'] == model and r['receipt']}):
            rows = [r for r in native if r['model'] == model and r['status'] == state and r['receipt']]
            values = [charts.elapsed_seconds(r['receipt']) for r in rows if charts.elapsed_seconds(r['receipt']) is not None]
            timings.append([text(spec['label']), text(state.replace('_', ' ')), f'{len(values)}/{len(rows)}', f'{median(values)/60:.2f} min' if values else 'Unknown', f'{min(values)/60:.2f}–{max(values)/60:.2f} min' if values else 'Unknown'])
    body += table(['Model', 'Terminal state', 'Known durations / attempts', 'Median harness time', 'Observed range'], timings)
    ungraded = sum(bool(r['receipt']) and not r['grade'] and charts.elapsed_seconds(r['receipt']) is not None for r in native)
    body += '<p>' + str(ungraded) + ' known durations from ungraded observations appear here but are excluded from the graded-only median on Overview.</p>'
    durations = []
    for row in native:
        if not row['receipt']:
            continue
        elapsed = charts.elapsed_seconds(row['receipt'])
        evidence = row.get('timing_evidence') or {}
        seconds = lambda n: 'Unknown' if n is None else f'{n:.3f} s'
        durations.append(['<a href="' + report.case_link(row) + '#' + text(row['model']) + '">' + text(row['experiment_label'] + ' / ' + row['case']['label']) + '</a>',
            text(row['model_label']), text(row['status'].replace('_', ' ')), seconds(elapsed),
            seconds(evidence.get('completed_request_seconds')), seconds(evidence.get('tool_seconds_sum'))])
    body += '<details><summary>Exact recorded durations and attempt links</summary><p>API durations include network/streaming; parallel request or tool durations can overlap. These columns are not an additive wall-clock decomposition. Missing historical timestamps remain unknown.</p>' + table(
        ['Attempt', 'Model', 'State', 'Harness elapsed', 'Completed API duration sum', 'Tool duration sum'], durations, 'runtime-attempts') + '</details></section>'
    body += report_prices.section(report.data)
    body += '<section><h2>Accounting and readability are separate</h2><p>Costs do not imply correctness. Tokenizers differ across providers; repeated context and reported reasoning are counted once using the provider ledger. Rate intervals and missing usage are independent uncertainties. Local compute and this coordinating conversation are outside this benchmark ledger.</p></section>'
    report.page('costs.html', 'Tokens and estimated cost', body)
