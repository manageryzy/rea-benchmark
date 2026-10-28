"""Focused startup-once and response-limit controls; no model/network calls."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import runtime_snapshot as snapshot
import runtime_attestation as runtime
import request_contract as contract
import run
from check_requests import gate, payload
from check_budget_runner import CompletedBudgetRunnerTests


class StartupOnce(unittest.TestCase):
    def setUp(self):
        snapshot._RUNTIMES.clear()
        snapshot._PID = os.getpid()
        self.capture = Mock(return_value={'identity': 'frozen', 'manifest': {'components': {}}})
        self.policy = {'verification_mode': 'startup_once'}

    def get(self, harness='dsh'):
        return snapshot.current(harness, root='/fixture', policy=self.policy, capture=self.capture)

    def test_concurrent_preparation_and_boundaries_capture_once(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            values = list(pool.map(lambda _: self.get(), range(12)))
        self.assertEqual(self.capture.call_count, 1)
        self.assertTrue(all(v['identity'] == 'frozen' for v in values))
        self.get('zcode'); self.assertEqual(self.capture.call_count, 2)

    def test_policy_change_or_restart_repopulates(self):
        self.get(); self.policy['version'] = 2; self.get()
        snapshot._PID = -1; self.get()
        self.assertEqual(self.capture.call_count, 3)

    def test_failures_not_cached_and_caller_mutation_isolated(self):
        self.capture.side_effect = [ValueError('capture failed'), {'identity': 'frozen'}]
        with self.assertRaises(ValueError): self.get()
        v = self.get(); v['identity'] = 'tampered'
        self.assertEqual(self.get()['identity'], 'frozen')
        self.assertEqual(self.capture.call_count, 2)

    def test_intentional_startup_trust_and_strict_fallback(self):
        with patch.object(runtime, 'config', return_value=self.policy), patch.object(runtime, 'capture', self.capture):
            a = runtime.current_runtime('dsh')
            self.capture.return_value = {'identity': 'host changed'}
            self.assertEqual(runtime.current_runtime('dsh', fresh=True), a)
        with patch.object(runtime, 'config', return_value={'verification_mode': 'strict_per_task'}), patch.object(runtime, 'capture', self.capture):
            self.assertEqual(runtime.current_runtime('dsh', fresh=True)['identity'], 'host changed')
            self.assertEqual(self.capture.call_count, 2)

    def test_new_process_does_not_import_passes(self):
        script = "import runtime_snapshot as s; assert not s.active('dsh'); print(len(s._RUNTIMES))"
        self.get()
        out = subprocess.check_output(['/usr/bin/python3', '-c', script], cwd=Path(__file__).parent)
        self.assertEqual(out.strip(), b'0')


class ResponseLimit(unittest.TestCase):
    def test_dsh_limit_drains_receipt_without_new_request_or_retry(self):
        dispatch = run.load_module(Path(__file__).resolve().parents[1]/'harnesses/dispatch.py', 'limit_dispatch_control')
        child = Mock()
        child.wait.return_value = 1
        child.poll.return_value = 1
        task = {'runtime_attestation': {'identity': 'offline-fixture'}, 'input_directory': '/tmp/unused'}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(dispatch.subprocess, 'Popen', return_value=child) as launch:
            with self.assertRaises(contract.BudgetExhausted):
                dispatch._launch_dsh(Path(temporary), task, None, 'http://127.0.0.1:1', 1,
                    budget_stop=lambda: 'response_output_limit_reached')
            child.wait.assert_called_once_with(timeout=5)
            self.assertEqual(launch.call_count, 1)

    def response(self, protocol, *, reason=True, complete=True):
        check = gate(protocol)
        row, _ = check.begin(check.contract['path'], json.dumps(payload(protocol)).encode())
        check.forward(row, fixture=True)
        observer = contract.ResponseObserver(check, row)
        if protocol == 'anthropic-messages':
            observer.event({'type':'message_delta','delta':{'stop_reason':'max_tokens' if reason else 'end_turn'},
                'usage':{'input_tokens':10,'output_tokens':128,'cache_read_input_tokens':0,'cache_creation_input_tokens':0}})
            observer.event({'type':'message_stop'})
        elif protocol == 'openai-chat-completions':
            observer.event({'choices':[{'index':0,'finish_reason':'length' if reason else 'stop'}],
                'usage':{'prompt_tokens':10,'completion_tokens':128,'total_tokens':138,'prompt_tokens_details':{'cached_tokens':0}}})
            observer.feed(b'data: [DONE]\n')
        else:
            observer.event({'type':'response.incomplete' if reason else 'response.completed',
                'response':{'status':'incomplete' if reason else 'completed', 'incomplete_details':{'reason':'max_output_tokens'},
                'output':[{'type':'message'}], 'usage':{'input_tokens':10,'output_tokens':128,'total_tokens':138,'input_tokens_details':{'cached_tokens':0}}}})
        self.assertIsNone(check.exhausted)  # Never terminate a stream mid-response.
        check.finish(row, status=200, complete=complete)
        return check

    def test_limit_stop_consistent_across_four_protocols(self):
        for protocol in ['anthropic-messages','openai-chat-completions','openai-responses','openai-codex-responses']:
            with self.subTest(protocol=protocol):
                check = self.response(protocol); summary = check.summary()
                self.assertEqual(summary['status'], 'budget_exhausted')
                self.assertEqual(summary['stop_reason'], 'response_output_limit_reached')
                self.assertFalse(summary['budget_exceeded'])
                self.assertFalse(contract.grading_allowed(summary, check.contract))
                self.assertEqual(summary['provider_forwards'], 0)
                with self.assertRaisesRegex(contract.Violation,'response_output_limit_reached'):
                    check.begin(check.contract['path'], json.dumps(payload(protocol)).encode())
                self.assertEqual(check.summary()['validated_forwards'], 1)

    def test_partial_transport_not_misclassified_as_limit(self):
        check = self.response('anthropic-messages', complete=False)
        self.assertIsNone(check.exhausted)
        self.assertFalse(check.summary()['transport_complete'])

    def test_exact_output_count_with_normal_finish_is_not_truncation(self):
        for protocol in ['anthropic-messages','openai-chat-completions','openai-responses']:
            check = self.response(protocol, reason=False)
            self.assertEqual(check.summary()['status'], 'wire_enforced')
            self.assertTrue(contract.grading_allowed(check.summary(), check.contract))

    def test_case_scoped_queue_continues_after_limit(self):
        helper = CompletedBudgetRunnerTests()
        calls, status = helper.batch(scope='case', failure=contract.BudgetExhausted)
        self.assertEqual(calls, [('limited','first'),('limited','second')])
        self.assertEqual(status['unprocessed_tasks'], 0)

    def test_installed_adapter_recognizes_saved_terminal_reason(self):
        root = Path(__file__).resolve().parents[2]
        events = root/'benchmarks/runs/complex-pilot-common-budget-20261019/conditions/compact-evidence-v3/deepseek-flash/rust/events.jsonl'
        terminal = [{'type':'status','phase':'turn_end','reason':{'kind':'max-tokens'}}]
        if events.exists():
            terminal = [e for l in events.read_text().splitlines() if (e:=json.loads(l)).get('phase')=='turn_end']
        script = ("import {responseLimitStop} from './benchmarks/harnesses/deepseek-harness.mjs'; "
            "const e=JSON.parse(process.argv[1]); if(!responseLimitStop(e)||responseLimitStop([{type:'status',phase:'turn_end',reason:{kind:'error'}}]))process.exit(1);")
        subprocess.run([str(root/'.tools/node-v24.22.0-linux-x64/bin/node'),'--input-type=module','-e',script,json.dumps(terminal)], cwd=root, check=True)


if __name__ == '__main__':
    unittest.main()
