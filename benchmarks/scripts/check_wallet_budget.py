"""Focused wallet accounting controls. Offline; no model calls."""
import copy
import json
import unittest
import threading
import urllib.error

import request_contract as rc
import report_budget
from check_requests import payload


def contract(model):
    ds = model == 'deepseek-flash'
    provider = dict(provider='deepseek-official' if ds else 'bigmodel-standard-api',
        protocol='anthropic-messages' if ds else 'openai-chat-completions',
        base_url='https://api.deepseek.com/anthropic' if ds else 'https://open.bigmodel.cn/api/paas/v4')
    return rc.build(provider, {'id': model}, {'max_tool_calls': 60},
        dict(max_output_tokens=8192, timeout_seconds=1800, tool_timeout_ms=60000,
             evidence_paging={'id': 'lean-evidence-v4'}))


def request(gate, number):
    value = payload(gate.contract['protocol'], maximum=8192)
    value.update(model=gate.contract['model'], messages=[{'role': 'user', 'content': str(number)}])
    return gate.begin(gate.contract['path'], json.dumps(value).encode())[0]


def usage(gate, row, *, fresh=100, cached=250000, output=100, complete=True):
    if gate.contract['protocol'] == 'anthropic-messages':
        raw = dict(input_tokens=fresh, cache_read_input_tokens=cached,
                   cache_creation_input_tokens=0, output_tokens=output)
    else:
        raw = dict(prompt_tokens=fresh+cached, prompt_tokens_details={'cached_tokens': cached},
                   completion_tokens=output, total_tokens=fresh+cached+output)
    if not complete:
        raw.pop('cache_read_input_tokens', None); raw.pop('prompt_tokens_details', None)
    gate.observe(row, raw)


class WalletControls(unittest.TestCase):
    def test_both_real_loopback_proxies_settle_wallet_before_next_forward(self):
        from check_stream_handoff import proxy, send
        for kind in ('dsh', 'glm'):
            with self.subTest(kind=kind):
                release = threading.Event(); release.set()
                with proxy(kind, release, wallet=True) as (server, url, calls):
                    with send(kind, url, 1) as response: response.read()
                    ledger = server.gate.wallet_usage()
                    self.assertTrue(ledger['usage_complete'])
                    self.assertGreater(ledger['estimated_cost_nano'], 0)
                    server.gate.contract['wallet']['limit_nano'] = ledger['estimated_cost_nano']
                    with self.assertRaises(urllib.error.HTTPError): send(kind, url, 2)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(server.gate.summary()['stop_reason'], 'task_wallet_limit_reached')

    def test_cached_history_over_200k_does_not_stop_either_model(self):
        for model in ('deepseek-flash', 'glm-5.3'):
            with self.subTest(model=model):
                gate = rc.Gate(contract(model)); row = request(gate, 1); gate.forward(row, fixture=True)
                usage(gate, row); usage(gate, row)  # repeated cumulative event
                gate.finish(row, status=200)
                expected = sum(n * gate.contract['wallet']['rates_nano_per_token'][k]
                    for k, n in dict(inputTokens=100, cacheReadTokens=250000, outputTokens=100).items())
                self.assertEqual(gate.wallet_usage()['estimated_cost_nano'], expected)
                self.assertGreater(gate.task_usage()['observed_total_tokens'], 200000)
                self.assertEqual(gate.summary()['status'], 'wire_enforced')
                request(gate, 2)

    def test_fresh_input_cost_stops_next_request_and_never_retries(self):
        for model in ('deepseek-flash', 'glm-5.3'):
            gate = rc.Gate(contract(model)); row = request(gate, 1); gate.forward(row, fixture=True)
            usage(gate, row, fresh=500000, cached=0)
            gate.finish(row, status=200)
            self.assertEqual(gate.summary()['stop_reason'], 'task_wallet_limit_reached')
            for turn in (2, 3):
                with self.assertRaisesRegex(rc.Violation, 'task_wallet_limit_reached'): request(gate, turn)
            self.assertEqual(gate.summary()['validated_forwards'], 1)

    def test_final_over_wallet_is_graded_separately(self):
        gate = rc.Gate(contract('deepseek-flash')); row = request(gate, 1); gate.forward(row, fixture=True)
        usage(gate, row, fresh=500000, cached=0); row['final_response'] = True
        gate.finish(row, status=200); receipt = gate.summary()
        self.assertEqual(receipt['status'], 'completed_over_budget')
        self.assertTrue(rc.grading_allowed(receipt, gate.contract))
        value = report_budget.summarize({'request_contract': gate.contract}, receipt)
        self.assertIsNone(value['max_observed_total_tokens'])
        self.assertEqual(value['wallet']['currency'], 'USD')
        self.assertEqual(report_budget.project(value), value)
        self.assertIn('not a hard invoice cap', report_budget.detail(value))

    def test_missing_cache_usage_blocks_next_request_not_free_input(self):
        for model in ('deepseek-flash', 'glm-5.3'):
            gate = rc.Gate(contract(model)); row = request(gate, 1); gate.forward(row, fixture=True)
            usage(gate, row, complete=False); gate.finish(row, status=200)
            with self.assertRaisesRegex(rc.Violation, 'wallet_usage_unavailable'): request(gate, 2)
            self.assertEqual(gate.summary()['status'], 'rejected')

    def test_output_turn_and_response_guards_remain(self):
        gate = rc.Gate(contract('deepseek-flash'))
        self.assertEqual(gate.contract['task_limits']['max_model_turns'], 12)
        self.assertEqual(gate.contract['task_limits']['max_output_tokens'], 32768)
        self.assertEqual(gate.contract['output_ceiling'], 8192)
        gate.contract['max_requests'] = 1
        row = request(gate, 1); gate.forward(row, fixture=True); usage(gate, row); gate.finish(row, status=200)
        with self.assertRaisesRegex(rc.Violation, 'model_turn_limit_reached'): request(gate, 2)

    def test_price_route_is_pinned(self):
        with self.assertRaisesRegex(rc.Violation, 'wallet_price_route_mismatch'):
            rc.build(dict(provider='bigmodel-standard-api', protocol='openai-chat-completions', base_url='https://api.z.ai'),
                {'id': 'glm-5.3'}, {'max_tool_calls': 60}, dict(max_output_tokens=8192,
                timeout_seconds=1800, tool_timeout_ms=60000, evidence_paging={'id':'lean-evidence-v4'}))

    def test_public_snapshot_accepts_only_safe_wallet_fields(self):
        import public_report
        gate = rc.Gate(contract('glm-5.3')); row = request(gate, 1); gate.forward(row, fixture=True)
        usage(gate, row); row['final_response'] = True; gate.finish(row, status=200)
        value = report_budget.summarize({'request_contract': gate.contract}, gate.summary())
        saved = json.loads(public_report.SNAPSHOT.read_bytes())
        saved['records'][0]['budget_condition'] = value
        public_report.validate(copy.deepcopy(saved))
        saved['records'][0]['budget_condition']['wallet']['credential'] = 'forbidden-fixture'
        with self.assertRaises(ValueError): public_report.validate(saved)

    def test_old_condition_retains_token_budget(self):
        current = contract('deepseek-flash')
        current.pop('wallet'); current.pop('total_token_limit_enforced')
        gate = rc.Gate(current); row = request(gate, 1); gate.forward(row, fixture=True)
        usage(gate, row); gate.finish(row, status=200)
        with self.assertRaisesRegex(rc.Violation, 'task_observed_token_limit_reached'): request(gate, 2)


if __name__ == '__main__': unittest.main()
