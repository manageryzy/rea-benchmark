"""Published-rate estimates; never substitute these for provider invoices."""
from datetime import datetime, timezone
from decimal import Decimal

def estimate(receipt, prices):
    observations = receipt.get('usage_observations') or [
        {'finished_at': receipt.get('started_at'), 'usage': receipt.get('reported_usage', {})}
    ]
    if not any(o['usage'] for o in observations):
        return {'low_usd': None, 'high_usd': None, 'actual_billed_usd': None,
                'note': 'No token usage was reported; charge cannot be estimated.'}
    lower = Decimal(0)
    upper = Decimal(0)
    periods = set()
    unknown_writes = False
    for observation in observations:
        usage = observation['usage']
        timestamp = observation.get('finished_at') or receipt.get('completed_at') or receipt.get('started_at')
        if not timestamp:
            return {'low_usd': None, 'high_usd': None, 'actual_billed_usd': None,
                    'note': 'Usage timestamp missing; pricing period cannot be established.'}
        when = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(timezone.utc)
        model = receipt['model']
        spec = prices.get(model)
        if spec is None:
            return {'low_usd': None, 'high_usd': None, 'actual_billed_usd': None,
                    'note': 'No saved price for this model; cost is unknown.'}
        if 'peak_utc_hours' in spec:
            peak = when.weekday() < 5 and any(a <= when.hour < b for a, b in spec['peak_utc_hours'])
            period = 'peak' if peak else 'off_peak'
            rates = spec[period]
        else:
            period = 'standard_short_context'
            rates = spec['short'].copy()
            context = sum(usage.get(k, 0) or 0 for k in ['inputTokens', 'cacheReadTokens', 'cacheWriteTokens'])
            if context > spec['short_context_max_input']:
                period = 'standard_long_context'
                rates = {k: v * spec['long_multipliers'][k] for k, v in rates.items()}
        periods.add(period)
        cost = sum(Decimal(str(usage.get(key, 0) or 0)) * Decimal(str(rates[rate]))
                   for key, rate in [('inputTokens', 'input'), ('cacheReadTokens', 'cache_read'),
                                     ('cacheWriteTokens', 'cache_write'), ('outputTokens', 'output')])
        lower += cost
        upper += cost
        if 'short' in spec and 'cacheWriteTokens' not in usage:
            unknown_writes = True
            upper += Decimal(str(usage.get('inputTokens', 0) or 0)) * (
                Decimal(str(rates['cache_write'])) - Decimal(str(rates['input'])))
    unit = Decimal(prices['unit_tokens'])
    return {'low_usd': float(lower / unit), 'high_usd': float(upper / unit),
            'actual_billed_usd': None, 'periods': sorted(periods),
            'cache_write_count_unknown': unknown_writes,
            'basis': 'Published token rates; recorded step completion timestamps determine pricing period. No local compute or setup costs.',
            'billing_mode': 'Codex plan/credits; API-equivalent estimate only' if receipt.get('provider') == 'openai-codex' else 'API rate-based estimate; invoice debit unavailable'}

def display(low, high):
    if low is None:
        return 'unavailable'
    return f'${low:.5f}' if abs(low - high) < 1e-10 else f'${low:.5f}–${high:.5f}'
