"""Concise overview and expenditure views over unchanged saved observations."""
from collections import Counter
from statistics import median
from html import escape as e
import report_views as views
import report_charts as charts
from report_labels import route_label


def overview(report):
    from report import table
    catalog = report.data['models']
    cells = views.aggregate_cells(report.data)
    stats = views.aggregate_stats(cells, catalog)
    graded = sum(s['graded_attempts'] for s in stats)
    passed = sum(s['passed_cells'] for s in stats)
    unique = len({c['key'][1:] for c in cells})
    body = '<section id="status"><h2>Benchmark overview</h2><div class="cards">'
    for value, label in [(str(unique), 'Unique configured cases'), (f'{graded}/{len(cells)}', 'Graded / planned cells'),
                         (str(passed), 'Full marks'), (str(graded-passed), 'Below full marks'),
                         (str(len(cells)-graded), 'Ungraded cells')]:
        body += '<div class="card"><strong>' + e(value) + '</strong>' + e(label) + '</div>'
    body += '</div><p>Valid saved results count once per model and case. Missing measurements are excluded from correctness scores.</p></section>'
    groups = views.aggregate_dimensions(cells, catalog)
    for group in groups:
        group['note'] = 'Each axis shows mean correctness across graded cases; n is the number of cases. Different models may cover different subsets.'
    body += '<section id="capability-radar"><h2>Capability radar</h2>'
    body += charts.capability_dimensions(groups, catalog, scope='All valid saved grades', radar_first=True)
    body += '<div class="cards category-cards">'
    for category, label, note in views.CAPABILITY_CATEGORIES:
        selected = [c for c in cells if c['category'] == category]
        n = sum(c['grade'] is not None for c in selected)
        body += '<div class="card"><h3>' + e(label) + '</h3><strong>' + (f'{n}/{len(selected)}' if selected else 'Unmeasured') + '</strong><small>' + ('Graded / planned cells' if selected else 'No cases configured; excluded from score and coverage denominators.') + '</small></div>'
    body += '</div></section><section id="coverage"><h2>Model coverage</h2>'
    body += charts.completion(stats, scope='All configured tasks', title='Completion coverage across all capability categories')
    body += table(['Model', 'Graded / planned', 'Full marks', 'Below full marks', 'Ungraded', 'Case-equal correctness'], [
        [e(s['model_label'])+'<small>'+e(route_label(s['model'],catalog[s['model']]))+'</small>',
         f'{s["graded_attempts"]}/{s["planned_cells"]}', s['passed_cells'], s['graded_attempts']-s['passed_cells'],
         s['planned_cells']-s['graded_attempts'], 'Unmeasured' if s['score'] is None else f'{s["score"]:.1f}%'] for s in stats])
    body += '</section><section id="ungraded"><h2>Why results are ungraded</h2><p>Unfinished budget stops, interrupted tasks, execution failures and localization format reviews have no semantic correctness score. Complete answers can retain a score after exceeding a budget.</p>'
    labels = {'not_started':'Not started', 'interrupted':'Interrupted', 'budget_exhausted':'Budget stopped',
              'environment_failure':'Execution failure', 'localization_format_review':'Localization format needs review',
              'completed_over_budget':'Grading pending', 'completed_queue_held':'Grading pending',
              'inference_ready':'Grading pending', 'running':'Running'}
    ungraded = []
    for model, info in catalog.items():
        counts = Counter(c['status'] for c in cells if c['model']==model and not c['grade'])
        for state, number in sorted(counts.items()):
            ungraded.append([e(info['label']),e(labels.get(state,'Not graded')),number])
    body += table(['Model','State','Count'],ungraded) + '</section>'
    body += '<p class="reader-links"><a href="cases/index.html">Native cases</a> · <a href="capabilities.html">Typed task results</a> · <a href="methodology.html#scoring">How scores are calculated</a></p>'
    report.page('index.html','Global benchmark summary',body)
    report.page('comparison.html','Native model comparison',views.comparison(report))
    report.page('dimensions.html','Model capability profiles',
        '<section id="all-capabilities"><h2>Capability overview</h2>' + charts.capability_dimensions(groups, catalog, scope='All valid saved grades', radar_first=True) + '</section>' + views.comparison(report, dimensions_only=True))


def costs(report):
    from report import table, money
    import localization_report
    ledger = views.expenditure(report)
    catalog = report.data['models']
    totals = []
    table_rows = []
    for model, info in catalog.items():
        selected = [r for r in ledger if r['model']==model]
        cost = views.cost_sum(selected)
        totals.append({'model':model,'model_label':info['label'],'started_attempts':len(selected),'cost':cost})
        table_rows.append([e(info['label']),len(selected),views.count(views.known_sum(r['tokens'] for r in selected)),
                           money(cost),sum(not r['cost'].get('usage_complete') for r in selected)])
    body = '<section id="expenditure"><h2>Saved cost summary</h2><p>Cost totals retain the saved accounting scope. Detailed token use and durations include every distinct saved attempt. Reused caches and offline regrading add no model usage.</p>'
    body += '<p>Combined known estimate: <strong>'+money(views.cost_sum(ledger))+'</strong>.</p>'
    body += '<p>Published-rate estimates cover recorded usage. Missing usage can increase the total. Codex amounts are API equivalents; subscription charges are unknown.</p>'
    body += charts.cost_chart(totals,scope='Saved accounting summary')
    body += table(['Model','Summary observations','Summary tokens','Known estimate','Incomplete attempts'],table_rows)+'</section>'
    native = {(r['experiment'],r['case']['id'],r['model'],r['condition'],r.get('attempt_id')):r for r in report.all_records() if r.get('receipt')}
    typed = {r['attempt_id']:r for r in localization_report.history(report.data)}
    synthetic = [{'model':r['model'],'receipt':r['receipt']} for r in native.values()]
    synthetic += [{'model':r['model'],'receipt':{'reported_usage':r['known_tokens']}} for r in typed.values()]
    body += '<section id="tokens"><h2>Token use</h2>'+charts.token_chart(synthetic,catalog,scope='All unique saved native and typed attempts')+'</section>'
    timings=[]; duration_rows=[]
    for model,info in catalog.items():
        selected=[r for r in native.values() if r['model']==model]
        values=[charts.elapsed_seconds(r['receipt']) for r in selected if charts.elapsed_seconds(r['receipt']) is not None]
        timings.append({'model':model,'model_label':info['label'],'seconds':median(values) if values else None,'n':len(values)})
        duration_rows.append([e(info['label']),f'{len(values)}/{len(selected)}',f'{median(values)/60:.2f} min' if values else 'Unknown',f'{min(values)/60:.2f}–{max(values)/60:.2f} min' if values else 'Unknown'])
    maximum=max((x['seconds'] or 0 for x in timings),default=1) or 1
    body += '<section id="runtime"><h2>Recorded native attempt durations</h2><p>Durations include model, network and tool waiting. Failed and ungraded attempts are included here. Typed durations are unavailable in this saved summary.</p>'
    body += charts.metric('Median recorded duration',timings,'seconds',lambda r:f'{r["seconds"]/60:.2f} min · n={r["n"]}' if r['seconds'] is not None else 'Unknown',maximum,scope='Native attempts with recorded durations',unit='s',note='Offline grading and report generation are excluded. Models have different task mixes.')
    body += table(['Model','Known durations / attempts','Median duration','Observed range'],duration_rows)+'</section>'
    detail=[]
    for r in native.values():
        detail.append(['<a href="'+report.case_link(r)+'#'+e(report.case_anchor(r))+'">'+e(r['case']['label'])+'</a>',e(r['model_label']),e('Native reconstruction'),views.count(r['receipt'].get('reported_usage',{}).get('totalTokens')),money(r['cost'])])
    for r in typed.values():
        detail.append(['<a href="attempts/'+e(r['attempt_id'])+'.html">'+e(r['task_id'])+'</a>',e(catalog.get(r['model'],{}).get('label',r['model'])),e('Typed capabilities'),views.count(r['known_tokens'].get('totalTokens')),money(r['cost'])])
    body += '<section id="usage"><h2>Usage details</h2><details><summary>Browse saved attempt usage</summary>'+table(['Task','Model','Track','Known tokens','Known estimate'],detail,'usage-details')+'</details></section>'
    body += '<section id="price-basis"><h2>Saved pricing basis</h2><p>Rates are saved with the measurements. A price range reflects rate uncertainty; it is not an upper bound when usage is missing.</p>'
    for item in report.data.get('price_bases',[]):
        body += '<details id="price-'+e(item['id'])+'"><summary>'+e(catalog.get(item['model'],{}).get('label',item['model']))+' · snapshot '+e(item['snapshot_at'] or 'date unknown')+'</summary>'
        body += '<p>'+e(item['currency'] or 'unknown currency')+' / '+str(item['unit_tokens'])+' tokens</p>'
        body += table(['Rate period','Fresh input','Cache read','Cache write','Output'],[[e(r['period'].replace('_',' ')),*[('Unknown' if r[k] is None else format(r[k],'.10g')) for k in ('input','cache_read','cache_write','output')]] for r in item['rates']])
        if item.get('source_url'):body+='<p><a href="'+e(item['source_url'],quote=True)+'">Published pricing source</a></p>'
        if item.get('cny_per_usd') is not None:
            body+='<p>Display conversion: 1 USD = '+format(item['cny_per_usd'],'.12g')+' CNY; conversion date '+e(item['fx_date'] or 'unknown')+'. Reference conversion only; invoice exchange rate unknown.</p>'
        body+='</details>'
    report.page('costs.html','Tokens and estimated cost',body+'</section>')
