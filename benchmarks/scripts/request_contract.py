"""One pre-forward request contract, used by both benchmark transports.

This attests serialized requests, not remote weights or a provider's internal
implementation. Historical executions without these receipts remain unknown.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'benchmarks/config/request-contract.json'
CAP_FIELDS = {'max_tokens', 'max_completion_tokens', 'max_output_tokens'}
BUDGET_STOPS = {'model_turn_limit_reached', 'task_output_budget_reached',
                'task_observed_token_limit_reached', 'response_output_limit_reached',
                'task_wallet_limit_reached', 'response_observed_output_limit_reached'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class Violation(ValueError):
    """Public error category only: never attach request bodies or credentials."""


class BudgetExhausted(RuntimeError):
    """Terminal attempt stop; the configured queue policy selects the next case."""


def positive(value, name):
    if type(value) is not int or value <= 0:
        raise Violation('invalid_' + name)
    return value


def build(provider, model, gateway, harness, *, policy=None):
    policy = copy.deepcopy(policy or json.loads(POLICY.read_bytes()))
    if (policy['schema_version'] != 1 or policy['mode'] != 'certified' or policy['max_retries'] != 0
            or policy.get('budget_semantics') != 'complete-current-response-stop-before-next-v1'):
        raise Violation('unsupported_request_policy')
    protocol = provider.get('protocol')
    recovery = policy.get('transport_recovery')
    if recovery is not None:
        expected = {'id': 'codex-transient-retry-v1', 'protocol': 'openai-codex-responses',
                    'max_retries_per_request': 2, 'max_retries_per_task': 6,
                    'allowed_causes': ['SSLEOFError', 'RemoteDisconnected', 'IncompleteRead'],
                    'initial_delay_ms': 1000, 'max_delay_ms': 5000,
                    'unknown_usage': 'retain_unknown_do_not_impute_zero'}
        if recovery != expected:
            raise Violation('unsupported_transport_recovery')
        if protocol != recovery['protocol']:
            recovery = None
    if protocol not in policy['routes']:
        raise Violation('unsupported_budget_route')
    route = policy['routes'][protocol]
    limits = copy.deepcopy(policy['task_limits'])
    if set(limits) != {'max_model_turns', 'max_response_tokens', 'max_output_tokens', 'max_observed_total_tokens'}:
        raise Violation('invalid_task_limits')
    for name, value in limits.items():
        positive(value, name)
    if limits['max_response_tokens'] > limits['max_output_tokens']:
        raise Violation('response_cap_exceeds_task_output_budget')
    observed = route.get('enforcement') == 'observed_task_limits'
    if observed and (protocol != 'openai-codex-responses' or provider.get('provider') != 'openai-codex'):
        raise Violation('unsupported_observed_budget_route')
    maximum = None if observed else min(positive(harness['max_output_tokens'], 'output_ceiling'), limits['max_response_tokens'])
    if not observed and maximum < route['minimum']:
        raise Violation('output_ceiling_below_route_minimum')
    reasoning = model.get('reasoning', 'high')
    if reasoning not in ('low', 'medium', 'high', 'xhigh', 'max'):
        raise Violation('unsupported_reasoning_setting')
    wallet = None
    if harness.get('evidence_paging', {}).get('id') == 'lean-evidence-v4':
        for item in policy.get('wallet_limits', []):
            if item['model'] != model['id']:
                continue
            if (provider.get('provider') != item['provider'] or protocol != item['protocol']
                    or urlsplit(provider.get('base_url', '')).hostname != item['host']):
                raise Violation('wallet_price_route_mismatch')
            wallet = copy.deepcopy(item)
            if wallet['currency'] not in ('USD', 'CNY') or wallet['semantics'] != 'observed_cost_stop_before_next':
                raise Violation('invalid_wallet_policy')
            for value in [wallet['limit_nano'], *wallet['rates_nano_per_token'].values()]:
                positive(value, 'wallet_amount')
            if set(wallet['rates_nano_per_token']) != {'inputTokens', 'cacheReadTokens', 'cacheWriteTokens', 'outputTokens'}:
                raise Violation('invalid_wallet_rates')
            if 'max_response_tokens' in wallet:
                cap = positive(wallet['max_response_tokens'], 'wallet_response_ceiling')
                if cap > limits['max_output_tokens'] or observed:
                    raise Violation('invalid_wallet_response_ceiling')
                limits['max_response_tokens'] = cap
                maximum = min(positive(harness['max_output_tokens'], 'output_ceiling'), cap)
            if wallet.get('response_allocation') not in (None, 'remaining_task_output_v1'):
                raise Violation('invalid_response_allocation')
    adaptive = None
    baseline = harness.get('budget_context')
    if baseline is not None:
        import adaptive_budget
        adaptive_policy = policy.get('adaptive_budget')
        if (not adaptive_policy or adaptive_policy['condition'] != harness.get('evidence_paging', {}).get('id')
                or baseline.get('model') != model['id']):
            raise Violation('adaptive_budget_context_mismatch')
        try:
            adaptive = adaptive_budget.resolve(baseline, provider, adaptive_policy, wallet)
        except (ValueError, KeyError, TypeError) as error:
            raise Violation('invalid_adaptive_budget_baseline') from error
        limits.update(max_model_turns=(None if adaptive_policy['max_model_turns'] is None else
                                      positive(adaptive_policy['max_model_turns'], 'model_turns')),
                      max_response_tokens=positive(adaptive_policy['max_response_tokens'], 'response_tokens'),
                      max_output_tokens=None, max_observed_total_tokens=None)
        maximum = None if observed else limits['max_response_tokens']
        if adaptive['mode'] == 'basic_turn_only':
            wallet = None
        elif wallet:
            wallet['limit_nano'] = adaptive['limit']
            wallet['max_response_tokens'] = limits['max_response_tokens']
            wallet.pop('response_allocation', None)
            wallet.pop('limit_basis', None)
        else:
            limits['max_observed_total_tokens'] = adaptive['limit']
    return {'schema_version': 1, 'policy_id': policy['id'],
            **({'deadline_outcome': policy['deadline_outcome']} if policy.get('deadline_outcome') else {}), 'policy_sha256': digest(policy),
            'budget_semantics': policy['budget_semantics'],
            'mode': 'observed_task_limits' if observed else 'certified', 'protocol': protocol, 'model': model['id'],
            'path': route['path'], 'cap_field': route['cap_field'], 'cap_minimum': route['minimum'],
            'output_ceiling': maximum, 'cap_scope': 'none' if observed else 'per_response', 'cap_units': route['cap_units'],
            'reasoning_accounting': route['reasoning_accounting'], 'reasoning': reasoning,
            'allowed_tool_names': sorted(harness.get('allowed_tool_names', [])),
            'wall_seconds': positive(harness['timeout_seconds'], 'wall_seconds'),
            'tool_calls': positive(gateway['max_tool_calls'], 'tool_calls'),
            'tool_timeout_ms': positive(harness['tool_timeout_ms'], 'tool_timeout_ms'),
            'max_requests': (None if limits['max_model_turns'] is None else
                min(limits['max_model_turns'], gateway['max_tool_calls'] + positive(policy['request_overhead_allowance'], 'request_allowance'))),
            'task_limits': limits,
            **({'adaptive_budget': adaptive} if adaptive else {}),
            **({'observed_response_ceiling': limits['max_response_tokens']} if adaptive and observed else {}),
            **({'wallet': wallet, 'total_token_limit_enforced': False} if wallet else {}),
            **({'response_allocation': wallet['response_allocation']}
               if wallet and wallet.get('response_allocation') else {}),
            'max_request_bytes': positive(policy['max_request_bytes'], 'request_bytes'),
            'max_retries': recovery['max_retries_per_request'] if recovery else 0,
            **({'transport_recovery': recovery} if recovery else {})}


def observed_only(contract):
    return (contract.get('mode') == 'observed_task_limits'
            and contract.get('protocol') == 'openai-codex-responses'
            and contract.get('cap_field') is None and contract.get('output_ceiling') is None)


def success_status(contract):
    """Do not certify a generation cap for the explicitly uncapped OAuth route."""
    return 'observed_limits_enforced' if observed_only(contract) else 'wire_enforced'


def complete_budget_usage(certificate):
    """Require final usage evidence, never treat a reservation as observed output."""
    rows = [row for row in certificate.get('requests', []) if row.get('forwarded')]
    latest = {item.get('request_id'): item for item in certificate.get('usage_observations', [])}
    return bool(rows) and all(
        row.get('response_complete') is True and not row.get('transport_error')
        and latest.get(row.get('request_id'), {}).get('complete') is True
        and all(type(latest[row.get('request_id')].get('usage', {}).get(key)) is int
                for key in ('outputTokens', 'totalTokens'))
        for row in rows)


def recovered_transport_errors(certificate, contract):
    """Prove bounded, same-input recovery; never erase an unsuccessful request."""
    policy = contract.get('transport_recovery')
    rows = certificate.get('requests', [])
    if not policy or not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        return set()
    if sum('retry_of' in r for r in rows) > policy['max_retries_per_task']:
        return set()
    recovered = set()
    for index in range(len(rows) - 2, -1, -1):
        row, following = rows[index:index + 2]
        resolved = (following.get('request_id') in recovered or
                    (following.get('forwarded') is True and following.get('http_status') == 200
                     and following.get('response_complete') is True and not following.get('transport_error')))
        if (resolved and row.get('transport_error') == 'provider_transport_interrupted'
                and row.get('transport_retry_eligible') is True
                and row.get('transport_diagnostic', {}).get('cause') in policy['allowed_causes']
                and row.get('http_status') in (None, 200)
                and row.get('recovery_request_id') == following.get('request_id')
                and following.get('retry_of') == row.get('request_id')
                and following.get('retry_root') == row.get('retry_root', row.get('request_id'))
                and following.get('retry_index') == row.get('retry_index', 0) + 1
                and following['retry_index'] <= policy['max_retries_per_request']
                and row.get('request_sha256') and row['request_sha256'] == following.get('request_sha256')
                and all(r.get('contract_sha256') == digest(contract)
                        and not r.get('rejected') and not r.get('budget_violation') for r in (row, following))):
            recovered.add(row['request_id'])
    return recovered


def settled_recovery_usage(certificate, contract):
    """Grading admission only; total usage stays incomplete after a lost response."""
    recovered = recovered_transport_errors(certificate, contract)
    return bool(recovered and complete_budget_usage({**certificate, 'requests': [
        r for r in certificate.get('requests', []) if r.get('request_id') not in recovered]}))


def grading_allowed(certificate, contract):
    """A completed final can be graded separately from aggregate-budget success.

    Historical receipts retain their original semantics. This helper cannot
    convert a stopped old attempt into a newly completed or compliant attempt.
    """
    if certificate.get('status') == 'deadline_exceeded':
        return deadline_grade_allowed(certificate, contract)
    recovered = recovered_transport_errors(certificate, contract)
    if certificate.get('status') == success_status(contract):
        return (not certificate.get('budget_exceeded', False)
                and all(not r.get('transport_error') or r.get('request_id') in recovered
                        for r in certificate.get('requests', [])))
    if certificate.get('status') != 'completed_over_budget':
        return False
    rows = [row for row in certificate.get('requests', []) if row.get('forwarded')]
    return bool(contract.get('budget_semantics') == 'complete-current-response-stop-before-next-v1'
                and certificate.get('contract') == contract
                and certificate.get('contract_sha256') == digest(contract)
                and certificate.get('budget_exceeded') is True and certificate.get('budget_excess')
                and certificate.get('queue_stop_required') is True
                and certificate.get('request_ledger_complete') is True
                and certificate.get('transport_complete') is True
                and certificate.get('final_response_complete') is True
                and (complete_budget_usage(certificate) or settled_recovery_usage(certificate, contract))
                and not certificate.get('rejected_requests')
                and rows and rows[-1].get('response_complete') is True
                and rows[-1].get('final_response') is True
                and not any((row.get('transport_error') and row.get('request_id') not in recovered)
                            or row.get('budget_violation') or row.get('rejected')
                            for row in certificate.get('requests', [])))


def deadline_grade_allowed(certificate, contract):
    """Admit only a negative completion grade, never partial answer correctness.

    Provider/auth/transport errors before the local deadline remain failures.
    Historical policies do not acquire this outcome retroactively.
    """
    if (contract.get('deadline_outcome') != 'completed_not_passed'
            or certificate.get('status') != 'deadline_exceeded'
            or certificate.get('contract') != contract
            or certificate.get('contract_sha256') != digest(contract)
            or type(certificate.get('deadline_elapsed_seconds')) not in (int, float)
            or not math.isfinite(certificate['deadline_elapsed_seconds'])
            or not certificate['deadline_elapsed_seconds'] >= contract['wall_seconds']
            or certificate.get('final_response_complete') is not False
            or certificate.get('request_ledger_complete') is not True):
        return False
    rows = certificate.get('requests', [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return False
    forwarded = [r for r in rows if r.get('forwarded')]
    if not forwarded or not rows:
        return False
    recovered = recovered_transport_errors(certificate, contract)
    for index, row in enumerate(rows):
        if row.get('request_id') in recovered:
            continue
        last = index == len(rows) - 1
        if row.get('contract_sha256') != certificate['contract_sha256'] or row.get('budget_violation'):
            return False
        if row.get('rejected') or not row.get('forwarded'):
            if not (last and row.get('rejected') == 'wall_limit_reached'):
                return False
        elif row.get('response_complete') is True:
            if row.get('http_status') != 200 or row.get('transport_error'):
                return False
        elif not (last and row.get('transport_error') == 'wall_limit_reached' and row.get('http_status') == 200):
            return False
    settled = [r for r in forwarded if r.get('response_complete') is True]
    if settled and not complete_budget_usage({**certificate, 'requests': settled}):
        return False
    return True


def from_task(task):
    frozen = task['runtime_attestation']['configuration']
    contract = frozen['harness'].get('request_contract')
    if not contract:
        raise Violation('missing_frozen_request_contract')
    expected = build(task['runtime_attestation']['provider'], {'id': task['model'], 'reasoning': task['reasoning']},
                     frozen['gateway'], frozen['harness'])
    if contract != expected or task.get('request_contract') != expected:
        raise Violation('request_contract_changed')
    return expected


def decode(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise Violation('duplicate_json_key')
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(Violation('nonfinite_json_number')))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise Violation('invalid_request_json') from error
    if not isinstance(value, dict):
        raise Violation('invalid_request_object')
    return value


def validate(contract, path, request):
    if path != contract['path']:
        raise Violation('unsupported_request_path')
    if request.get('model') != contract['model']:
        raise Violation('request_model_changed')
    field = contract['cap_field']
    if observed_only(contract):
        if CAP_FIELDS.intersection(request):
            raise Violation('unexpected_cap_in_observed_condition')
        cap = None
    else:
        if CAP_FIELDS.intersection(request) != {field}:
            raise Violation('missing_or_ambiguous_output_cap')
        cap = request[field]
        if type(cap) is not int or not contract['cap_minimum'] <= cap <= contract['output_ceiling']:
            raise Violation('invalid_output_cap')
    if type(request.get('stream', False)) is not bool:
        raise Violation('invalid_stream_setting')
    if request.get('background', False) is not False:
        raise Violation('background_generation_denied')
    protocol, effort = contract['protocol'], contract['reasoning']
    encoding = 'request'
    if protocol == 'anthropic-messages':
        valid = request.get('thinking', {}).get('type') == 'enabled' and request.get('output_config', {}).get('effort') == effort
    elif protocol in ('openai-responses', 'openai-codex-responses'):
        reasoning = request.get('reasoning', {})
        if reasoning.get('mode', 'standard') != 'standard':
            raise Violation('reasoning_mode_changed')
        items = request.get('input', [])
        updates = []
        adjacent = False
        if isinstance(items, list):
            for item in items:
                update = isinstance(item, dict) and item.get('type') == 'configuration_update'
                if update:
                    if adjacent or set(item) - {'id', 'type', 'reasoning'} or set(item.get('reasoning', {})) != {'effort'}:
                        raise Violation('invalid_reasoning_configuration_update')
                    updates.append(item['reasoning']['effort'])
                adjacent = update
        if updates:
            # GPT-6 updates select effective effort; request-level effort stays
            # fixed for prefix caching. Validate both fields, never assume the
            # carrier (the installed SDK uses medium) is the effective effort.
            if not contract['model'].startswith('gpt-6') or request.get('truncation', 'disabled') != 'disabled' or request.get('context_management'):
                raise Violation('unsupported_reasoning_configuration_update')
            valid = reasoning.get('effort') in ('low', 'medium', 'high', 'xhigh', 'max') and all(value == effort for value in updates)
            encoding = 'input_configuration_updates'
        else:
            valid = reasoning.get('effort') == effort
    elif protocol == 'openai-chat-completions':
        valid = request.get('reasoning_effort') == effort and request.get('thinking', {}).get('type') == 'enabled'
    else:
        raise Violation('unsupported_budget_route')
    if not valid:
        raise Violation('reasoning_setting_changed')
    for tool in request.get('tools', []):
        if protocol == 'anthropic-messages':
            name = tool.get('name') if tool.get('type') in (None, 'custom') else None
        elif tool.get('type') == 'function':
            name = tool.get('name') if protocol in ('openai-responses', 'openai-codex-responses') else tool.get('function', {}).get('name')
        else:
            name = None
        if name not in contract['allowed_tool_names']:
            raise Violation('undeclared_provider_tool')
    return {'cap': cap, 'stream': request.get('stream', False), 'reasoning_encoding': encoding,
            'effective_reasoning_effort': effort}


class Gate:
    """Request-linked ledger; a rejected request can never be forwarded/retried."""
    def __init__(self, contract, *, clock=time.monotonic):
        self.contract = copy.deepcopy(contract)
        self.clock, self.started = clock, clock()
        self.requests, self.observations, self.seen = [], [], set()
        self.lock = threading.RLock()
        self.failed = False
        self.in_flight = None
        self.usage_by_request = {}
        self.exhausted = None

    def task_usage(self):
        """Charge final known output once, reserving the full cap if uncertain.

        The reported-total threshold is a stop-before-the-next-turn rule. Input
        usage is not known before a provider request and may cross that threshold
        in the final admitted request; it is not a hard invoice ceiling.
        """
        output, observed = 0, 0
        for row in self.requests:
            if not row.get('forwarded'):
                continue
            usage = self.usage_by_request.get(row['request_id'], {})
            final_output = (row.get('response_complete') is True and not row.get('transport_error')
                            and row.get('http_status', 200) is not None and row.get('http_status', 200) < 300
                            and 'outputTokens' in usage)
            output += usage['outputTokens'] if final_output else (row['cap'] or usage.get('outputTokens', 0))
            observed += usage.get('totalTokens', sum(usage.get(k, 0) for k in
                                    ('inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens')))
        return {'output_tokens_charged_or_reserved': output, 'observed_total_tokens': observed,
                'model_turns_forwarded': sum(bool(r.get('forwarded')) for r in self.requests)}

    def remaining(self):
        return self.contract['wall_seconds'] - (self.clock() - self.started)

    def wallet_usage(self):
        """Fixed-price protection estimate, never a provider invoice.

        Count each request's latest cumulative components once. Unknown usage
        cannot authorize another request; final response/usage settles first.
        """
        wallet = self.contract.get('wallet')
        if not wallet:
            return None
        rates, cost, complete = wallet['rates_nano_per_token'], 0, True
        latest = {o['request_id']: o for o in self.observations}
        for row in self.requests:
            if not row.get('forwarded'):
                continue
            item = latest.get(row['request_id'], {})
            complete &= (item.get('complete') is True and row.get('response_complete') is True
                         and not row.get('transport_error'))
            cost += sum(item.get('usage', {}).get(key, 0) * rate for key, rate in rates.items())
        return {'currency': wallet['currency'], 'estimated_cost_nano': cost,
                'limit_nano': wallet['limit_nano'], 'usage_complete': bool(complete),
                'semantics': wallet['semantics']}

    def begin(self, path, raw):
        with self.lock:
            row = {'sequence': len(self.requests) + 1, 'request_id': str(len(self.requests) + 1),
                   'protocol': self.contract['protocol'], 'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   'forwarded': False, 'contract_sha256': digest(self.contract),
                   'request_sha256': hashlib.sha256(raw).hexdigest()}
            previous = self.requests[-1] if self.requests else None
            recovery = self.contract.get('transport_recovery')
            retry = bool(recovery and self.failed and previous
                         and previous.get('transport_retry_eligible') is True
                         and previous.get('request_sha256') == row['request_sha256']
                         and previous.get('transport_diagnostic', {}).get('cause') in recovery['allowed_causes']
                         and not previous.get('budget_violation') and not previous.get('rejected')
                         and previous.get('retry_index', 0) < recovery['max_retries_per_request']
                         and sum('retry_of' in r for r in self.requests) < recovery['max_retries_per_task'])
            self.requests.append(row)
            try:
                if self.failed and not retry:
                    raise Violation('retry_after_transport_failure_denied')
                if self.exhausted:
                    raise Violation(self.exhausted)
                if self.remaining() <= 0:
                    raise Violation('wall_limit_reached')
                if self.contract['max_requests'] is not None and row['sequence'] > self.contract['max_requests']:
                    raise Violation('model_turn_limit_reached')
                if self.in_flight is not None:
                    raise Violation('concurrent_provider_requests_denied')
                if len(raw) > self.contract['max_request_bytes']:
                    raise Violation('request_body_too_large')
                if row['request_sha256'] in self.seen and not retry:
                    raise Violation('duplicate_request_retry_denied')
                request = decode(raw)
                row.update(validate(self.contract, path, request))
                limits, usage = self.contract['task_limits'], self.task_usage()
                wallet = self.wallet_usage()
                if wallet and not wallet['usage_complete']:
                    raise Violation('wallet_usage_unavailable')
                if wallet and wallet['limit_nano'] is not None and wallet['estimated_cost_nano'] >= wallet['limit_nano']:
                    raise Violation('task_wallet_limit_reached')
                if not wallet and limits['max_observed_total_tokens'] is not None and usage['observed_total_tokens'] >= limits['max_observed_total_tokens']:
                    raise Violation('task_observed_token_limit_reached')
                if (self.contract.get('observed_response_ceiling') is not None
                        and any(u.get('outputTokens', 0) >= self.contract['observed_response_ceiling'] for u in self.usage_by_request.values())):
                    raise Violation('response_observed_output_limit_reached')
                if self.contract.get('response_allocation') == 'remaining_task_output_v1':
                    # Validate the client's original ceiling first. Only the
                    # cap may change, and only downwards to the task remainder.
                    remaining = limits['max_output_tokens'] - usage['output_tokens_charged_or_reserved']
                    if row['cap'] is None or remaining < self.contract['cap_minimum']:
                        raise Violation('task_output_budget_reached')
                    if row['cap'] > remaining:
                        row['requested_cap'] = row['cap']
                        row['cap'] = remaining
                        request[self.contract['cap_field']] = remaining
                        row['original_request_sha256'] = row['request_sha256']
                        row['request_sha256'] = digest(request)
                if limits['max_output_tokens'] is not None and ((observed_only(self.contract) and usage['output_tokens_charged_or_reserved'] >= limits['max_output_tokens'])
                        or (row['cap'] is not None and usage['output_tokens_charged_or_reserved'] + row['cap'] > limits['max_output_tokens'])):
                    raise Violation('task_output_budget_reached')
            except (Violation, AttributeError, TypeError) as error:
                category = str(error) if isinstance(error, Violation) else 'malformed_request_fields'
                if category in BUDGET_STOPS:
                    self.exhausted = category
                row.update(rejected=category, finished_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
                raise Violation(category) from None
            if retry:
                row.update(retry_of=previous['request_id'],
                           retry_root=previous.get('retry_root', previous['request_id']),
                           retry_index=previous.get('retry_index', 0) + 1)
                previous['recovery_request_id'] = row['request_id']
                # Original error and partial usage stay in their original row.
                # SDK recovery discards incomplete assistant content/tool calls.
                self.failed = False
            self.seen.add(row.get('original_request_sha256', row['request_sha256']))
            self.in_flight = row['sequence']
            return row, request

    def forward(self, row, *, fixture=False):
        with self.lock:
            if row is not self.requests[row['sequence'] - 1] or row.get('rejected') or row.get('forwarded') or self.in_flight != row['sequence']:
                raise Violation('invalid_forward_transition')
            if self.remaining() <= 0:
                self.finish(row, complete=False, error='wall_limit_reached')
                raise Violation('wall_limit_reached')
            row['forwarded'] = True
            row['upstream_kind'] = 'offline_fixture' if fixture else 'provider'

    def finish(self, row, *, status=None, complete=True, error=None):
        with self.lock:
            row.update(http_status=status if status is not None else row.get('http_status'), finished_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       response_complete=complete)
            if error:
                row['transport_error'] = error
            if (observed_only(self.contract) and row.get('forwarded') and complete
                    and not {'outputTokens', 'totalTokens'} <= self.usage_by_request.get(row['request_id'], {}).keys()):
                row['budget_violation'] = 'uncapped_usage_unavailable'
                self.failed = True
            if not complete or (status is not None and status >= 300):
                self.failed = True
                for observation in self.observations:
                    if observation['request_id'] == row['request_id']:
                        observation['complete'] = False
            if self.in_flight == row['sequence']:
                self.in_flight = None
            # A completed transport can contain an incomplete model answer.
            # Wait for the whole response/usage before ending the attempt; a
            # partial stream or provider error must never masquerade as a cap.
            if (row.get('response_output_limit_reached') and complete and not error
                    and status is not None and 200 <= status < 300 and not self.failed):
                self.exhausted = 'response_output_limit_reached'

    def observe(self, row, raw):
        """Preserve cumulative provider usage under its actual request ID."""
        if self.contract['protocol'] in ('openai-responses', 'openai-codex-responses'):
            usage = {'inputTokens': raw.get('input_tokens'), 'outputTokens': raw.get('output_tokens'),
                     'cacheReadTokens': (raw.get('input_tokens_details') or {}).get('cached_tokens'),
                     'totalTokens': raw.get('total_tokens')}
            if usage['inputTokens'] is not None and usage['cacheReadTokens'] is not None:
                usage['inputTokens'] -= usage['cacheReadTokens']
        elif self.contract['protocol'] == 'anthropic-messages':
            usage = {'inputTokens': raw.get('input_tokens'), 'outputTokens': raw.get('output_tokens'),
                     'cacheReadTokens': raw.get('cache_read_input_tokens'), 'cacheWriteTokens': raw.get('cache_creation_input_tokens')}
        else:
            cached = (raw.get('prompt_tokens_details') or {}).get('cached_tokens')
            usage = {'inputTokens': raw.get('prompt_tokens'), 'outputTokens': raw.get('completion_tokens'),
                     'cacheReadTokens': cached, 'totalTokens': raw.get('total_tokens')}
            if usage['inputTokens'] is not None and cached is not None:
                usage['inputTokens'] -= cached
        usage = {key: value for key, value in usage.items() if value is not None}
        if not usage:
            return
        if any(type(value) is not int or value < 0 for value in usage.values()):
            raise Violation('invalid_provider_usage')
        previous = self.usage_by_request.setdefault(row['request_id'], {})
        for key, value in usage.items():
            if key in previous and value < previous[key]:
                raise Violation('decreasing_provider_usage')
            previous[key] = value
        usage = dict(previous)
        if 'totalTokens' not in usage and 'inputTokens' in usage and 'outputTokens' in usage:
            usage['totalTokens'] = sum(usage.get(key, 0) for key in ('inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens'))
        required = {'inputTokens', 'outputTokens', 'cacheReadTokens'}
        if self.contract['protocol'] == 'anthropic-messages':
            required.add('cacheWriteTokens')
        complete = required <= previous.keys()
        if row['cap'] is not None and usage.get('outputTokens', 0) > row['cap']:
            self.failed = True
            row['budget_violation'] = 'reported_output_exceeds_cap'
            raise Violation('reported_output_exceeds_cap')
        self.observations.append({'sequence': row['sequence'], 'request_id': row['request_id'],
                                  'finished_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                  'usage': usage, 'usage_semantics': 'cumulative', 'complete': complete,
                                  'token_scope': 'provider_total' if 'totalTokens' in previous else 'known_component_subtotal'})
        # Usage is cumulative response evidence, not permission to interrupt the
        # response being delivered. Every route denies the next request in begin;
        # summary records any overrun separately from final-answer completion.

    def summary(self):
        forwards = sum(bool(row['forwarded']) and row.get('upstream_kind') == 'provider' for row in self.requests)
        validated = sum(bool(row['forwarded']) for row in self.requests)
        rejected = sum('rejected' in row for row in self.requests)
        invalid = (any(r.get('rejected') not in BUDGET_STOPS for r in self.requests if r.get('rejected'))
                   or any(r.get('budget_violation') for r in self.requests)
                   or (observed_only(self.contract) and self.failed and not self.exhausted))
        totals = self.task_usage()
        limits = dict(self.contract['task_limits'])
        wallet = self.wallet_usage()
        measurements = {'max_observed_total_tokens': totals['observed_total_tokens'],
                        'max_output_tokens': totals['output_tokens_charged_or_reserved']}
        reasons = {'max_observed_total_tokens': 'task_observed_token_limit_reached',
                   'max_output_tokens': 'task_output_budget_reached'}
        if wallet:
            measurements.pop('max_observed_total_tokens')
            if wallet['limit_nano'] is not None:
                measurements['wallet_cost_nano'] = wallet['estimated_cost_nano']
                limits['wallet_cost_nano'] = wallet['limit_nano']
                reasons['wallet_cost_nano'] = 'task_wallet_limit_reached'
            invalid |= not wallet['usage_complete']
        if self.contract.get('observed_response_ceiling') is not None:
            measurements['response_output_tokens'] = max((u.get('outputTokens', 0) for u in self.usage_by_request.values()), default=0)
            limits['response_output_tokens'] = self.contract['observed_response_ceiling']
            reasons['response_output_tokens'] = 'response_observed_output_limit_reached'
        measurements = {name: value for name, value in measurements.items() if limits[name] is not None}
        reached = [name for name, observed in measurements.items()
                   if observed >= limits[name]]
        excess = {name: {'limit': limits[name], 'observed': observed,
                         'excess': observed - limits[name]}
                  for name, observed in measurements.items() if observed > limits[name]}
        forwarded = [row for row in self.requests if row.get('forwarded')]
        final = bool(forwarded and forwarded[-1].get('final_response') is True
                     and forwarded[-1].get('response_complete') is True
                     and not self.failed and self.in_flight is None)
        usage_complete = complete_budget_usage({'requests': self.requests,
                                               'usage_observations': self.observations})
        recovery_settled = settled_recovery_usage(
            {'requests': self.requests, 'usage_observations': self.observations}, self.contract)
        invalid = invalid or bool(excess and final and not usage_complete and not recovery_settled)
        stop = self.exhausted or (reasons[reached[0]] if reached and not final else None)
        status = ('rejected' if invalid else 'budget_exhausted' if stop else
                  'completed_over_budget' if excess and final else
                  success_status(self.contract) if validated else 'no_provider_forwards')
        receipt = {'schema_version': 1, 'status': status,
                'stop_reason': stop, 'task_usage': totals,
                **({'wallet_usage': wallet} if wallet else {}),
                'budget_exceeded': bool(excess), 'budget_excess': excess,
                'budget_usage_complete': usage_complete,
                'final_response_complete': final,
                'queue_stop_required': bool(self.exhausted or reached),
                'queue_stop_reason': self.exhausted or (reasons[reached[0]] if reached else None),
                'contract_sha256': digest(self.contract), 'contract': self.contract,
                'provider_forwards': forwards, 'validated_forwards': validated, 'rejected_requests': rejected,
                'requests': self.requests, 'usage_observations': self.observations,
                'request_ledger_complete': self.in_flight is None,
                'transport_recovery_attempts': sum('retry_of' in r for r in self.requests),
                'recovered_request_ids': sorted(recovered_transport_errors({'requests': self.requests}, self.contract)),
                'transport_complete': not self.failed and self.in_flight is None,
                'provider_output_cap_enforced': not observed_only(self.contract),
                'remote_enforcement_independently_verified': False}
        if self.contract.get('deadline_outcome') == 'completed_not_passed' and self.remaining() <= 0 and not final:
            candidate = {**receipt, 'status': 'deadline_exceeded',
                         'deadline_elapsed_seconds': self.clock() - self.started}
            if deadline_grade_allowed(candidate, self.contract):
                receipt = {**candidate, 'queue_stop_required': False, 'queue_stop_reason': None}
        return receipt


class ResponseRelay:
    """Stream ordinary SSE frames, retaining completion until ledger settlement.

    A client may start its next request as soon as it sees a terminal event,
    before the upstream HTTP reader reaches EOF. Never expose that event while
    the previous request still owns the gate or has unsettled usage.
    """
    def __init__(self, observer):
        self.observer = observer
        self.pending = bytearray()
        self.frame = bytearray()
        self.tail = bytearray()
        self.holding = False

    def feed(self, chunk):
        if not self.observer.row['stream']:
            self.observer.feed(chunk)
            self.tail.extend(chunk)
            return b''
        self.pending.extend(chunk)
        ready = bytearray()
        while True:
            end = self.pending.find(b'\n')
            if end < 0:
                break
            line = self.pending[:end + 1]
            del self.pending[:end + 1]
            self.frame.extend(line)
            if line not in (b'\n', b'\r\n'):
                continue
            self.observer.feed(self.frame)
            self.holding = (self.holding or self.observer.complete
                            or bool(self.observer.row.get('finish_reason'))
                            or any(self.observer.choice_finishes.values()))
            (self.tail if self.holding else ready).extend(self.frame)
            self.frame.clear()
        if len(self.pending) + len(self.frame) + len(self.tail) > 67108864:
            raise Violation('provider_event_too_large')
        return bytes(ready)

    def end(self):
        if self.observer.row['stream']:
            remaining = self.frame + self.pending
            self.observer.feed(remaining)
            self.tail.extend(remaining)
            self.frame.clear()
            self.pending.clear()
        complete = self.observer.end()
        # An incomplete/error response must never publish a success terminator.
        return complete, bytes(self.tail) if complete else b''


class ResponseObserver:
    """Incremental SSE/JSON completion and usage parsing, without clipping output."""
    def __init__(self, gate, row):
        self.gate, self.row = gate, row
        self.buffer = bytearray()
        self.complete = False
        self.message_output = False
        self.tool_output = False
        self.choice_finishes = {}

    def event(self, value):
        if not isinstance(value, dict):
            return
        response = value.get('response', {})
        protocol = self.gate.contract['protocol']
        if protocol == 'anthropic-messages':
            reason = value.get('stop_reason') or value.get('delta', {}).get('stop_reason')
            if reason:
                self.row['finish_reason'] = reason
                if reason == 'max_tokens':
                    self.row['response_output_limit_reached'] = True
                self.row['final_response'] = reason in ('end_turn', 'stop_sequence')
        elif protocol in ('openai-responses', 'openai-codex-responses'):
            items = response.get('output', [])
            if isinstance(value.get('item'), dict):
                items = [*items, value['item']]
            for item in items:
                kind = item.get('type')
                if kind == 'message':
                    self.message_output = True
                elif kind != 'reasoning':
                    self.tool_output = True
            if value.get('type') in ('response.completed', 'response.incomplete'):
                if (value.get('type') == 'response.incomplete'
                        and (response.get('incomplete_details') or {}).get('reason') == 'max_output_tokens'):
                    self.row['finish_reason'] = 'max_output_tokens'
                    self.row['response_output_limit_reached'] = True
                self.row['final_response'] = (value.get('type') == 'response.completed'
                    and response.get('status', 'completed') == 'completed'
                    and self.message_output and not self.tool_output)
            elif not self.row['stream'] and value.get('object') == 'response':
                if (value.get('status') == 'incomplete'
                        and (value.get('incomplete_details') or {}).get('reason') == 'max_output_tokens'):
                    self.row['finish_reason'] = 'max_output_tokens'
                    self.row['response_output_limit_reached'] = True
                self.row['final_response'] = (value.get('status') == 'completed'
                    and any(item.get('type') == 'message' for item in value.get('output', []))
                    and all(item.get('type') in ('message', 'reasoning') for item in value.get('output', [])))
        else:
            for choice in value.get('choices', []):
                index = choice.get('index', 0)
                self.choice_finishes.setdefault(index, None)
                if choice.get('finish_reason'):
                    self.choice_finishes[index] = choice['finish_reason']
                    if choice['finish_reason'] == 'length':
                        self.row['finish_reason'] = 'length'
                        self.row['response_output_limit_reached'] = True
            if self.choice_finishes:
                self.row['final_response'] = all(reason == 'stop' for reason in self.choice_finishes.values())
        usage = value.get('usage') or value.get('message', {}).get('usage') or response.get('usage')
        if usage:
            self.gate.observe(self.row, usage)
        if value.get('type') in ('message_stop', 'response.completed', 'response.incomplete'):
            self.complete = True
        if value.get('type') in ('error', 'response.failed') or value.get('error'):
            raise Violation('provider_response_error')

    def feed(self, chunk):
        self.buffer.extend(chunk)
        if not self.row['stream']:
            if len(self.buffer) > 67108864:
                raise Violation('provider_response_too_large')
            return
        while b'\n' in self.buffer:
            line, _, rest = self.buffer.partition(b'\n')
            self.buffer = bytearray(rest)
            if line.startswith(b'data:'):
                raw = line[5:].strip()
                if raw == b'[DONE]' and self.gate.contract['protocol'] == 'openai-chat-completions':
                    self.complete = True
                elif raw == b'[DONE]':
                    pass
                elif raw:
                    self.event(decode(raw))
        if len(self.buffer) > 67108864:
            raise Violation('provider_event_too_large')

    def end(self):
        if not self.row['stream']:
            self.event(decode(self.buffer))
            self.complete = True
        elif self.buffer.strip():
            self.feed(b'\n')
        return self.complete
