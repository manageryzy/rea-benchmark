"""Request-level accounting derived from evidence, independent of task success."""
import hashlib
import json
from pathlib import Path

VERSION = 'request-ledger-v1'
TOKEN_KEYS = ('inputTokens', 'cacheReadTokens', 'cacheWriteTokens', 'outputTokens', 'totalTokens')


def tokens(observation):
    raw = observation.get('raw_usage', observation.get('usage', {})) or {}
    if 'prompt_tokens' in raw or 'completion_tokens' in raw:
        cached = (raw.get('prompt_tokens_details') or {}).get('cached_tokens')
        prompt = raw.get('prompt_tokens')
        # Missing cache breakdown remains unknown, not an invented cache hit.
        normalized = {'inputTokens': prompt - (cached or 0) if prompt is not None else None,
                      'cacheReadTokens': cached, 'outputTokens': raw.get('completion_tokens'),
                      'totalTokens': raw.get('total_tokens')}
    else:
        normalized = {key: raw[key] for key in TOKEN_KEYS if key in raw}
    for value in normalized.values():
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError('Invalid provider token count')
    return {key: value for key, value in normalized.items() if value is not None}


def request_id(value):
    for key in ('request_id', 'sequence', 'request_sequence'):
        if value.get(key) is not None:
            return str(value[key])
    return None


def ledger(receipt):
    """Never infer completeness from exit status or equality of empty counts."""
    receipt = receipt or {}
    requests = receipt.get('requests', [])
    raw = receipt.get('usage_observations_raw') or receipt.get('usage_observations') or []
    issues, grouped, unlinked = [], {}, []
    for index, item in enumerate(raw):
        normalized = tokens(item)
        if not normalized:
            continue
        rid = request_id(item)
        row = {**item, 'usage': normalized, 'request_id': rid,
               'raw_usage': item.get('raw_usage', item.get('usage'))}
        if rid is None:
            unlinked.append(row)
            continue
        if item.get('usage_semantics', 'cumulative') != 'cumulative':
            # Delta streams need an event identity; repeated deltas cannot be guessed away.
            event = item.get('event_id')
            if event is None:
                issues.append('unidentified_delta_usage')
                unlinked.append(row)
                continue
            key = rid + ':event:' + str(event)
        else:
            key = rid
        if key in grouped:
            previous = grouped[key]
            if item.get('usage_semantics', 'cumulative') == 'cumulative':
                # Providers can repeat the same cumulative usage on multiple SSE events.
                for token, count in previous['usage'].items():
                    if token in normalized and normalized[token] < count:
                        issues.append('decreasing_cumulative_usage')
                row['usage'] = {token: max(previous['usage'].get(token, 0), normalized.get(token, 0))
                                for token in previous['usage'].keys() | normalized.keys()}
            elif previous['usage'] != row['usage']:
                issues.append('conflicting_usage_event')
        grouped[key] = row
    observations = list(grouped.values()) + unlinked
    if not observations and any(value for value in tokens({'usage': receipt.get('reported_usage', {})}).values()):
        observations = [{'finished_at': receipt.get('completed_at', receipt.get('started_at')),
                         'usage': tokens({'usage': receipt['reported_usage']}), 'request_id': None,
                         'source': 'aggregate_only'}]
        observations = [row for row in observations if row['usage']]
    totals = {}
    for row in observations:
        for key, value in row['usage'].items():
            totals[key] = totals.get(key, 0) + value
    entries, ids = [], set()
    for index, request in enumerate(requests):
        rid = request_id(request)
        if rid is None or rid in ids:
            issues.append('missing_or_duplicate_request_identity')
        if rid is not None:
            ids.add(rid)
        matched = [row for row in observations if rid is not None and row.get('request_id') == rid]
        no_forward = request.get('forwarded') is False
        complete = no_forward or (bool(matched) and bool(request.get('finished_at')) and
                                  all('inputTokens' in row['usage'] and 'outputTokens' in row['usage'] and
                                      row.get('complete') is not False for row in matched))
        entries.append({'request_id': rid or f'unidentified-{index}', 'http_status': request.get('http_status'),
                        'started_at': request.get('started_at'), 'finished_at': request.get('finished_at'),
                        'forwarded': request.get('forwarded'),
                        'status': 'complete' if complete else 'partial' if matched else 'unknown'})
    if unlinked:
        issues.append('usage_request_identity_missing')
    if any(row.get('request_id') and row['request_id'] not in ids for row in observations):
        issues.append('usage_without_request_record')
    proven_zero = receipt.get('provider_forwards') == 0 and receipt.get('request_ledger_complete') is True
    complete = (bool(entries) and all(row['status'] == 'complete' for row in entries) and not issues) or (not entries and not observations and proven_zero)
    if receipt.get('usage_complete') is False:
        complete = False
        issues.append('explicitly_incomplete')
    status = 'complete' if complete else 'partial' if observations else 'unknown'
    if not requests and not proven_zero:
        issues.append('request_coverage_unknown')
    identity = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    return {'version': VERSION, 'receipt_sha256': identity, 'status': status,
            'usage_complete': status == 'complete', 'requests': entries,
            'observations': observations, 'reported_usage': totals, 'issues': sorted(set(issues)),
            'observed_usage_available': bool(observations), 'proven_zero_forwards': proven_zero}


def derive(receipt):
    accounting = ledger(receipt)
    return {**receipt, 'reported_usage': accounting['reported_usage'],
            'usage_observations': accounting['observations'], 'usage_complete': accounting['usage_complete'],
            'usage_status': accounting['status'], 'accounting_version': VERSION}, accounting


def annotate_cost(cost, accounting):
    return {**cost, 'usage_complete': accounting['usage_complete'], 'usage_status': accounting['status'],
            'accounting_version': VERSION, 'unknown_usage_remainder': not accounting['usage_complete'],
            'known_subtotal_low_usd': cost.get('low_usd'), 'known_subtotal_high_usd': cost.get('high_usd'),
            'estimated_total_low_usd': cost.get('low_usd') if accounting['usage_complete'] else None,
            'estimated_total_high_usd': cost.get('high_usd') if accounting['usage_complete'] else None}


def save(directory, prices, cost):
    """Store derived accounting under its own declared action, never inference."""
    import action_cache
    directory = Path(directory)
    raw = (directory / 'result.json').read_bytes()
    receipt = json.loads(raw)
    value = ledger(receipt)
    semantic = {key: item for key, item in value.items() if key != 'receipt_sha256'}
    model = receipt.get('model')
    if model == 'glm-5.3':
        selected_prices = {key: prices.get(key) for key in ['input', 'cache_read', 'output', 'unit_tokens']}
        selected_prices['fx'] = {'cny_per_usd': prices.get('fx', {}).get('cny_per_usd')}
    else:
        spec = prices.get(model, {})
        selected_prices = {'unit_tokens': prices.get('unit_tokens'), model: {key: spec[key] for key in
                           ['peak_utc_hours', 'peak', 'off_peak', 'short', 'short_context_max_input', 'long_multipliers'] if key in spec}}
    inputs = action_cache.implementation_inputs('accounting')
    inputs.update({'evidence/ledger.json': action_cache.canonical(semantic),
                   'configuration/pricing.json': action_cache.canonical(selected_prices),
                   'evidence/cost.json': action_cache.canonical(cost)})
    action = action_cache.Action('accounting', inputs, arguments=['rea-benchmark', 'account', model])
    cache = action_cache.Cache()
    result = cache.lookup(action)
    payload = {'ledger': semantic, 'cost': cost}
    if result is None:
        cache.commit(action, {'accounting.json': action_cache.canonical(payload)})
    elif json.loads(cache.get(result['output_files']['accounting.json']['digest'])) != payload:
        raise ValueError('Accounting action output mismatch')
    # Origin receipt identity is provenance, separate from the semantic action.
    identity = {'version': VERSION, 'cache_schema': 3, 'action': action.descriptor,
                'receipt_sha256': hashlib.sha256(raw).hexdigest()}
    key = action.key
    path = directory / 'accounting' / VERSION / key / 'record.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({'identity': identity, 'ledger': value, 'cost': cost}, sort_keys=True, indent=2) + '\n'
    if path.exists():
        # A changed provenance-only receipt may share an accounting result. Keep
        # each original receipt record rather than replacing its provenance.
        old = json.loads(path.read_text())
        if {k: v for k, v in old['ledger'].items() if k != 'receipt_sha256'} != semantic or old['cost'] != cost:
            raise ValueError('Accounting cache differs for identical action')
    else:
        with path.open('x') as stream:
            stream.write(text)
    return key
