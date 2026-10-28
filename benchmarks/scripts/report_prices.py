"""Sanitized pricing and request coverage for both report presentations.

This is a presentation projection of saved evidence; it never revises accounting
or looks up live prices. No request contents, endpoints or credentials survive.
"""
from datetime import datetime
import hashlib
from html import escape
import json
from urllib.parse import urlsplit

import records
import usage

RATE_KEYS = ('input', 'cache_read', 'cache_write', 'output')
PRICE_HOSTS = {'api-docs.deepseek.com', 'developers.openai.com', 'docs.bigmodel.cn',
               'www.ecb.europa.eu'}


def public_url(value):
    parsed = urlsplit(value or '')
    return value if (parsed.scheme == 'https' and parsed.hostname in PRICE_HOSTS
                     and not parsed.username and not parsed.password and not parsed.query
                     and not parsed.fragment and parsed.port in (None, 443)) else None


def coverage(receipt):
    """Counts only; unknown request coverage stays unknown, not zero."""
    if 'usage_coverage' in receipt:
        return {key: receipt['usage_coverage'].get(key) for key in (
            'recorded_requests', 'requests_with_incomplete_usage', 'proven_zero_forwards')}
    ledger = usage.ledger(receipt)
    requests = ledger['requests']
    proven_zero = ledger['proven_zero_forwards']
    known = bool(requests) or proven_zero
    missing = sum(item['status'] != 'complete' for item in requests)
    return {'recorded_requests': len(requests) if known else None,
            'requests_with_incomplete_usage': missing if known else None,
            'proven_zero_forwards': proven_zero}


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value
    except ValueError:
        return None


def basis(prices, model):
    """Create an explicit allowlist, never embed provider configuration."""
    spec = prices if prices.get('model') == model else prices.get(model, {})
    if not spec:
        return None
    rates = []
    if 'peak_utc_hours' in spec:
        for period in ('peak', 'off_peak'):
            rates.append({'period': period, **{key: spec[period].get(key) for key in RATE_KEYS}})
        rule = 'Recorded completion time; weekday peak UTC hours shown below, otherwise off-peak.'
    elif 'short' in spec:
        rates.append({'period': 'standard_short_context', **{key: spec['short'].get(key) for key in RATE_KEYS}})
        if spec.get('long_multipliers'):
            rates.append({'period': 'standard_long_context', **{
                key: spec['short'][key] * spec['long_multipliers'][key] for key in RATE_KEYS}})
        rule = 'Recorded input context selects short or long context rates.'
    else:
        rates.append({'period': 'standard', **{key: spec.get(key) for key in RATE_KEYS}})
        rule = 'Published standard token rates at the saved snapshot date.'
    fx = prices.get('fx', {})
    result = {'model': model, 'snapshot_at': _date(prices.get('verified_at_utc', prices.get('verified_at'))),
              'currency': prices.get('currency'), 'unit_tokens': prices.get('unit_tokens'),
              'source_url': public_url(spec.get('pricing_source', spec.get('source'))),
              'snapshot_sha256': hashlib.sha256(json.dumps(prices, sort_keys=True).encode()).hexdigest(),
              'fx_date': _date(fx.get('date')), 'cny_per_usd': fx.get('cny_per_usd'),
              'fx_source_url': public_url(fx.get('source')), 'rates': rates,
              'rule': rule, 'short_context_max_input': spec.get('short_context_max_input'),
              'peak_utc_hours': spec.get('peak_utc_hours', []),
              'estimate_kind': 'api_equivalent' if model.startswith('gpt-') else 'published_rate_estimate',
              'actual_invoice_available': False}
    result['id'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    result.update(recorded_periods=[], request_first_at=None, request_last_at=None)
    return result


def prepare(data, read=None):
    """Attach presentation metadata while the private report input seal is open."""
    read = read or records.read
    catalog = {row['id']: row for row in data['experiments']}
    bases = {row['id']: row for row in data.get('price_bases', [])}
    loaded = {}
    for row in data['records'] + data.get('condition_records', []):
        if not row.get('receipt'):
            continue
        row['receipt']['usage_coverage'] = coverage(row['receipt'])
        if row['receipt']['usage_coverage']['proven_zero_forwards']:
            tokens = row['receipt'].setdefault('reported_usage', {})
            for category in usage.TOKEN_KEYS:
                tokens.setdefault(category, 0)
        model = row['model']
        filename = data['models'].get(model, {}).get('pricing_file') or catalog[row['experiment']].get('pricing_file')
        if not filename:
            continue
        key = (filename, model)
        if key not in loaded:
            prices = read(records.ROOT / filename)
            loaded[key] = basis(prices, model) if prices else None
        item = loaded[key]
        if item:
            bases.setdefault(item['id'], item)
            row['pricing_basis_id'] = item['id']
            selected = bases[item['id']]
            selected['recorded_periods'] = sorted(set(selected['recorded_periods']) | set(row['cost'].get('periods', [])))
            # These are observed request/step timestamps, never inferred dates
            # from filenames or the price snapshot date.
            receipt = row['receipt']
            timestamps = [_date(value) for entry in receipt.get('requests', []) for value in
                          (entry.get('started_at'), entry.get('finished_at'))]
            timestamps += [_date(entry.get('finished_at')) for entry in receipt.get('usage_observations', [])]
            timestamps += [selected['request_first_at'], selected['request_last_at']]
            timestamps = [value for value in timestamps if value]
            if timestamps:
                ordered = sorted(timestamps, key=lambda value: datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())
                selected.update(request_first_at=ordered[0], request_last_at=ordered[-1])
    data['price_bases'] = sorted(bases.values(), key=lambda item: (item['model'], item['snapshot_at'] or '', item['id']))


def section(data):
    """Only renders the sanitized shape consumed by PublicReport."""
    items = data.get('price_bases', [])
    body = '<section id="price-basis"><h2>Saved pricing basis</h2><p>These are frozen published-rate estimates for known usage, not verified invoices. A rate interval is not a confidence interval or an upper bound on the final bill when usage is missing. Invoice amounts and deployment time are unavailable.</p>'
    if not items:
        return body + '<p>No sanitized price snapshot is available in this saved report. Existing cost estimates are retained without inventing their pricing provenance.</p></section>'
    for item in items:
        label = data['models'].get(item['model'], {}).get('label', item['model'])
        body += '<details id="price-' + item['id'] + '"><summary>' + escape(label) + ' · snapshot ' + escape(item['snapshot_at'] or 'date unknown') + '</summary>'
        body += '<p>' + escape(item['rule']) + ' All rates below are ' + escape(item['currency'] or 'unknown currency') + ' per ' + format(item['unit_tokens'], ',') + ' tokens. '
        body += 'API-equivalent estimate; subscription debit unknown.' if item['estimate_kind'] == 'api_equivalent' else 'Published-rate estimate; actual invoice debit unknown.'
        if item['source_url']:
            body += ' <a href="' + escape(item['source_url'], quote=True) + '">Published pricing source</a>.'
        body += '</p><div class="scroll" tabindex="0" role="region" aria-label="Saved token rates"><table><caption>Saved token rates · ' + escape(label) + '</caption><thead><tr><th scope="col">Rate period</th><th scope="col">Fresh input</th><th scope="col">Cache read</th><th scope="col">Cache write</th><th scope="col">Output</th></tr></thead><tbody>'
        for rates in item['rates']:
            body += '<tr><th scope="row">' + escape(rates['period'].replace('_', ' ')) + '</th>' + ''.join('<td>' + ('Unknown' if rates[key] is None else format(rates[key], '.10g')) + '</td>' for key in RATE_KEYS) + '</tr>'
        body += '</tbody></table></div>'
        if item['peak_utc_hours']:
            body += '<p>Weekday peak UTC hours: ' + ', '.join(str(a) + ':00–' + str(b) + ':00' for a, b in item['peak_utc_hours']) + '.</p>'
        if item['short_context_max_input'] is not None:
            body += '<p>Short-context threshold: ' + format(item['short_context_max_input'], ',') + ' input tokens.</p>'
        body += '<p>Recorded request/step timestamp span: ' + escape(item['request_first_at'] or 'unknown') + ' to ' + escape(item['request_last_at'] or 'unknown') + '. Recorded rate periods: ' + escape(', '.join(item['recorded_periods']) or 'unknown') + '.</p>'
        if item['cny_per_usd'] is not None:
            body += '<p>Display conversion: 1 USD = ' + format(item['cny_per_usd'], '.12g') + ' CNY; conversion date ' + escape(item['fx_date'] or 'unknown') + '. Reference conversion only; invoice exchange rate unknown.'
            if item['fx_source_url']:
                body += ' <a href="' + escape(item['fx_source_url'], quote=True) + '">Exchange-rate source</a>.'
            body += '</p>'
        body += '<p>Saved snapshot identity: <code>' + item['snapshot_sha256'] + '</code>.</p></details>'
    return body + '</section>'
