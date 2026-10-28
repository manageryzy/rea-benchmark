#!/usr/bin/python3
"""Request-budget acceptance with dummy credentials and loopback transports only."""
import copy
from contextlib import contextmanager
from email.message import Message
import importlib.util
import io
import json
import hashlib
from pathlib import Path
import socket
import os
import signal
import sys
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from types import SimpleNamespace

import request_contract as contract

ROOT = contract.ROOT
NODE = ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'


def public_premise_prompt():
    from capabilities.coordinator import model_prompt
    manifest = {'task_id': 'task_offline_public_premises', 'binary_sha256': 'a' * 64,
                'known_abi': 'sysv_amd64',
                'given_facts': [{'path': [], 'kind': 'record', 'size': 24, 'alignment': 4}]}
    return model_prompt(b'Use close_binary, then finish the fixture.',
                        json.dumps(manifest, sort_keys=True).encode()).decode()


def text_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from text_values(item)


def backend():
    spec = importlib.util.spec_from_file_location('budget_test_transport', ROOT / 'benchmarks/harnesses/provider-transport.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def task(protocol='openai-responses', maximum=128):
    provider = {'protocol': protocol, 'provider': 'deepseek-official' if protocol == 'anthropic-messages' else 'openai',
                'model': 'deepseek-flash' if protocol == 'anthropic-messages' else 'gpt-6.1-sol',
                'base_url': 'https://frozen.invalid/anthropic' if protocol == 'anthropic-messages' else 'https://frozen.invalid/v1'}
    if protocol == 'openai-codex-responses':
        provider.update(provider='openai-codex', base_url='https://frozen.invalid/backend-api')
    gateway = {'max_tool_calls': 8}
    harness = {'max_output_tokens': maximum, 'timeout_seconds': 30, 'tool_timeout_ms': 10000}
    harness['allowed_tool_names'] = sorted('mcp__rea__' + t['name'] for t in json.loads((ROOT / 'benchmarks/config/rea-tools.json').read_bytes())['tools'])
    model = {'id': provider['model'], 'reasoning': 'high'}
    harness['request_contract'] = contract.build(provider, model, gateway, harness)
    return {'model': model['id'], 'reasoning': 'high', 'request_contract': harness['request_contract'],
            'runtime_attestation': {'provider': provider, 'configuration': {'gateway': gateway, 'harness': harness},
                'runtime': {'manifest': {'environment': {'SSL_CERT_FILE': '/etc/ssl/certs/ca-certificates.crt'}}}}}


def payload(protocol='openai-responses', maximum=128, stream=True):
    request = {'model': task(protocol)['model'], 'stream': stream, 'messages': [{'role': 'user', 'content': 'Synthetic offline control'}]}
    if protocol in ('openai-responses', 'openai-codex-responses'):
        request.update(max_output_tokens=maximum, reasoning={'effort': 'high'}, input=request.pop('messages'))
        if protocol == 'openai-codex-responses':
            request.pop('max_output_tokens')
    else:
        request.update(max_tokens=maximum, thinking={'type': 'enabled'})
        request.update(output_config={'effort': 'high'}) if protocol == 'anthropic-messages' else request.update(reasoning_effort='high')
    return request


def gate(protocol='openai-responses', **kwargs):
    return contract.Gate(task(protocol)['request_contract'], **kwargs)


class ContractTests(unittest.TestCase):
    def test_session_limits_are_positive_configured_and_frozen(self):
        current = task(maximum=128000)['request_contract']
        self.assertEqual(current['output_ceiling'], 8192)
        self.assertEqual(current['max_requests'], 12)
        self.assertEqual(current['task_limits']['max_output_tokens'], 32768)
        self.assertEqual(current['task_limits']['max_observed_total_tokens'], 200000)
        policy = json.loads(contract.POLICY.read_bytes())
        for name in policy['task_limits']:
            for bad in (0, -1, True, '12'):
                changed = copy.deepcopy(policy); changed['task_limits'][name] = bad
                with self.subTest(name=name, bad=bad), self.assertRaises(contract.Violation):
                    contract.build({'protocol': 'openai-responses'}, {'id': 'fixture'},
                        {'max_tool_calls': 60}, {'max_output_tokens': 128000, 'timeout_seconds': 30, 'tool_timeout_ms': 1000}, policy=changed)

    def test_turn_limit_counts_zero_tool_and_empty_responses_and_stays_terminal(self):
        check = gate(); check.contract['max_requests'] = 2
        for turn in range(2):
            row, _ = check.begin(check.contract['path'], json.dumps({**payload(), 'metadata': {'turn': turn}}).encode())
            check.forward(row, fixture=True); check.finish(row, status=200)
        for turn in range(2, 4):
            with self.assertRaisesRegex(contract.Violation, 'model_turn_limit'):
                check.begin(check.contract['path'], json.dumps({**payload(), 'metadata': {'turn': turn}}).encode())
        self.assertEqual(check.summary()['status'], 'budget_exhausted')
        self.assertEqual(check.summary()['validated_forwards'], 2)
        self.assertEqual(check.summary()['provider_forwards'], 0)

    def test_cumulative_output_reserves_full_request_if_usage_missing(self):
        for protocol in ('anthropic-messages', 'openai-responses', 'openai-chat-completions'):
            check = gate(protocol); check.contract['task_limits']['max_output_tokens'] = 256
            for turn in range(2):
                row, _ = check.begin(check.contract['path'], json.dumps({**payload(protocol), 'metadata': {'turn': turn}}).encode())
                check.forward(row, fixture=True); check.finish(row, status=200)
            with self.assertRaisesRegex(contract.Violation, 'task_output_budget'):
                check.begin(check.contract['path'], json.dumps({**payload(protocol), 'metadata': {'turn': 2}}).encode())
            self.assertEqual(check.summary()['task_usage']['output_tokens_charged_or_reserved'], 256)

    def test_complete_cumulative_usage_releases_unused_reservation_once(self):
        check = gate(); check.contract['task_limits']['max_output_tokens'] = 300
        for turn in range(4):
            row, _ = check.begin(check.contract['path'], json.dumps({**payload(), 'metadata': {'turn': turn}}).encode())
            check.forward(row, fixture=True)
            for output in (10, 50, 50):
                check.observe(row, {'input_tokens': 100, 'output_tokens': output, 'total_tokens': 100 + output})
            check.finish(row, status=200)
        self.assertEqual(check.task_usage()['output_tokens_charged_or_reserved'], 200)
        self.assertEqual(check.task_usage()['observed_total_tokens'], 600)
        with self.assertRaisesRegex(contract.Violation, 'task_output_budget'):
            check.begin(check.contract['path'], json.dumps({**payload(), 'metadata': {'turn': 4}}).encode())
        self.assertEqual(check.summary()['validated_forwards'], 4)

    def test_observed_total_threshold_stops_next_turn_and_keeps_cached_input(self):
        check = gate(); check.contract['task_limits']['max_observed_total_tokens'] = 100
        row, _ = check.begin(check.contract['path'], json.dumps(payload()).encode()); check.forward(row, fixture=True)
        check.observe(row, {'input_tokens': 120, 'output_tokens': 5, 'total_tokens': 125,
                            'input_tokens_details': {'cached_tokens': 100}})
        check.finish(row, status=200)
        self.assertEqual(check.task_usage()['observed_total_tokens'], 125)
        with self.assertRaisesRegex(contract.Violation, 'task_observed_token_limit'):
            check.begin(check.contract['path'], json.dumps({**payload(), 'metadata': {'turn': 2}}).encode())
        self.assertEqual(check.summary()['validated_forwards'], 1)

    def test_incomplete_response_never_refunds_its_full_cap(self):
        check = gate(); check.contract['task_limits']['max_output_tokens'] = 128
        row, _ = check.begin(check.contract['path'], json.dumps(payload()).encode()); check.forward(row, fixture=True)
        check.observe(row, {'input_tokens': 1, 'output_tokens': 1})
        check.finish(row, status=200, complete=False, error='lost_stream')
        self.assertEqual(check.task_usage()['output_tokens_charged_or_reserved'], 128)
        with self.assertRaisesRegex(contract.Violation, 'retry_after_transport_failure'):
            check.begin(check.contract['path'], json.dumps({**payload(), 'metadata': {'turn': 2}}).encode())

    def test_all_routes_reject_invalid_caps_before_forwarding(self):
        for protocol in ('openai-responses', 'anthropic-messages', 'openai-chat-completions'):
            field = task(protocol)['request_contract']['cap_field']
            for value in (None, 0, -1, True, '128', 129, 1.5):
                with self.subTest(protocol=protocol, value=value):
                    check = gate(protocol)
                    request = payload(protocol)
                    if value is None:
                        del request[field]
                    else:
                        request[field] = value
                    with self.assertRaises(contract.Violation):
                        check.begin(check.contract['path'], json.dumps(request).encode())
                    self.assertEqual(check.summary()['provider_forwards'], 0)
                    self.assertEqual(check.summary()['validated_forwards'], 0)

    def test_valid_caps_and_continuations_streaming_and_json(self):
        for protocol in ('openai-responses', 'anthropic-messages', 'openai-chat-completions'):
            for streaming in (False, True):
                check = gate(protocol)
                for turn in range(3):
                    request = payload(protocol, 64, streaming)
                    request['metadata'] = {'turn': turn}  # distinct tool continuation
                    row, _ = check.begin(check.contract['path'], json.dumps(request).encode())
                    check.forward(row, fixture=True)
                    check.finish(row, status=200)
                self.assertEqual(check.summary()['validated_forwards'], 3)
                self.assertEqual(check.summary()['provider_forwards'], 0)
                self.assertEqual(check.summary()['status'], 'wire_enforced')

    def test_model_path_reasoning_alias_and_malformed_fields(self):
        for protocol in ('openai-responses', 'anthropic-messages', 'openai-chat-completions'):
            variants = []
            for field, value in [('model', 'different-model'), ('stream', 'true'), ('max_completion_tokens', 128)]:
                variants.append({**payload(protocol), field: value})
            wrong = payload(protocol)
            if protocol == 'openai-responses':
                wrong['reasoning'] = {'effort': 'low'}
            elif protocol == 'anthropic-messages':
                wrong['output_config'] = {'effort': 'low'}
            else:
                wrong['reasoning_effort'] = 'low'
            variants.append(wrong)
            for request in variants:
                check = gate(protocol)
                with self.assertRaises(contract.Violation):
                    check.begin(check.contract['path'], json.dumps(request).encode())
                self.assertEqual(check.summary()['provider_forwards'], 0)
            check = gate(protocol)
            with self.assertRaises(contract.Violation):
                check.begin(check.contract['path'] + '?redirect=https://other.invalid', json.dumps(payload(protocol)).encode())

    def test_duplicate_json_keys_and_nonfinite_json_rejected(self):
        for raw in (b'{"max_output_tokens":1,"max_output_tokens":128}', b'{"cap":NaN}', b'[]', b'not-json'):
            with self.assertRaises(contract.Violation):
                contract.decode(raw)

    def test_background_generation_and_native_provider_tools_are_denied(self):
        for fields in ({'background': True}, {'tools': [{'type': 'web_search'}]},
                       {'tools': [{'type': 'function', 'name': 'undeclared_shell'}]}):
            check = gate()
            with self.assertRaises(contract.Violation):
                check.begin(check.contract['path'], json.dumps({**payload(), **fields}).encode())
            self.assertEqual(check.summary()['provider_forwards'], 0)

    def test_responses_configuration_updates_use_effective_effort(self):
        request = payload()
        request['reasoning']['effort'] = 'medium'
        request['input'].insert(0, {'type': 'configuration_update', 'reasoning': {'effort': 'high'}})
        row, _ = gate().begin('/v1/responses', json.dumps(request).encode())
        self.assertEqual(row['effective_reasoning_effort'], 'high')
        self.assertEqual(row['reasoning_encoding'], 'input_configuration_updates')
        for mutation in ('wrong_effort', 'adjacent', 'auto_truncation', 'pro_mode', 'unknown_field'):
            changed = copy.deepcopy(request)
            if mutation == 'wrong_effort':
                changed['input'][0]['reasoning']['effort'] = 'low'
            elif mutation == 'adjacent':
                changed['input'].insert(0, changed['input'][0])
            elif mutation == 'auto_truncation':
                changed['truncation'] = 'auto'
            elif mutation == 'pro_mode':
                changed['reasoning']['mode'] = 'pro'
            else:
                changed['input'][0]['other_compute_mode'] = 'unknown'
            with self.subTest(mutation=mutation), self.assertRaises(contract.Violation):
                gate().begin('/v1/responses', json.dumps(changed).encode())

    def test_no_automatic_retries_and_failure_is_terminal(self):
        for successful in (True, False):
            check = gate()
            raw = json.dumps(payload()).encode()
            row, _ = check.begin(check.contract['path'], raw)
            check.forward(row, fixture=True)
            check.finish(row, status=200 if successful else 503, complete=successful)
            with self.assertRaises(contract.Violation):
                check.begin(check.contract['path'], raw)
            if not successful:
                request = {**payload(), 'metadata': {'attempt': 2}}
                with self.assertRaises(contract.Violation):
                    check.begin(check.contract['path'], json.dumps(request).encode())
            self.assertEqual(check.summary()['validated_forwards'], 1)

    def test_deadline_request_count_concurrency_and_body_limits(self):
        now = [0]
        check = gate(clock=lambda: now[0])
        now[0] = 31
        with self.assertRaisesRegex(contract.Violation, 'wall_limit'):
            check.begin(check.contract['path'], json.dumps(payload()).encode())
        for key, value in [('max_requests', 0), ('max_request_bytes', 1)]:
            check = gate()
            check.contract[key] = value
            with self.assertRaises(contract.Violation):
                check.begin(check.contract['path'], json.dumps(payload()).encode())
        check = gate()
        check.begin(check.contract['path'], json.dumps(payload()).encode())
        with self.assertRaisesRegex(contract.Violation, 'concurrent'):
            check.begin(check.contract['path'], json.dumps({**payload(), 'metadata': {'next': 1}}).encode())

    def test_unauthorized_uncapped_provider_refused_before_inventory_or_model(self):
        import runtime_attestation as runtime
        with patch.object(runtime, 'provider', return_value={'protocol': 'openai-codex-responses'}), \
             patch.object(runtime, 'capture', side_effect=AssertionError('Inventory must not start')):
            with self.assertRaisesRegex(contract.Violation, 'unsupported_observed_budget_route'):
                runtime.prepare({'id': 'gpt-6.1-sol', 'harness': 'deepseek-harness', 'reasoning': 'high'},
                    {'gateway': {'max_tool_calls': 8}, 'harness': {'max_output_tokens': 128, 'timeout_seconds': 30, 'tool_timeout_ms': 10000}})

    def test_codex_uncapped_is_explicit_and_distinct_from_cap_certification(self):
        check = gate('openai-codex-responses')
        self.assertEqual(check.contract['mode'], 'observed_task_limits')
        self.assertIsNone(check.contract['cap_field'])
        self.assertIsNone(check.contract['output_ceiling'])
        self.assertNotEqual(contract.digest(check.contract), contract.digest(task()['request_contract']))
        row, _ = check.begin(check.contract['path'], json.dumps(payload('openai-codex-responses')).encode())
        check.forward(row, fixture=True)
        check.observe(row, {'input_tokens': 100, 'output_tokens': 9000, 'total_tokens': 9100,
                            'input_tokens_details': {'cached_tokens': 80}})
        check.finish(row, status=200)
        self.assertEqual(check.summary()['status'], 'observed_limits_enforced')
        self.assertFalse(check.summary()['provider_output_cap_enforced'])
        self.assertEqual(check.task_usage()['observed_total_tokens'], 9100)
        self.assertEqual(check.task_usage()['output_tokens_charged_or_reserved'], 9000)
        self.assertEqual(check.observations[-1]['usage']['inputTokens'], 20)

    def test_codex_uncapped_missing_usage_cannot_continue_or_be_certified(self):
        check = gate('openai-codex-responses')
        row, _ = check.begin(check.contract['path'], json.dumps(payload('openai-codex-responses')).encode())
        check.forward(row, fixture=True); check.finish(row, status=200)
        self.assertEqual(check.summary()['status'], 'rejected')
        self.assertFalse(check.summary()['transport_complete'])
        with self.assertRaisesRegex(contract.Violation, 'retry_after_transport_failure'):
            check.begin(check.contract['path'], json.dumps({**payload('openai-codex-responses'), 'input': []}).encode())

    def test_codex_uncapped_stops_on_reported_output_or_total_threshold(self):
        for field in ('max_output_tokens', 'max_observed_total_tokens'):
            check = gate('openai-codex-responses'); check.contract['task_limits'][field] = 100
            row, _ = check.begin(check.contract['path'], json.dumps(payload('openai-codex-responses')).encode())
            check.forward(row, fixture=True)
            check.observe(row, {'input_tokens': 1, 'output_tokens': 150, 'total_tokens': 151})
            check.finish(row, status=200)
            self.assertEqual(check.summary()['status'], 'budget_exhausted')
            self.assertEqual(check.summary()['validated_forwards'], 1)
            with self.assertRaises(contract.Violation):
                check.begin(check.contract['path'], json.dumps({**payload('openai-codex-responses'), 'input': []}).encode())

    def test_codex_uncapped_still_checks_path_model_reasoning_tools_and_caps(self):
        for update in ({'model': 'different'}, {'reasoning': {'effort': 'low'}},
                       {'tools': [{'type': 'function', 'name': 'undeclared'}]}, {'max_output_tokens': 8192}):
            check = gate('openai-codex-responses')
            with self.assertRaises(contract.Violation):
                check.begin(check.contract['path'], json.dumps({**payload('openai-codex-responses'), **update}).encode())
            self.assertEqual(check.summary()['validated_forwards'], 0)
        check = gate('openai-codex-responses')
        with self.assertRaisesRegex(contract.Violation, 'path'):
            check.begin('/v1/responses', json.dumps(payload('openai-codex-responses')).encode())

    def test_codex_uncapped_turn_and_wall_limits_remain_enforced(self):
        now = [0]
        check = gate('openai-codex-responses', clock=lambda: now[0]); check.contract['max_requests'] = 1
        row, _ = check.begin(check.contract['path'], json.dumps(payload('openai-codex-responses')).encode())
        check.forward(row, fixture=True)
        check.observe(row, {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3})
        check.finish(row, status=200)
        with self.assertRaisesRegex(contract.Violation, 'model_turn_limit'):
            check.begin(check.contract['path'], json.dumps({**payload('openai-codex-responses'), 'input': []}).encode())
        check = gate('openai-codex-responses', clock=lambda: now[0]); now[0] = 31
        with self.assertRaisesRegex(contract.Violation, 'wall_limit'):
            check.begin(check.contract['path'], json.dumps(payload('openai-codex-responses')).encode())

    def test_contract_is_frozen_and_separate_from_historical_results(self):
        original = task()
        self.assertEqual(contract.from_task(original), original['request_contract'])
        changed = copy.deepcopy(original)
        changed['request_contract']['output_ceiling'] += 1
        with self.assertRaisesRegex(contract.Violation, 'changed'):
            contract.from_task(changed)
        with self.assertRaisesRegex(contract.Violation, 'missing_frozen'):
            contract.from_task({'runtime_attestation': {'configuration': {'harness': {}}}})

    def test_stream_chunks_and_cumulative_usage_and_truncation(self):
        check = gate()
        row, _ = check.begin(check.contract['path'], json.dumps(payload()).encode())
        observer = contract.ResponseObserver(check, row)
        event = {'type': 'response.completed', 'response': {'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}}
        raw = b'data: ' + json.dumps(event).encode() + b'\r\n\r\n'
        for byte in raw:
            observer.feed(bytes([byte]))
        self.assertTrue(observer.end())
        self.assertEqual(check.observations[0]['usage']['outputTokens'], 5)
        unfinished = contract.ResponseObserver(check, row)
        unfinished.feed(b'data: {"type":"response.created"}\n\n')
        self.assertFalse(unfinished.end())
        event['response']['usage']['output_tokens'] = 129
        with self.assertRaisesRegex(contract.Violation, 'exceeds_cap'):
            contract.ResponseObserver(check, row).feed(b'data: ' + json.dumps(event).encode() + b'\n\n')

    def test_usage_is_request_linked_and_incomplete_components_remain_unknown(self):
        import usage
        check = gate('anthropic-messages')
        row, _ = check.begin(check.contract['path'], json.dumps(payload('anthropic-messages')).encode())
        check.forward(row, fixture=True)
        check.observe(row, {'input_tokens': 10, 'output_tokens': 0})
        check.observe(row, {'output_tokens': 5})
        check.finish(row, status=200)
        ledger = usage.ledger(check.summary())
        self.assertEqual(ledger['reported_usage']['totalTokens'], 15)
        self.assertEqual(ledger['status'], 'partial')
        check.observe(row, {'cache_read_input_tokens': 2, 'cache_creation_input_tokens': 1})
        ledger = usage.ledger(check.summary())
        self.assertEqual(ledger['reported_usage']['totalTokens'], 18)
        self.assertEqual(ledger['status'], 'complete')
        check.finish(row, complete=False, error='incomplete_provider_stream')
        self.assertEqual(usage.ledger(check.summary())['status'], 'partial')


class BudgetCompletionTests(unittest.TestCase):
    protocols = ('anthropic-messages', 'openai-responses', 'openai-codex-responses',
                 'openai-chat-completions')

    @staticmethod
    def completed(protocol, *, total=125, final=True, streaming=True, partial=False, fixture=True):
        check = gate(protocol)
        check.contract['task_limits']['max_observed_total_tokens'] = 100
        row, _ = check.begin(check.contract['path'], json.dumps(payload(protocol, stream=streaming)).encode())
        check.forward(row, fixture=fixture)
        observer = contract.ResponseObserver(check, row)
        if protocol == 'anthropic-messages':
            usage = {'input_tokens': total - 7, 'output_tokens': 5,
                     'cache_read_input_tokens': 2, 'cache_creation_input_tokens': 0}
            if partial:
                usage.pop('cache_read_input_tokens')
            result = {'type': 'message', 'stop_reason': 'end_turn' if final else 'tool_use', 'usage': usage}
            events = [{'type': 'message_delta', 'delta': {'stop_reason': result['stop_reason']}, 'usage': usage},
                      {'type': 'message_stop'}]
        elif protocol in ('openai-responses', 'openai-codex-responses'):
            usage = {'input_tokens': total - 5, 'output_tokens': 5, 'total_tokens': total,
                     'input_tokens_details': {'cached_tokens': 2}}
            if partial:
                usage.pop('input_tokens_details')
            result = {'object': 'response', 'status': 'completed', 'usage': usage,
                      'output': [{'type': 'message' if final else 'function_call'}]}
            events = [{'type': 'response.completed', 'response': result}]
        else:
            usage = {'prompt_tokens': total - 5, 'completion_tokens': 5, 'total_tokens': total,
                     'prompt_tokens_details': {'cached_tokens': 2}}
            if partial:
                usage.pop('prompt_tokens_details')
            result = {'choices': [{'index': 0, 'finish_reason': 'stop' if final else 'tool_calls'}], 'usage': usage}
            events = [result]
        wire = (b''.join(b'data: ' + json.dumps(event).encode() + b'\n\n' for event in events)
                if streaming else json.dumps(result).encode())
        if streaming and protocol == 'openai-chat-completions':
            wire += b'data: [DONE]\n\n'
        observer.feed(wire)
        # Observation cannot kill the current response before the client saves it.
        if check.exhausted is not None:
            raise AssertionError('Current response was interrupted at usage observation')
        check.finish(row, status=200, complete=observer.end())
        return check

    def test_final_overrun_is_gradeable_and_holds_every_provider_queue(self):
        for protocol in self.protocols:
            for streaming in (False, True):
                with self.subTest(protocol=protocol, streaming=streaming):
                    check = self.completed(protocol, streaming=streaming)
                    receipt = check.summary()
                    self.assertEqual(receipt['status'], 'completed_over_budget')
                    self.assertTrue(contract.grading_allowed(receipt, check.contract))
                    self.assertTrue(receipt['queue_stop_required'])
                    self.assertTrue(receipt['budget_usage_complete'])
                    self.assertEqual(receipt['budget_excess'], {'max_observed_total_tokens':
                                     {'limit': 100, 'observed': 125, 'excess': 25}})
                    self.assertIsNone(check.exhausted)
                    with self.assertRaisesRegex(contract.Violation, 'task_observed_token_limit_reached'):
                        check.begin(check.contract['path'], json.dumps({**payload(protocol), 'metadata': {'next': 1}}).encode())
                    self.assertEqual(check.summary()['validated_forwards'], 1)
                    self.assertFalse(contract.grading_allowed(check.summary(), check.contract))

    def test_exact_threshold_holds_queue_without_claiming_overrun(self):
        for protocol in self.protocols:
            with self.subTest(protocol=protocol):
                check = self.completed(protocol, total=100)
                receipt = check.summary()
                self.assertEqual(receipt['status'], contract.success_status(check.contract))
                self.assertFalse(receipt['budget_exceeded'])
                self.assertTrue(receipt['queue_stop_required'])
                self.assertTrue(contract.grading_allowed(receipt, check.contract))

    def test_tool_only_or_partial_usage_overrun_cannot_receive_grade(self):
        for protocol in self.protocols:
            for update in ({'final': False}, {'partial': True}):
                with self.subTest(protocol=protocol, update=update):
                    check = self.completed(protocol, **update)
                    receipt = check.summary()
                    self.assertTrue(receipt['queue_stop_required'])
                    self.assertNotEqual(receipt['status'], 'completed_over_budget')
                    self.assertFalse(contract.grading_allowed(receipt, check.contract))

    def test_missing_final_usage_or_transport_failure_cannot_be_promoted(self):
        check = self.completed('openai-codex-responses')
        receipt = check.summary()
        for field, value in (('usage_observations', []), ('request_ledger_complete', False),
                             ('transport_complete', False), ('final_response_complete', False),
                             ('rejected_requests', 1)):
            changed = copy.deepcopy(receipt); changed[field] = value
            with self.subTest(field=field):
                self.assertFalse(contract.grading_allowed(changed, check.contract))
        check.finish(check.requests[0], status=200, complete=False, error='lost_stream')
        self.assertFalse(contract.grading_allowed(check.summary(), check.contract))

    def test_truncated_responses_never_become_completed_final_submissions(self):
        for protocol in self.protocols:
            with self.subTest(protocol=protocol):
                check = gate(protocol)
                check.contract['task_limits']['max_observed_total_tokens'] = 100
                row, _ = check.begin(check.contract['path'], json.dumps(payload(protocol)).encode())
                check.forward(row, fixture=True)
                observer = contract.ResponseObserver(check, row)
                if protocol == 'anthropic-messages':
                    observer.event({'type': 'message_delta', 'delta': {'stop_reason': 'max_tokens'},
                                    'usage': {'input_tokens': 120, 'output_tokens': 5,
                                              'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}})
                    observer.event({'type': 'message_stop'})
                elif protocol in ('openai-responses', 'openai-codex-responses'):
                    observer.event({'type': 'response.incomplete', 'response': {'status': 'incomplete',
                        'output': [{'type': 'message'}], 'usage': {'input_tokens': 120, 'output_tokens': 5,
                        'total_tokens': 125, 'input_tokens_details': {'cached_tokens': 0}}}})
                else:
                    observer.event({'choices': [{'index': 0, 'finish_reason': 'length'}],
                                    'usage': {'prompt_tokens': 120, 'completion_tokens': 5, 'total_tokens': 125,
                                              'prompt_tokens_details': {'cached_tokens': 0}}})
                    observer.feed(b'data: [DONE]\n\n')
                check.finish(row, status=200, complete=observer.end())
                self.assertEqual(check.summary()['status'], 'budget_exhausted')
                self.assertFalse(check.summary()['final_response_complete'])
                self.assertFalse(contract.grading_allowed(check.summary(), check.contract))

    def test_current_policy_cannot_promote_a_historical_stopped_receipt(self):
        check = self.completed('openai-codex-responses')
        receipt = check.summary()
        old = copy.deepcopy(check.contract); old.pop('budget_semantics'); old['policy_id'] = 'provider-request-contract-v3'
        receipt.update(contract=old, contract_sha256=contract.digest(old))
        self.assertFalse(contract.grading_allowed(receipt, old))
        self.assertFalse(contract.grading_allowed(receipt, check.contract))
        receipt['status'] = 'budget_exhausted'
        self.assertFalse(contract.grading_allowed(receipt, old))

    def test_uncapped_output_overrun_finishes_but_blocks_following_request(self):
        check = self.completed('openai-codex-responses', total=50)
        check.contract['task_limits']['max_output_tokens'] = 4
        receipt = check.summary()
        self.assertEqual(receipt['status'], 'completed_over_budget')
        self.assertEqual(receipt['budget_excess'], {'max_output_tokens': {'limit': 4, 'observed': 5, 'excess': 1}})
        self.assertTrue(contract.grading_allowed(receipt, check.contract))
        with self.assertRaisesRegex(contract.Violation, 'task_output_budget_reached'):
            check.begin(check.contract['path'], json.dumps({**payload('openai-codex-responses'), 'metadata': {'next': 1}}).encode())

    def test_dsh_dispatch_preserves_completed_final_and_rejects_client_failure(self):
        spec = importlib.util.spec_from_file_location('completion_dispatch', ROOT / 'benchmarks/harnesses/dispatch.py')
        dispatch = importlib.util.module_from_spec(spec); spec.loader.exec_module(dispatch)
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                # Simulated accounting/child output only; no socket or model call.
                check = self.completed('openai-codex-responses', fixture=False)
                current = task('openai-codex-responses'); current['request_contract'] = check.contract
                current['runtime_attestation']['identity'] = 'offline-fixture'
                @contextmanager
                def transport(*_args, **_kwargs):
                    try:
                        yield SimpleNamespace(base_url='http://127.0.0.1:1/v1', deadline_ms=1, gate=check)
                    finally:
                        (directory / 'request-budget.json').write_text(json.dumps(check.summary()))
                def child(*_args, **_kwargs):
                    (directory / 'answer-final.md').write_text('Synthetic completed candidate')
                    (directory / 'harness-result.json').write_text(json.dumps({'exit_code': int(failed), 'timed_out': False}))
                with patch.object(dispatch, 'load_module', return_value=SimpleNamespace(transport=transport)), \
                     patch.object(dispatch, '_launch_dsh', side_effect=child):
                    if failed:
                        with self.assertRaisesRegex(RuntimeError, 'contract failed'):
                            dispatch._launch(directory, current, {'harness': 'deepseek-harness'})
                    else:
                        dispatch._launch(directory, current, {'harness': 'deepseek-harness'})
                result = json.loads((directory / 'result.json').read_bytes())
                self.assertEqual(result['budget_status'], 'completed_over_budget')
                self.assertTrue(result['budget_exceeded'])
                self.assertTrue(result['queue_stop_required'])
                self.assertEqual((directory / 'harness-failure.json').exists(), failed)
                self.assertFalse((directory / 'budget-exhausted.json').exists())
                self.assertEqual((directory / 'answer-final.md').read_text(), 'Synthetic completed candidate')

    def test_zcode_dispatch_preserves_completed_final_and_rejects_client_failure(self):
        import records
        spec = importlib.util.spec_from_file_location('zcode_completion_dispatch', ROOT / 'benchmarks/harnesses/dispatch.py')
        dispatch = importlib.util.module_from_spec(spec); spec.loader.exec_module(dispatch)
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                check = self.completed('openai-chat-completions', fixture=False)
                raw = {'requests': check.requests, 'request_budget': check.summary(), 'elapsed_seconds': 1,
                    'policy_violations': [], 'upstream_error': None, 'exit_code': int(failed), 'timed_out': False,
                    'usage_observations_raw': [{'sequence': 1, 'usage': {'prompt_tokens': 120, 'completion_tokens': 5,
                        'total_tokens': 125, 'prompt_tokens_details': {'cached_tokens': 2}}}]}
                def collect(*_args):
                    (directory / 'answer-final.md').write_text('Synthetic completed candidate')
                    return 'Synthetic completed candidate', ['glm-5.3']
                backend = SimpleNamespace(launch=lambda *_a, **_k: raw, collect_wire=collect)
                current = {'runtime_attestation': {'identity': 'fixture'}, 'environment_profile': {},
                    'request_contract': check.contract, 'condition': 'fixture', 'language': 'cpp', 'id': 'fixture',
                    'binary_sha256': 'a'*64, 'prompt_sha256': 'b'*64}
                model = {'id': 'glm-5.3', 'harness': 'zcode', 'label': 'GLM fixture', 'reasoning': 'high'}
                with patch.object(dispatch, 'load_module', return_value=backend), \
                     patch.object(dispatch.runtime_attestation, 'glm_credentials', return_value=('https://unused.invalid', 'dummy')):
                    if failed:
                        with self.assertRaisesRegex(RuntimeError, 'ZCode/provider failed'):
                            dispatch._launch(directory, current, model, records=records)
                    else:
                        dispatch._launch(directory, current, model, records=records)
                result = json.loads((directory / 'result.json').read_bytes())
                self.assertEqual(result['budget_status'], 'completed_over_budget')
                self.assertEqual(result['reported_usage']['totalTokens'], 125)
                self.assertTrue(result['budget_exceeded'])
                self.assertTrue(result['queue_stop_required'])
                self.assertEqual((directory / 'harness-failure.json').exists(), failed)
                self.assertFalse((directory / 'budget-exhausted.json').exists())
                self.assertEqual((directory / 'answer-final.md').read_text(), 'Synthetic completed candidate')


class Response(io.BytesIO):
    status = 200
    def __init__(self, raw, streaming=True):
        super().__init__(raw)
        self.headers = Message()
        self.headers['Content-Type'] = 'text/event-stream' if streaming else 'application/json'


def client_response(protocol):
    if protocol == 'anthropic-messages':
        events = [
            {'type': 'message_start', 'message': {'id': 'offline-message', 'type': 'message', 'role': 'assistant',
             'model': 'deepseek-flash', 'content': [], 'stop_reason': None, 'usage': {'input_tokens': 10, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'REQUEST_BUDGET_OK'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 5}},
            {'type': 'message_stop'},
        ]
    else:
        item = {'id': 'msg-offline', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                'content': [{'type': 'output_text', 'text': 'REQUEST_BUDGET_OK', 'annotations': []}]}
        response = {'id': 'resp_offline', 'object': 'response', 'status': 'completed', 'model': 'gpt-6.1-sol', 'output': [item],
                    'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15,
                              'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}}
        events = [{'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': [], 'usage': None}},
                  {'type': 'response.output_item.added', 'output_index': 0, 'item': {**item, 'status': 'in_progress', 'content': []}},
                  {'type': 'response.content_part.added', 'output_index': 0, 'content_index': 0, 'item_id': item['id'],
                   'part': {'type': 'output_text', 'text': '', 'annotations': []}},
                  {'type': 'response.output_text.delta', 'output_index': 0, 'content_index': 0, 'item_id': item['id'], 'delta': 'REQUEST_BUDGET_OK'},
                  {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                  {'type': 'response.completed', 'response': response}]
    return b''.join(b'event: ' + event['type'].encode() + b'\ndata: ' + json.dumps(event).encode() + b'\n\n' for event in events)


class TransportTests(unittest.TestCase):
    def test_http_output_budget_refusal_makes_no_additional_upstream_call(self):
        module = backend(); called = []
        def upstream(request, row):
            called.append(request)
            return Response(b'data: {"type":"response.completed","response":{"usage":{"input_tokens":10,"output_tokens":5}}}\n\n')
        with tempfile.TemporaryDirectory() as temporary, module.transport(Path(temporary), task(), fixture_upstream=upstream) as proxy:
            proxy.gate.contract['task_limits']['max_output_tokens'] = 128
            url = proxy.base_url + '/responses'
            def send(body):
                return urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(),
                    headers={'Content-Type': 'application/json'}))
            with send(payload()) as response: response.read()
            for turn in (1, 2):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    send({**payload(), 'metadata': {'turn': turn}})
                self.assertIn(b'task_output_budget', error.exception.read())
            self.assertEqual(len(called), 1)
            self.assertEqual(proxy.gate.summary()['status'], 'budget_exhausted')
            self.assertEqual(proxy.gate.summary()['provider_forwards'], 0)

    def test_installed_zcode_stops_after_one_allowed_turn_without_retry(self):
        spec = importlib.util.spec_from_file_location('budget_stop_zcode', ROOT / 'benchmarks/harnesses/zcode.py')
        zcode = importlib.util.module_from_spec(spec); spec.loader.exec_module(zcode)
        original_build = contract.build
        def one_turn(*args, **kwargs):
            value = original_build(*args, **kwargs)
            value['max_requests'] = value['task_limits']['max_model_turns'] = 1
            return value
        with tempfile.TemporaryDirectory(prefix='rea-budget-stop-zcode-') as temporary:
            directory = Path(temporary); frozen = directory/'inputs'; frozen.mkdir()
            tools = (ROOT/'benchmarks/config/rea-tools.json').read_bytes()
            (frozen/'tools.json').write_bytes(tools); (frozen/'target').write_bytes(b'Offline fixture only')
            (frozen/'prompt.txt').write_text('Use close_binary, then finish the fixture.')
            (frozen/'manifest.json').write_text('{}')
            (frozen/'gateway.json').write_text(json.dumps({'analysis_tools_file': str(frozen/'tools.json')}))
            item = {'input_directory': str(frozen), 'max_output_tokens': 128000, 'max_tool_calls': 8,
                    'tool_count': len(json.loads(tools)['tools']), 'timeout_seconds': 90}
            evidence = 'benchmarks/runs/native-pilot-20261019/gpt-6.1-sol/cpp/evidence-03-close_binary.json'
            with patch.object(contract, 'build', side_effect=one_turn):
                raw = zcode.launch(directory/'run', item, fixture=True,
                    replay=[{'tool': 'close_binary', 'arguments': {}, 'result_path': evidence}])
            self.assertEqual(raw['request_budget']['status'], 'budget_exhausted')
            self.assertEqual(raw['request_budget']['stop_reason'], 'model_turn_limit_reached')
            self.assertEqual(raw['request_budget']['validated_forwards'], 1)
            self.assertEqual(raw['request_budget']['provider_forwards'], 0)
            self.assertEqual(raw['requests'][0]['cap'], 8192)
            self.assertFalse(raw['timed_out'])

    def test_actual_installed_zcode_serialization_with_cached_evidence(self):
        spec = importlib.util.spec_from_file_location('budget_test_zcode', ROOT / 'benchmarks/harnesses/zcode.py')
        zcode = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(zcode)
        with tempfile.TemporaryDirectory(prefix='rea-budget-zcode-') as temporary:
            directory = Path(temporary)
            frozen = directory / 'inputs'
            frozen.mkdir()
            tools = (ROOT / 'benchmarks/config/rea-tools.json').read_bytes()
            (frozen / 'tools.json').write_bytes(tools)
            (frozen / 'target').write_bytes(b'Offline fixture: never loaded or executed.')
            prompt = public_premise_prompt()
            (frozen / 'prompt.txt').write_text(prompt)
            (frozen / 'manifest.json').write_text('{}')
            (frozen / 'gateway.json').write_text(json.dumps({'analysis_tools_file': str(frozen / 'tools.json')}))
            item = {'input_directory': str(frozen), 'max_output_tokens': 8192, 'max_tool_calls': 8,
                    'tool_count': len(json.loads(tools)['tools']), 'timeout_seconds': 90}
            evidence = 'benchmarks/runs/native-pilot-20261019/gpt-6.1-sol/cpp/evidence-03-close_binary.json'
            raw = zcode.launch(directory / 'run', item, fixture=True,
                replay=[{'tool': 'close_binary', 'arguments': {}, 'result_path': evidence}])
            self.assertEqual(raw['exit_code'], 0)
            self.assertFalse(raw['policy_violations'], raw['policy_violations'])
            self.assertFalse(raw['upstream_error'], raw['upstream_error'])
            self.assertEqual(raw['request_budget']['provider_forwards'], 0)
            self.assertEqual(raw['request_budget']['validated_forwards'], 2)
            self.assertEqual(raw['request_budget']['status'], 'wire_enforced')
            first_request = json.loads((directory / 'run/wire/request-001.json').read_bytes())
            self.assertTrue(any(prompt in text for text in text_values(first_request)),
                            'The actual ZCode request omitted the supplied public premises')

    def test_installed_zcode_nonfixture_seed_environment_and_contract_delivery(self):
        import action_cache
        import runtime_attestation
        from capabilities import analysis_context, artifact_format
        spec = importlib.util.spec_from_file_location('seeded_nonfixture_zcode', ROOT / 'benchmarks/harnesses/zcode.py')
        zcode = importlib.util.module_from_spec(spec); spec.loader.exec_module(zcode)
        with tempfile.TemporaryDirectory(prefix='rea-seeded-zcode-') as temporary:
            directory = Path(temporary); frozen = directory / 'inputs'; frozen.mkdir()
            case = json.loads((ROOT / '.cache/benchmark-capabilities/task-index/record_codec_sysv_amd64_scalar_recover.json').read_bytes())
            public = ROOT / case['public_directory']; target = (public / 'target').read_bytes()
            manifest = (public / 'manifest.json').read_bytes()
            context = analysis_context.derive(target, json.loads(manifest), 'abi_probe')
            prompt = 'Reply REQUEST_BUDGET_OK.' + artifact_format.NOTICE.decode()
            for name, raw in {'target': target, 'manifest.json': manifest, 'prompt.txt': prompt.encode(),
                              'tools.json': b'{"tools":[]}', 'analysis.seeds': context['seed_text'].encode()}.items():
                (frozen / name).write_bytes(raw)
            provider = {'protocol': 'openai-chat-completions', 'provider': 'bigmodel-standard-api',
                        'model': 'glm-5.3', 'base_url': 'https://frozen.invalid/v4'}
            gateway = {'analysis_context': context, 'max_tool_calls': 8,
                       'analysis_tools_file': '@task/tools.json', 'tool_timeout_ms': 120000}
            harness = {'max_output_tokens': 8192, 'timeout_seconds': 90, 'tool_timeout_ms': 120000,
                       'allowed_tool_names': [], 'known_entry_seeds': True, 'explicit_type_roles': True}
            policy = contract.build(provider, {'id': 'glm-5.3', 'reasoning': 'high'}, gateway, harness)
            harness['request_contract'] = policy
            env = {'SSL_CERT_FILE': '/etc/ssl/certs/ca-certificates.crt',
                   'NODE_OPTIONS': '--require ' + str(ROOT / 'benchmarks/scripts/offline-network.cjs')}
            runtime_manifest = {'schema_version': 1, 'environment': env}
            attestation = {'schema_version': 1, 'runtime': {'manifest': runtime_manifest,
                'identity': runtime_attestation.sha(action_cache.canonical(runtime_manifest))},
                'provider': provider, 'configuration': {'gateway': gateway, 'harness': harness}}
            attestation['identity'] = runtime_attestation.sha(action_cache.canonical(attestation))
            sealed = runtime_attestation.environment(attestation, input_directory=frozen)
            (frozen / 'gateway.json').write_text(json.dumps({**gateway, 'analysis_tools_file': str(frozen / 'tools.json'),
                                                            'runtime_environment': sealed}))
            item = {'input_directory': str(frozen), 'max_output_tokens': 8192, 'max_tool_calls': 8,
                    'tool_count': 0, 'timeout_seconds': 90, 'model': 'glm-5.3', 'reasoning': 'high',
                    'runtime_attestation': attestation,
                    'request_contract': policy, 'environment_profile': {'files_sha256': {
                        'benchmarks/harnesses/zcode.py': zcode.sha(ROOT / 'benchmarks/harnesses/zcode.py')}}}
            proof = {'identity': attestation['identity'], 'status': 'verified_local_content_and_route'}
            events = [
                {'id': 'offline', 'object': 'chat.completion.chunk', 'model': 'glm-5.3',
                 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'REQUEST_BUDGET_OK'}, 'finish_reason': None}]},
                {'id': 'offline', 'object': 'chat.completion.chunk', 'model': 'glm-5.3',
                 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15,
                           'prompt_tokens_details': {'cached_tokens': 0}}}]
            body = b''.join(b'data: ' + json.dumps(e).encode() + b'\n\n' for e in events) + b'data: [DONE]\n\n'
            calls, launches = [], []
            def upstream(request, **kwargs):
                calls.append(json.loads(request.data)); response = Response(body)
                response.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=lambda value: None)))
                return response
            original_popen = subprocess.Popen
            def launch(*args, **kwargs):
                launches.append(kwargs['env'].get('REA_GHIDRA_SEED_FILE'))
                return original_popen(*args, **kwargs)
            with patch.object(zcode, 'direct_opener', return_value=SimpleNamespace(open=upstream)), \
                 patch.object(zcode.subprocess, 'Popen', side_effect=launch):
                raw = zcode.launch(directory / 'run', item, credentials=('https://frozen.invalid/v4', 'OFFLINE-DUMMY'),
                                   fixture=False, attestation_proof=proof)
            self.assertEqual(raw['exit_code'], 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(launches, [str(frozen / 'analysis.seeds')])
            self.assertEqual(calls[0]['max_tokens'], 8192)
            self.assertTrue(any(artifact_format.NOTICE.decode() in text for text in text_values(calls[0])))
            self.assertEqual(raw['request_budget']['status'], 'wire_enforced')
            # This simulated production forward was intercepted in memory; Node
            # and Python permit only loopback sockets. No provider was contacted.
            (frozen / 'analysis.seeds').unlink()
            with patch.object(zcode, 'Recorder') as recorder, patch.object(zcode.subprocess, 'Popen') as child:
                with self.assertRaises(FileNotFoundError):
                    zcode.launch(directory / 'bad', item, credentials=('https://frozen.invalid/v4', 'OFFLINE-DUMMY'),
                                 fixture=False, attestation_proof=proof)
                recorder.assert_not_called(); child.assert_not_called()

    def test_actual_installed_dsh_provider_serialization(self):
        self.check_installed_dsh_serialization(without_evidence=False)

    def test_actual_installed_dsh_premises_only_has_no_tools(self):
        self.check_installed_dsh_serialization(without_evidence=True)

    def check_installed_dsh_serialization(self, *, without_evidence):
        module = backend()
        for protocol in ('anthropic-messages', 'openai-responses'):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory(prefix='rea-request-client-') as temporary:
                directory = Path(temporary)
                current = task(protocol, 8192)
                frozen = current['runtime_attestation']['configuration']
                frozen['harness']['timeout_seconds'] = 180
                if without_evidence:
                    frozen['harness']['allowed_tool_names'] = []
                current['request_contract'] = frozen['harness']['request_contract'] = contract.build(
                    current['runtime_attestation']['provider'], {'id': current['model'], 'reasoning': 'high'}, frozen['gateway'], frozen['harness'])
                called = []
                def upstream(request, row):
                    called.append(json.loads(request.data))
                    return Response(client_response(protocol))
                with module.transport(directory, current, fixture_upstream=upstream) as proxy:
                    harness_config = json.loads((ROOT/'benchmarks/config/pilot.json').read_bytes())
                    harness_config.update(max_output_tokens=128000, request_contract=current['request_contract'])
                    catalog = directory / 'tools.json'
                    catalog.write_bytes(b'{"tools":[]}' if without_evidence else
                                        (ROOT/'benchmarks/config/rea-tools.json').read_bytes())
                    harness_config['analysis_tools_file'] = str(catalog)
                    config_path = directory/'harness-config.json'
                    config_path.write_text(json.dumps(harness_config))
                    spec = {'baseURL': proxy.base_url, 'provider': current['runtime_attestation']['provider']['provider'],
                            'model': current['model'], 'directory': str(directory), 'prompt': public_premise_prompt(),
                            'tools': str(catalog)}
                    if without_evidence:
                        from capabilities.evidence_access import NOTICE
                        spec['prompt'] += NOTICE.decode()
                    (directory / 'spec.json').write_text(json.dumps(spec))
                    child = subprocess.Popen([str(NODE), '--require', str(ROOT / 'benchmarks/scripts/offline-network.cjs'),
                        str(ROOT / 'benchmarks/scripts/request-client.mjs'), str(directory / 'spec.json')], cwd=ROOT,
                        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(directory),
                             'REA_BENCHMARK_CONFIG': str(config_path)},
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                    try:
                        stdout, stderr = child.communicate(timeout=150)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        stdout, stderr = child.communicate(timeout=5)
                        logs = [(p.name, p.read_text()[-2500:]) for p in directory.glob('runtime-*.*')]
                        self.fail('Actual client timed out: ' + repr(logs) + stderr.decode()[-1500:] + '\n' + json.dumps(proxy.gate.summary())[:2000])
                    self.assertEqual(child.returncode, 0, stderr.decode()[-4500:] + '\n' + json.dumps(proxy.gate.summary())[:2000])
                    self.assertEqual(len(called), 1)
                    if without_evidence:
                        self.assertFalse(called[0].get('tools'), 'Analysis tools leaked into premises-only DSH request')
                        self.assertFalse((directory / 'replay.json').exists(),
                                         'Premises-only DSH started an unnecessary analysis server')
                        self.assertNotIn('mcp-client(rea)', (directory / 'runtime-stderr.log').read_text(),
                                         'Premises-only DSH retained an MCP startup failure')
                    self.assertTrue(any(spec['prompt'] in text for text in text_values(called[0])),
                                    'The actual DSH request omitted the supplied public premises')
                    self.assertEqual(called[0][current['request_contract']['cap_field']], 8192)
                    self.assertEqual(proxy.gate.summary()['status'], 'wire_enforced')
                    self.assertEqual(proxy.gate.summary()['provider_forwards'], 0)

    def test_actual_installed_zcode_premises_only_has_no_tools(self):
        from capabilities.evidence_access import NOTICE
        spec = importlib.util.spec_from_file_location('no_evidence_zcode', ROOT / 'benchmarks/harnesses/zcode.py')
        zcode = importlib.util.module_from_spec(spec); spec.loader.exec_module(zcode)
        def final_response(handler, sequence, request):
            event = {'id': 'offline-no-evidence', 'object': 'chat.completion.chunk', 'model': 'glm-5.3',
                     'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'OFFLINE_CONTROL_OK'},
                                  'finish_reason': 'stop'}],
                     'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}
            body = ('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode()
            (handler.server.directory / f'wire/response-{sequence:03d}.sse').write_bytes(body)
            handler.server.observations.append({'sequence': sequence, 'usage': event['usage']})
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/event-stream')
            handler.send_header('Content-Length', str(len(body)))
            handler.end_headers(); handler.wfile.write(body)
        with tempfile.TemporaryDirectory(prefix='rea-no-evidence-zcode-') as temporary:
            directory = Path(temporary); frozen = directory / 'inputs'; frozen.mkdir()
            prompt = public_premise_prompt() + NOTICE.decode()
            for name, raw in {'target': b'', 'manifest.json': b'{}', 'prompt.txt': prompt.encode(),
                              'tools.json': b'{"tools":[]}'}.items():
                (frozen / name).write_bytes(raw)
            (frozen / 'gateway.json').write_text(json.dumps({'analysis_tools_file': str(frozen / 'tools.json')}))
            item = {'input_directory': str(frozen), 'max_output_tokens': 8192, 'max_tool_calls': 8,
                    'tool_count': 0, 'timeout_seconds': 90}
            with patch.object(zcode.Handler, 'fixture_response', final_response):
                raw = zcode.launch(directory / 'run', item, fixture=True)
            self.assertEqual(raw['exit_code'], 0)
            self.assertFalse(raw['policy_violations'])
            self.assertFalse(raw['upstream_error'])
            requests = list((directory / 'run/wire').glob('request-*.json'))
            self.assertEqual(len(requests), 1)
            request = json.loads(requests[0].read_bytes())
            self.assertFalse(request.get('tools'), 'Analysis tools leaked into premises-only ZCode request')
            self.assertTrue(any(prompt in text for text in text_values(request)))
            self.assertEqual(raw['request_budget']['provider_forwards'], 0)

    def test_actual_http_boundary_rejects_bad_caps_and_forwards_exact_valid_bytes(self):
        module = backend()
        called = []
        def upstream(request, row):
            called.append(request)
            return Response(b'data: {"type":"response.completed","response":{"usage":{"input_tokens":10,"output_tokens":5}}}\n\n')
        with tempfile.TemporaryDirectory() as temporary, module.transport(Path(temporary), task(), fixture_upstream=upstream) as proxy:
            url = proxy.base_url + '/responses'
            for cap in (None, 0, 129):
                body = payload()
                if cap is None:
                    body.pop('max_output_tokens')
                else:
                    body['max_output_tokens'] = cap
                request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(request)
            self.assertFalse(called)
            raw = json.dumps(payload()).encode()
            with urllib.request.urlopen(urllib.request.Request(url, data=raw, headers={'Content-Type': 'application/json'})) as response:
                self.assertIn(b'response.completed', response.read())
            self.assertEqual(called[0].data, raw)
            self.assertEqual(called[0].full_url, 'https://frozen.invalid/v1/responses')
            self.assertEqual(proxy.gate.summary()['provider_forwards'], 0)
            self.assertEqual(proxy.gate.summary()['validated_forwards'], 1)


class DispatchLedgerTests(unittest.TestCase):
    def test_budget_exhaustion_stops_the_model_queue_before_its_next_case(self):
        import run
        from capabilities import coordinator, registration
        selected = [{'model': 'glm-5.3', 'experiment': 'fixture', 'suite': 'fixture',
                     'case': name, 'status': 'not_started', 'condition': 'fixture'} for name in ('first', 'second')]
        fake_registry = SimpleNamespace(begin=lambda *_a, **_k: None,
            complete=lambda _measurement, entries, *_a: (entries, None))
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}):
            root = Path(temporary)
            with patch.object(run, 'ROOT', root), patch.object(run, 'BENCH', root/'benchmarks'), \
                 patch.object(registration, 'Registry', return_value=fake_registry), \
                 patch.object(coordinator, 'prepare_selected', return_value=selected), \
                 patch.object(run, 'execute', side_effect=contract.BudgetExhausted('Task output budget exhausted')) as execute:
                with self.assertRaisesRegex(RuntimeError, 'Benchmark incomplete'):
                    run.run_batch(selected, {'execution': {'max_parallel_models': 1}}, offline=True)
                self.assertEqual(execute.call_count, 1)
            receipt = json.loads((root/'benchmarks/config/benchmark-status.json').read_bytes())
            self.assertEqual(receipt['unprocessed_tasks'], 1)
            self.assertEqual(receipt['provider_launches'], 0)
            self.assertEqual(receipt['failures'][0]['case'], 'first')

    def test_zcode_budget_stop_is_not_an_environment_failure_and_keeps_usage(self):
        import records
        spec = importlib.util.spec_from_file_location('zcode_budget_dispatch', ROOT/'benchmarks/harnesses/dispatch.py')
        dispatch = importlib.util.module_from_spec(spec); spec.loader.exec_module(dispatch)
        check = gate('openai-chat-completions'); check.contract['max_requests'] = 1
        row, _ = check.begin(check.contract['path'], json.dumps(payload('openai-chat-completions')).encode())
        check.forward(row, fixture=True)
        usage = {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110,
                 'prompt_tokens_details': {'cached_tokens': 20}}
        check.observe(row, usage); check.finish(row, status=200)
        try:
            check.begin(check.contract['path'], json.dumps({**payload('openai-chat-completions'), 'metadata': {'next': True}}).encode())
        except contract.Violation:
            pass
        raw = {'requests': check.requests, 'request_budget': check.summary(), 'elapsed_seconds': 1,
            'policy_violations': [{'category': check.exhausted}], 'upstream_error': None,
            'exit_code': -15, 'timed_out': False,
            'usage_observations_raw': [{'sequence': 1, 'usage': usage}]}
        backend = SimpleNamespace(launch=lambda *_a, **_k: raw, collect_wire=lambda *_a: ('', ['glm-5.3']))
        current = {'runtime_attestation': {'identity': 'fixture'}, 'environment_profile': {},
            'condition': 'fixture', 'language': 'cpp', 'id': 'fixture', 'binary_sha256': 'a'*64, 'prompt_sha256': 'b'*64}
        model = {'id': 'glm-5.3', 'harness': 'zcode', 'label': 'GLM fixture', 'reasoning': 'high'}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(dispatch, 'load_module', return_value=backend), \
                 patch.object(dispatch.runtime_attestation, 'glm_credentials', return_value=('https://unused.invalid', 'dummy')):
                with self.assertRaises(contract.BudgetExhausted):
                    dispatch._launch(directory, current, model, records=records)
            self.assertTrue((directory/'budget-exhausted.json').is_file())
            self.assertFalse((directory/'harness-failure.json').exists())
            result = json.loads((directory/'result.json').read_bytes())
            self.assertEqual(result['reported_usage']['totalTokens'], 110)
            self.assertEqual(result['request_budget']['status'], 'budget_exhausted')

    def test_deleting_task_contract_cannot_bypass_grading_admission(self):
        import run
        current = task()
        current['runtime_attestation']['identity'] = 'offline-fixture'
        current.pop('request_contract')
        runtime_root = ROOT / '.cache/benchmark-runtime'
        runtime_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='request-grade-control-', dir=runtime_root) as temporary:
            directory = Path(temporary)
            for phase in ('before', 'after'):
                (directory / ('runtime-verification-' + phase + '.json')).write_text(json.dumps({
                    'identity': 'offline-fixture', 'status': 'verified_local_content_and_route',
                    'provider': current['runtime_attestation']['provider']}))
            with self.assertRaisesRegex(contract.Violation, 'request_contract_changed'):
                run.grade_saved(directory, current)

    def test_dispatch_preserves_billed_usage_on_both_success_and_failure(self):
        spec = importlib.util.spec_from_file_location('budget_dispatch_control', ROOT / 'benchmarks/harnesses/dispatch.py')
        dispatch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dispatch)
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                current = task()
                current['runtime_attestation']['identity'] = 'offline-fixture'
                model = {'id': current['model'], 'harness': 'deepseek-harness'}
                @contextmanager
                def simulated_transport(_directory, _task):
                    try:
                        yield SimpleNamespace(base_url='http://127.0.0.1:1/v1', deadline_ms=1)
                    finally:
                        check = contract.Gate(current['request_contract'])
                        row, _ = check.begin(check.contract['path'], json.dumps(payload()).encode())
                        # Accounting simulation only: no socket/model is used.
                        check.forward(row)
                        check.observe(row, {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15,
                                            'input_tokens_details': {'cached_tokens': 2}})
                        check.finish(row, status=200)
                        (directory / 'request-budget.json').write_text(json.dumps(check.summary()))
                def child(*_args, **_kwargs):
                    (directory / 'harness-result.json').write_text(json.dumps({'model': current['model'], 'exit_code': int(failed), 'timed_out': False}))
                    if failed:
                        raise RuntimeError('Simulated client failure after a charged request')
                with patch.object(dispatch, 'load_module', return_value=SimpleNamespace(transport=simulated_transport)), \
                     patch.object(dispatch, '_launch_dsh', side_effect=child):
                    if failed:
                        with self.assertRaisesRegex(RuntimeError, 'Simulated client failure'):
                            dispatch._launch(directory, current, model)
                    else:
                        dispatch._launch(directory, current, model)
                result = json.loads((directory / 'result.json').read_bytes())
                self.assertEqual(result['reported_usage'], {'inputTokens': 8, 'outputTokens': 5, 'cacheReadTokens': 2, 'totalTokens': 15})
                self.assertTrue(result['usage_complete'])
                self.assertEqual((directory / 'harness-failure.json').exists(), failed)
                self.assertEqual(result['provider_forwards'], 1)  # simulated accounting event

    def test_budget_stop_preserves_usage_and_does_not_become_harness_failure(self):
        spec = importlib.util.spec_from_file_location('budget_stop_dispatch', ROOT / 'benchmarks/harnesses/dispatch.py')
        dispatch = importlib.util.module_from_spec(spec); spec.loader.exec_module(dispatch)
        current = task()
        current['runtime_attestation']['identity'] = 'offline-fixture'
        current['environment_profile'] = {}
        @contextmanager
        def simulated(directory, _task):
            check = contract.Gate(current['request_contract']); check.contract['max_requests'] = 1
            try:
                row, _ = check.begin(check.contract['path'], json.dumps(payload()).encode())
                check.forward(row, fixture=True)
                check.observe(row, {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
                check.finish(row, status=200)
                try:
                    check.begin(check.contract['path'], json.dumps({**payload(), 'metadata': {'next': True}}).encode())
                except contract.Violation:
                    pass
                yield SimpleNamespace(base_url='http://127.0.0.1:1/v1', deadline_ms=1, gate=check)
            finally:
                (directory/'request-budget.json').write_text(json.dumps(check.summary()))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(dispatch, 'load_module', return_value=SimpleNamespace(transport=simulated)), \
                 patch.object(dispatch, '_launch_dsh', side_effect=contract.BudgetExhausted('Stopped locally')):
                with self.assertRaises(contract.BudgetExhausted):
                    dispatch._launch(directory, current, {'id': current['model'], 'harness': 'deepseek-harness'})
            self.assertTrue((directory/'budget-exhausted.json').is_file())
            self.assertFalse((directory/'harness-failure.json').exists())
            result = json.loads((directory/'result.json').read_bytes())
            self.assertEqual(result['reported_usage']['totalTokens'], 15)
            self.assertEqual(result['request_budget']['status'], 'budget_exhausted')


if __name__ == '__main__':
    from check_codex_transport import CodexTransportTests
    sources = [Path(__file__), ROOT / 'benchmarks/scripts/request-client.mjs', ROOT / 'benchmarks/config/request-contract.json',
        ROOT / 'benchmarks/scripts/request_contract.py', ROOT / 'benchmarks/harnesses/provider-transport.py',
        ROOT / 'benchmarks/harnesses/deepseek-harness.mjs', ROOT / 'benchmarks/harnesses/zcode.py',
        ROOT / 'benchmarks/harnesses/dispatch.py', ROOT / 'benchmarks/scripts/runtime_attestation.py',
        ROOT / 'benchmarks/scripts/run.py', ROOT / 'benchmarks/scripts/runtime_storage.py', ROOT / 'benchmarks/scripts/offline-network.cjs',
        ROOT / 'benchmarks/scripts/capabilities/coordinator.py',
        ROOT / 'benchmarks/scripts/capabilities/evidence_access.py', ROOT / 'benchmarks/scripts/check_codex_transport.py',
        ROOT / 'benchmarks/harnesses/provider-auth.mjs', ROOT / 'benchmarks/config/runtime-attestation.json']
    sources += [ROOT / '.tools/harness-0.2.1-rc.2/node_modules' / name for name in (
        '@deepseek-ai/dsh-llm-deepseek/lib/index.js', '@deepseek-ai/dsh-llm-pi-ai/lib/index.js',
        '@earendil-works/pi-ai/dist/api/openai-responses.js', '@earendil-works/pi-ai/dist/api/openai-responses-shared.js',
        '@earendil-works/pi-ai/dist/api/openai-codex-responses.js')]
    sources.append(ROOT / '.tools/zcode-3.16.1/agent/zcode.cjs')
    started = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    original = socket.socket.connect
    def local_only(connection, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise AssertionError('External network/model access forbidden')
        return original(connection, address)
    with patch.object(socket.socket, 'connect', local_only):
        result = unittest.main(verbosity=2, exit=False).result
    if len(sys.argv) == 1:
        current = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value for name, value in started.items())
        receipt = {'schema_version': 1, 'passed': result.wasSuccessful() and current, 'model_api_calls': 0,
                   'checks': result.testsRun, 'inputs_sha256': started,
                   'installed_dsh_protocols': 3, 'installed_zcode_clients': 1,
                   'historical_budgets_retroactively_certified': False}
        (ROOT / 'benchmarks/config/request-contract-validation.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
        if not current:
            raise RuntimeError('Request-control sources changed during validation')
    sys.exit(0 if result.wasSuccessful() else 1)
