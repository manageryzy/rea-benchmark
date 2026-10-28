"""Safe presentation of saved request limits; never certify against current policy."""
from collections import Counter
import hashlib
from html import escape
import json
from pathlib import Path
import re

import records
import request_contract

MODES = {'unknown', 'provider_response_cap', 'observed_task_limits'}
EVIDENCE = {'unknown', 'recorded_contract', 'recorded_request_receipt'}
PROTOCOLS = {'anthropic-messages', 'openai-responses', 'openai-chat-completions', 'openai-codex-responses'}
NUMBERS = ('per_response_output_tokens', 'max_model_turns', 'max_task_output_tokens',
           'max_observed_total_tokens', 'wall_seconds', 'tool_calls')
FIELDS = ('mode', 'evidence', 'protocol', 'provider_output_cap_enforced', *NUMBERS)
LABELS = {'unknown': 'Budget condition unknown', 'provider_response_cap': 'Provider response cap',
          'observed_task_limits': 'Observed task limits; no provider response cap'}
OUTCOME_FIELDS = ('status', 'budget_exceeded', 'queue_stop_required', 'queue_stop_reason')
OUTCOME_STATUSES = {'wire_enforced', 'observed_limits_enforced', 'budget_exhausted', 'completed_over_budget', 'deadline_exceeded'}
WALLET_FIELDS = ('currency', 'limit_nano', 'semantics')


ADAPTIVE_FIELDS = ('mode', 'metric', 'sample_count', 'multiplier', 'limit', 'baseline_sha256')


def validate_adaptive(value):
    if (not isinstance(value, dict) or set(value) != set(ADAPTIVE_FIELDS)
            or value['mode'] not in ('basic_turn_only', 'type_mean')
            or value['metric'] not in ('fixed_price_cost', 'reported_total_tokens')
            or type(value['sample_count']) is not int or value['sample_count'] < 0
            or type(value['multiplier']) is not int or value['multiplier'] != 2
            or not isinstance(value['baseline_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['baseline_sha256'])
            or (value['limit'] is None) != (value['sample_count'] == 0)
            or value['limit'] is not None and (type(value['limit']) is not int or value['limit'] <= 0)
            or value['mode'] == 'basic_turn_only' and value['sample_count'] != 0):
        raise ValueError('Invalid adaptive budget summary')
    return value


def validate_wallet(value, *, bootstrap=False):
    if (not isinstance(value, dict) or set(value) != set(WALLET_FIELDS)
            or value['currency'] not in ('USD', 'CNY')
            or not (bootstrap and value['limit_nano'] is None or type(value['limit_nano']) is int and 0 < value['limit_nano'] <= 10**12)
            or value['semantics'] != 'observed_cost_stop_before_next'):
        raise ValueError('Invalid public wallet condition')
    return value


def validate_outcome(value):
    if (not isinstance(value, dict) or set(value) != set(OUTCOME_FIELDS)
            or value['status'] not in OUTCOME_STATUSES
            or type(value['budget_exceeded']) is not bool
            or type(value['queue_stop_required']) is not bool
            or value['queue_stop_reason'] is not None and value['queue_stop_reason'] not in request_contract.BUDGET_STOPS
            or value['queue_stop_required'] != (value['queue_stop_reason'] is not None)
            or value['status'] == 'completed_over_budget' and not (value['budget_exceeded'] and value['queue_stop_required'])
            or value['status'] in ('wire_enforced', 'observed_limits_enforced') and value['budget_exceeded']
            or value['budget_exceeded'] and not value['queue_stop_required'] and value['status'] != 'deadline_exceeded'):
        raise ValueError('Invalid saved budget outcome')
    return value


def outcome(receipt, contract):
    """Publish only recorded, contract-bound flags; never infer historical compliance."""
    if (receipt.get('contract') != contract or receipt.get('contract_sha256') != digest(contract)
            or not all(key in receipt for key in OUTCOME_FIELDS)):
        return None
    try:
        value = validate_outcome({key: receipt[key] for key in OUTCOME_FIELDS})
        if value['status'] in ('completed_over_budget', 'deadline_exceeded') and not request_contract.grading_allowed(receipt, contract):
            return None
        return value
    except (ValueError, TypeError, KeyError):
        return None


def outcome_label(value):
    if not value:
        return ''
    if value.get('status') == 'deadline_exceeded':
        return 'Deadline reached / completed, not passed'
    if value.get('budget_exceeded'):
        return 'Budget exceeded / attempt stopped'
    if value.get('queue_stop_required'):
        return 'Budget limit reached / attempt stopped'
    return ''


def unknown():
    return {'mode': 'unknown', 'evidence': 'unknown', 'protocol': None,
            'provider_output_cap_enforced': None, **dict.fromkeys(NUMBERS)}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def positive(value):
    return value if type(value) is int and 0 < value <= 10**9 else None


def summarize(task, receipt):
    """Use only a frozen contract and, when present, its matching saved receipt.

    This labels local enforcement evidence, not a remote-provider guarantee.
    Missing historical identities stay unknown even if today's policy is known.
    """
    result = unknown()
    if not isinstance(task, dict) or not isinstance(receipt, dict):
        return result
    contract = task.get('request_contract')
    if not isinstance(contract, dict) or contract.get('protocol') not in PROTOCOLS:
        return result
    observed = (contract.get('mode') == 'observed_task_limits'
        and contract.get('protocol') == 'openai-codex-responses'
        and contract.get('cap_scope') == 'none'
        and all(contract.get(key) is None for key in ('cap_field', 'cap_minimum', 'output_ceiling')))
    capped = (contract.get('mode') == 'certified' and contract.get('cap_scope') == 'per_response'
        and contract.get('cap_field') in ('max_tokens', 'max_completion_tokens', 'max_output_tokens')
        and positive(contract.get('output_ceiling')) is not None)
    if not observed and not capped:
        return result
    limits = contract.get('task_limits', {})
    if not isinstance(limits, dict):
        return result
    result.update(mode='observed_task_limits' if observed else 'provider_response_cap',
        evidence='recorded_contract', protocol=contract['protocol'],
        provider_output_cap_enforced=False if observed else None,
        per_response_output_tokens=None if observed else positive(contract['output_ceiling']),
        max_model_turns=positive(limits.get('max_model_turns')),
        max_task_output_tokens=positive(limits.get('max_output_tokens')),
        max_observed_total_tokens=positive(limits.get('max_observed_total_tokens')),
        wall_seconds=positive(contract.get('wall_seconds')), tool_calls=positive(contract.get('tool_calls')))
    if contract.get('adaptive_budget'):
        result['adaptive'] = validate_adaptive({key: contract['adaptive_budget'].get(key) for key in ADAPTIVE_FIELDS})
    if contract.get('observed_response_ceiling') is not None:
        result['observed_response_output_tokens'] = positive(contract['observed_response_ceiling'])
    if contract.get('wallet') and contract.get('total_token_limit_enforced') is False:
        result['wallet'] = validate_wallet({key: contract['wallet'].get(key) for key in WALLET_FIELDS}, bootstrap=result.get('adaptive', {}).get('sample_count') == 0)
        result['max_observed_total_tokens'] = None
    if contract.get('response_allocation') == 'remaining_task_output_v1':
        result['response_allocation'] = contract['response_allocation']
    status = ('observed_limits_enforced', 'budget_exhausted', 'completed_over_budget') if observed else ('wire_enforced', 'budget_exhausted', 'completed_over_budget')
    status += ('deadline_exceeded',)
    saved_outcome = outcome(receipt, contract)
    if (receipt.get('contract') == contract and receipt.get('contract_sha256') == digest(contract)
            and type(receipt.get('provider_forwards')) is int and receipt['provider_forwards'] > 0
            and receipt.get('status') in status
            and (receipt.get('status') not in ('completed_over_budget', 'deadline_exceeded') or saved_outcome is not None)
            and (not observed or receipt.get('provider_output_cap_enforced') is False)):
        result['evidence'] = 'recorded_request_receipt'
        result['provider_output_cap_enforced'] = not observed
        if saved_outcome:
            result['outcome'] = saved_outcome
    return result


def validate(value):
    if (not isinstance(value, dict) or not set(FIELDS) <= set(value) or set(value) - set(FIELDS) - {'outcome', 'wallet', 'response_allocation', 'adaptive', 'observed_response_output_tokens'}
            or value['mode'] not in MODES or value['evidence'] not in EVIDENCE
            or value['protocol'] is not None and value['protocol'] not in PROTOCOLS
            or type(value['provider_output_cap_enforced']) not in (bool, type(None))
            or any(value[key] is not None and positive(value[key]) is None for key in NUMBERS)):
        raise ValueError('Invalid public budget condition')
    if value['mode'] == 'unknown' and value != unknown():
        raise ValueError('Unknown historical budgets cannot acquire inferred limits')
    if value['mode'] == 'observed_task_limits' and (
            value['protocol'] != 'openai-codex-responses'
            or value['provider_output_cap_enforced'] is not False
            or value['per_response_output_tokens'] is not None):
        raise ValueError('Observed task limits cannot claim a provider response cap')
    if value['mode'] == 'provider_response_cap' and (
            value['per_response_output_tokens'] is None
            or value['provider_output_cap_enforced'] is False
            or value['provider_output_cap_enforced'] is True and value['evidence'] != 'recorded_request_receipt'):
        raise ValueError('Provider cap claim lacks recorded request evidence')
    if 'outcome' in value:
        validate_outcome(value['outcome'])
        if value['evidence'] != 'recorded_request_receipt':
            raise ValueError('Budget outcome lacks recorded request evidence')
    if 'wallet' in value:
        validate_wallet(value['wallet'], bootstrap=value.get('adaptive', {}).get('sample_count') == 0)
        if value['mode'] == 'unknown' or value['max_observed_total_tokens'] is not None:
            raise ValueError('Wallet condition must replace the raw total-token threshold')
    if 'adaptive' in value:
        a = validate_adaptive(value['adaptive'])
        if value['mode'] == 'unknown' or value['max_task_output_tokens'] is not None:
            raise ValueError('Adaptive budgets cannot inherit a legacy output total')
        if a['mode'] == 'basic_turn_only':
            if 'wallet' in value or value['max_observed_total_tokens'] is not None:
                raise ValueError('Basic condition cannot impose aggregate limits')
        elif a['metric'] == 'fixed_price_cost':
            if value.get('wallet', {}).get('limit_nano') != a['limit'] or 'wallet' not in value:
                raise ValueError('Adaptive wallet summary differs from saved limit')
        elif value['max_observed_total_tokens'] != a['limit']:
            raise ValueError('Adaptive token summary differs from saved limit')
    if 'observed_response_output_tokens' in value and (value['mode'] != 'observed_task_limits'
            or positive(value['observed_response_output_tokens']) is None):
        raise ValueError('Invalid observed response threshold')
    if 'response_allocation' in value and (value['response_allocation'] != 'remaining_task_output_v1'
            or 'wallet' not in value or value['mode'] != 'provider_response_cap'):
        raise ValueError('Invalid response allocation condition')
    return value


def project(value):
    result = {key: value.get(key) for key in FIELDS}
    if 'outcome' in value:
        result['outcome'] = {key: value['outcome'].get(key) for key in OUTCOME_FIELDS}
    if 'wallet' in value:
        result['wallet'] = {key: value['wallet'].get(key) for key in WALLET_FIELDS}
    if 'response_allocation' in value:
        result['response_allocation'] = value['response_allocation']
    if 'adaptive' in value:
        result['adaptive'] = {key: value['adaptive'].get(key) for key in ADAPTIVE_FIELDS}
    if 'observed_response_output_tokens' in value:
        result['observed_response_output_tokens'] = value['observed_response_output_tokens']
    return validate(result)


def prepare(data, *, read=records.read, root=None):
    root = Path(root or records.ROOT)
    def load(directory):
        try:
            path = root / directory
            if not path.resolve().is_relative_to(root.resolve()):
                return unknown()
            return summarize(read(path / 'task.json', {}) or {}, read(path / 'request-budget.json', {}) or {})
        except (OSError, ValueError, TypeError):
            return unknown()
    for row in data['records'] + data.get('condition_records', []):
        row['budget_condition'] = load(row['directory']) if row.get('directory') and row['status'] != 'not_started' else unknown()
    history = {row['attempt_id']: row for row in data.get('capability_measurements', {}).get('history', [])}
    summaries = {}
    for path in (root / 'benchmarks/runs/capabilities-v1').glob('*/*/*/*/task.json'):
        if not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            task = read(path, {}) or {}
            key = task.get('fingerprint')
            if key not in history or task.get('model') != history[key]['model'] or task.get('offline_fixture'):
                continue
            value = load(str(path.parent.relative_to(root)))
            summaries[key] = value if key not in summaries or summaries[key] == value else unknown()
        except (OSError, ValueError, TypeError):
            continue
    data['typed_budget_conditions'] = {key: summaries.get(key, unknown()) for key in sorted(history)}


def description(value):
    value = value or unknown()
    label = LABELS[value['mode']]
    if value['mode'] == 'provider_response_cap':
        label += ' · ' + str(value['per_response_output_tokens']) + ' tokens/response'
        label += ' · saved request evidence' if value['provider_output_cap_enforced'] else ' · contract only; execution unverified'
    elif value['mode'] == 'observed_task_limits':
        label += ' · saved request evidence' if value['evidence'] == 'recorded_request_receipt' else ' · contract only'
    if outcome_label(value.get('outcome')):
        label += ' · ' + outcome_label(value['outcome'])
    if value.get('wallet'):
        w = value['wallet']
        label += (f" · wallet estimate {w['currency']} {w['limit_nano']/10**9:.7g}/case" if w['limit_nano'] is not None else ' · bootstrap: no aggregate wallet limit')
    if value.get('adaptive'):
        a = value['adaptive']
        label += (' · Basic: no aggregate token/cost cap' if a['mode'] == 'basic_turn_only' else f" · 2× same-model/type mean; {a['sample_count']} complete samples")
    if value.get('adaptive'):
        label += (' · no turn-count limit' if value['max_model_turns'] is None else f" · {value['max_model_turns']} turns")
    if value.get('observed_response_output_tokens'):
        label += f" · observed response threshold {value['observed_response_output_tokens']:,}"
    return label


def detail(value):
    value = value or unknown()
    body = '<p class="notice">' + escape(description(value)) + '.</p>'
    if value.get('outcome', {}).get('status') == 'deadline_exceeded':
        body += '<p><strong>Deadline reached / completed, not passed.</strong> No complete answer arrived within the case time limit. Correctness is zero; partial output is retained as evidence, not graded as a completed answer. The next case can continue. API and transport failures remain execution failures. Usage may be incomplete when the final response was interrupted.</p>'
    elif outcome_label(value.get('outcome')):
        compliance = ('This does not make the attempt budget-compliant.' if value['outcome']['budget_exceeded']
                      else 'The recorded threshold was reached without an observed excess.')
        body += '<p><strong>' + escape(outcome_label(value['outcome'])) + '.</strong> A complete final candidate may retain its offline correctness grade. ' + compliance + ' No automatic retry of this attempt; the configured queue policy decides whether to continue to the next case.</p>'
    if value.get('wallet'):
        body += '<p>Cached input, uncached input and generated output are priced separately using frozen route-specific rates. Raw total tokens remain reported but do not stop this condition. The estimate stops the next request after the amount is reached; the final admitted response can overshoot. This is not a hard invoice cap. Response, tool and time guards remain active; the recorded condition determines whether an aggregate output limit applies.</p>'
    if value.get('adaptive'):
        body += '<p>The baseline is frozen before each case: twice the mean of complete answers from the same model, provider and task type, using the latest answer per case. Incorrect complete answers are included; incomplete stops are excluded. With no sample the first case has no aggregate threshold. Basic native cases have no aggregate token/cost threshold. Response, wall-time and tool guards remain. Turn counts apply only when recorded in this condition. Prior attempts keep their original limits.</p>'
    if value.get('response_allocation'):
        body += '<p>The response ceiling is reduced to the remaining task-output allowance before forwarding. Original and forwarded request hashes are both recorded. This permits normal long responses without increasing the cumulative output limit.</p>'
    if value['mode'] == 'observed_task_limits':
        body += '<p>Signed-in Codex OAuth account. Elapsed time and tool calls remain bounded; a turn limit applies only when recorded. Any recorded output or total-token thresholds stop later requests based on observed usage; one response can exceed those thresholds because no provider response cap is sent. Missing usage stops further requests. Cost is an API-equivalent estimate; actual subscription debit is unknown.</p>'
    elif value['mode'] == 'unknown':
        body += '<p>This saved attempt has no identifiable frozen request-limit condition. Current configuration does not certify historical limits.</p>'
    else:
        body += '<p>The recorded route declares an output cap in each provider request. Local request validation does not independently verify the provider’s internal enforcement.</p>'
    return body


def comparison_note(rows, catalog):
    parts = []
    for model in catalog:
        kinds = Counter(description(row.get('budget_condition')) for row in rows if row['model'] == model and row.get('grade'))
        if kinds:
            parts.append(escape(catalog[model]['label']) + ': ' + '; '.join(escape(label) + ' (' + str(count) + ')' for label, count in sorted(kinds.items())))
    return ('<p class="notice">Shared graded cases match task coverage only, not request budgets. Provider-capped, observed-limit and unknown historical conditions remain distinct; mixed profiles are descriptive model + harness comparisons, not equal-budget rankings.</p>'
        + '<details><summary>Request-limit conditions in these scores</summary><p>' + '<br>'.join(parts or ['No graded budget observations.']) + '</p></details>')
