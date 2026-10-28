"""Frozen per-case budgets from complete saved answers; never launches a provider.

Scan history once per worker/model. Thereafter only the worker's newly prepared
attempts are ingested. A baseline is frozen for the entire preparation/dispatch
of a case, and its exact sample hashes enter the inference identity.
"""
import copy
import hashlib
import json
from pathlib import Path

from benchmark_trace import traced

ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = ('inputTokens', 'cacheReadTokens', 'cacheWriteTokens', 'outputTokens', 'totalTokens')
_histories = {}
_frozen = {}
_pending = set()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def kind(suite, case):
    return {'suite': suite, 'kind': case.get('kind', 'native'),
            'source_language': case.get('source_language', case.get('language')),
            'output_language': case.get('output_language', case.get('language')),
            'submission': 'codegen' if case.get('profile_id', '').endswith('_codegen') else 'recover'}


def saved_suite(task):
    return {'basic': 'record-parser', 'complex': 'complex-native-v1',
            'stl': 'stl-native-v2', 'core': 'stl-native-v2',
            'capability': 'capabilities-v1'}.get(task.get('track'))


class History:
    def __init__(self, root, model, condition):
        self.root, self.model, self.condition = Path(root), model, condition
        self.rows = {}
        self.loaded = set()

    @traced('adaptive_budget:read_receipt')
    def ingest(self, directory):
        directory = Path(directory)
        result = directory / 'result.json'
        if str(directory) in self.loaded or '/' + self.model + '/' not in str(directory):
            return
        if not result.is_file() or not (directory / 'task.json').is_file():
            return
        try:
            raw = result.read_bytes(); value = json.loads(raw)
            task_raw = (directory / 'task.json').read_bytes(); task = json.loads(task_raw)
        except (OSError, ValueError):
            return  # A still-being-written receipt is not a baseline sample.
        self.loaded.add(str(directory))
        if (value.get('model') != self.model or value.get('condition') != self.condition
                or not value.get('usage_complete') or task.get('offline_fixture')):
            return
        budget = value.get('request_budget') or {}
        if (budget.get('final_response_complete') is not True
                or budget.get('transport_complete') is not True
                or budget.get('request_ledger_complete') is not True):
            return
        requests = [r for r in budget.get('requests', []) if r.get('forwarded')]
        if not requests or any(r.get('upstream_kind') != 'provider' for r in requests):
            return
        latest = {str(o['request_id']): o for o in budget.get('usage_observations', [])}
        tokens = dict.fromkeys(COMPONENTS, 0)
        for row in requests:
            obs = latest.get(str(row['request_id']), {}); usage = obs.get('usage', {})
            required = set(COMPONENTS) - {'cacheWriteTokens'}
            if budget.get('contract', {}).get('protocol') == 'anthropic-messages':
                required.add('cacheWriteTokens')
            if (obs.get('complete') is not True or row.get('response_complete') is not True
                    or row.get('transport_error') or any(type(usage.get(k)) is not int or usage[k] < 0 for k in required)):
                return
            for key in COMPONENTS:
                tokens[key] += usage.get(key, 0)
        if tokens['totalTokens'] != sum(tokens[k] for k in COMPONENTS[:-1]):
            return
        import request_contract
        contract = task.get('request_contract', {})
        if (budget.get('contract') != contract or budget.get('contract_sha256') != request_contract.digest(contract)
                or not request_contract.grading_allowed(budget, contract)):
            return
        suite = saved_suite(task)
        if suite is None:
            return
        grouping = kind(suite, task)
        case_id = task.get('task_id', task.get('id'))
        if not isinstance(case_id, str):
            return
        sample = {'case_id': case_id, 'type': grouping, 'provider': value.get('provider'),
                  'started_at': value['started_at'], 'tokens': tokens,
                  'result': str(result.relative_to(self.root)),
                  'result_sha256': hashlib.sha256(raw).hexdigest(),
                  'task_sha256': hashlib.sha256(task_raw).hexdigest()}
        key = (canonical(grouping), case_id, value.get('provider'))
        # Repeated attempts of a case are not extra votes in its type's mean.
        if key not in self.rows or sample['started_at'] > self.rows[key]['started_at']:
            self.rows[key] = sample

    @traced('adaptive_budget:initial_history')
    def load(self):
        runs = self.root / 'benchmarks/runs'
        # The pinned grading/runtime image intentionally has no search CLI.
        # Search only model roots in the two supported native/typed layouts.
        roots = list(runs.glob('*/' + self.model))
        roots += list(runs.glob('*/conditions/*/' + self.model))
        for model_root in sorted(set(roots)):
            # Legacy native: case/result. Action-native and typed: three
            # directory levels. Never walk wire logs, agent homes or tool trees.
            results = set(model_root.glob('*/result.json')) | set(model_root.glob('*/*/*/result.json'))
            for result in sorted(results):
                self.ingest(result.parent)



@traced('adaptive_budget:freeze_case')
def context(model, suite, case, condition, *, root=ROOT, completed=()):
    root = Path(root)
    policy = json.loads((root / 'benchmarks/config/request-contract.json').read_bytes()).get('adaptive_budget')
    if not policy or condition != policy['condition']:
        return None
    grouping = kind(suite, case)
    identity = (str(root), model['id'], case['id'], canonical(grouping), condition, canonical(policy))
    if identity in _frozen:
        return copy.deepcopy(_frozen[identity])
    mode = 'basic_turn_only' if suite in policy['basic_suites'] else 'type_mean'
    samples = []
    if mode == 'type_mean':
        hkey = (str(root), model['id'], condition)
        if hkey not in _histories:
            history = History(root, model['id'], condition); history.load(); _histories[hkey] = history
        history = _histories[hkey]
        for directory in sorted(_pending | {str(root / p) for p in completed}):
            history.ingest(directory)
        samples = sorted((s for s in history.rows.values() if s['type'] == grouping
                          and s['case_id'] != case['id']), key=lambda s: (s['case_id'], s['provider']))
    value = {'schema_version': 1, 'policy_id': policy['id'], 'model': model['id'],
             'mode': mode, 'type': grouping, 'samples': samples}
    value['sha256'] = hashlib.sha256(canonical(value)).hexdigest()
    _frozen[identity] = value
    return copy.deepcopy(value)


def track(directory):
    _pending.add(str(Path(directory)))


def resolve(value, provider, policy, wallet):
    """Validate the frozen baseline and derive a cap without reading files again."""
    unsigned = {k: v for k, v in value.items() if k != 'sha256'}
    if (value.get('sha256') != hashlib.sha256(canonical(unsigned)).hexdigest()
            or value.get('schema_version') != 1 or value.get('policy_id') != policy['id']
            or value.get('mode') not in ('basic_turn_only', 'type_mean')):
        raise ValueError('Invalid frozen adaptive budget baseline')
    if type(policy.get('multiplier')) is not int or policy['multiplier'] != 2:
        raise ValueError('Adaptive budget multiplier must be two')
    basic = value['type']['suite'] in policy['basic_suites']
    if basic != (value['mode'] == 'basic_turn_only') or basic and value['samples']:
        raise ValueError('Adaptive budget task class differs from declared suite')
    samples = [s for s in value['samples'] if s['provider'] == provider.get('provider')]
    if len({s['case_id'] for s in samples}) != len(samples) or any(s['type'] != value['type'] for s in samples):
        raise ValueError('Adaptive budget samples mix task types or repeat a case')
    for sample in samples:
        t = sample['tokens']
        if (set(t) != set(COMPONENTS) or any(type(n) is not int or n < 0 for n in t.values())
                or t['totalTokens'] != sum(t[k] for k in COMPONENTS[:-1])):
            raise ValueError('Invalid adaptive budget usage components')
    count = len(samples)
    metric = 'fixed_price_cost' if wallet else 'reported_total_tokens'
    total = sum(sum(s['tokens'][k] * rate for k, rate in wallet['rates_nano_per_token'].items())
                if wallet else s['tokens']['totalTokens'] for s in samples)
    limit = (policy['multiplier'] * total + count - 1) // count if count and not basic else None
    if limit is not None and limit <= 0:
        raise ValueError('Adaptive budget cannot use a zero-cost baseline')
    return {'mode': value['mode'], 'metric': metric, 'sample_count': count,
            'multiplier': policy['multiplier'], 'limit': limit, 'baseline_sha256': value['sha256'],
            'type': value['type']}
