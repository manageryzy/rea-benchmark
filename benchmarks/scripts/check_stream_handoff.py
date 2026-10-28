"""Real loopback stream handoff controls; no provider calls."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
import request_contract as contract
from check_requests import ROOT, Response, backend, client_response, payload, task


def chat_response():
    events = [{'choices': [{'index': 0, 'delta': {'content': 'fixture'}, 'finish_reason': None}]},
              {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
              {'choices': [], 'usage': {'prompt_tokens': 10, 'prompt_tokens_details': {'cached_tokens': 2}, 'completion_tokens': 5, 'total_tokens': 15}}]
    return b''.join(b'data: ' + json.dumps(x).encode() + b'\n\n' for x in events) + b'data: [DONE]\n\n'


class HeldEOF(Response):
    def __init__(self, raw, release):
        super().__init__(raw)
        self.release = release
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=lambda _: None)))

    def read1(self, size=-1):
        value = super().read1(size)
        if not value and not self.release.wait(3): raise TimeoutError('Offline EOF barrier expired')
        return value


@contextmanager
def proxy(kind, release, *, wallet=False, maximum=128, budget_context=None):
    called = []
    protocol = 'anthropic-messages' if kind == 'dsh' else 'openai-chat-completions'
    raw = client_response(protocol) if kind == 'dsh' else chat_response()
    if wallet and kind == 'dsh':
        # The old minimal fixture deliberately omitted cache usage. A wallet
        # contract requires the same complete component ledger as production.
        raw = raw.replace(b'"input_tokens": 10', b'"input_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0')
    def upstream(request, *_args, **_kwargs):
        called.append(request)
        return HeldEOF(raw, release)
    with tempfile.TemporaryDirectory(prefix='rea-handoff-') as temporary:
        directory = Path(temporary)
        if kind == 'dsh':
            fixture = task(protocol, maximum=maximum)
            if wallet:
                provider = fixture['runtime_attestation']['provider']
                provider['base_url'] = 'https://api.deepseek.com/anthropic'
                harness = fixture['runtime_attestation']['configuration']['harness']
                harness['evidence_paging'] = {'id':'lean-evidence-v4'}
                if budget_context is not None: harness['budget_context'] = budget_context
                fixture['request_contract'] = harness['request_contract'] = contract.build(provider,
                    {'id':fixture['model']}, fixture['runtime_attestation']['configuration']['gateway'], harness)
            with backend().transport(directory, fixture, fixture_upstream=upstream) as server:
                yield server, server.base_url + '/messages', called
        else:
            spec = importlib.util.spec_from_file_location('handoff_zcode', ROOT/'benchmarks/harnesses/zcode.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            (directory/'tools.json').write_text('{"tools": []}'); (directory/'wire').mkdir()
            item = {'input_directory': str(directory), 'max_output_tokens': maximum, 'max_tool_calls': 8,
                    'model': 'glm-5.3', 'reasoning': 'high'}
            provider = {'protocol': protocol, 'model': 'glm-5.3'}
            gateway = {'max_tool_calls': 8}
            harness = {'max_output_tokens': maximum, 'timeout_seconds': 30, 'tool_timeout_ms': 1000, 'allowed_tool_names': []}
            if wallet:
                provider.update(provider='bigmodel-standard-api', base_url='https://open.bigmodel.cn/api/paas/v4')
                harness['evidence_paging'] = {'id':'lean-evidence-v4'}
                if budget_context is not None: harness['budget_context'] = budget_context
            item['request_contract'] = harness['request_contract'] = contract.build(provider,
                {'id': 'glm-5.3', 'reasoning': 'high'}, gateway, harness)
            item['runtime_attestation'] = {'provider': provider, 'configuration': {'gateway': gateway, 'harness': harness}}
            server = module.Recorder(directory, item, ('https://offline.invalid', 'offline-dummy'))
            server.opener = SimpleNamespace(open=upstream)
            original = server.gate.forward
            server.gate.forward = lambda row: original(row, fixture=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try: yield server, f'http://127.0.0.1:{server.server_port}' + server.gate.contract['path'], called
            finally:
                release.set(); server.shutdown(); server.server_close(); thread.join(3)


def send(kind, url, sequence):
    value = payload('anthropic-messages' if kind == 'dsh' else 'openai-chat-completions')
    if kind == 'glm': value.update(model='glm-5.3', tools=[])
    value['messages'][0]['content'] = 'Offline serial request ' + str(sequence)
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        urllib.request.Request(url, data=json.dumps(value).encode(), headers={'Content-Type': 'application/json'}), timeout=4)


class HandoffTests(unittest.TestCase):
    def serial(self, kind):
        release = threading.Event(); saw_end = threading.Event(); errors = []
        with proxy(kind, release) as (server, url, called):
            first = send(kind, url, 1)
            self.assertTrue(first.readline().startswith((b'event:', b'data:')))
            def client():
                try:
                    while True:
                        line = first.readline()
                        if not line: raise AssertionError('Missing stream terminator')
                        if b'data: [DONE]' in line or (line.startswith(b'data:') and b'"type": "message_stop"' in line): break
                    saw_end.set()
                    self.assertIsNone(server.gate.in_flight)
                    self.assertEqual(server.gate.task_usage()['output_tokens_charged_or_reserved'], 5)
                    with send(kind, url, 2) as second: self.assertEqual(second.status, 200); second.read()
                    first.read()
                except BaseException as error: errors.append(error)
                finally: first.close()
            thread = threading.Thread(target=client); thread.start()
            try: self.assertFalse(saw_end.wait(.05), 'Terminal frame escaped before settlement')
            finally: release.set()
            thread.join(4); self.assertFalse(thread.is_alive())
            if errors: raise errors[0]
            self.assertEqual(len(called), 2)
            self.assertEqual(server.gate.summary()['provider_forwards'], 0)

    def concurrent(self, kind):
        release = threading.Event()
        with proxy(kind, release) as (server, url, called):
            first = send(kind, url, 1); first.readline()
            try:
                with self.assertRaises(urllib.error.HTTPError) as error: send(kind, url, 2)
                self.assertEqual(error.exception.code, 400)
                self.assertIn(b'concurrent_provider_requests_denied', error.exception.read())
                self.assertEqual(len(called), 1)
            finally: release.set(); first.read(); first.close()

    def test_dsh_serial_handoff(self): self.serial('dsh')
    def test_glm_serial_handoff(self): self.serial('glm')
    def test_dsh_true_concurrent_is_rejected(self): self.concurrent('dsh')
    def test_glm_true_concurrent_is_rejected(self): self.concurrent('glm')

    def test_fragmented_streams_preserve_bytes_and_usage(self):
        for protocol in ['anthropic-messages', 'openai-responses', 'openai-codex-responses', 'openai-chat-completions']:
            with self.subTest(protocol=protocol):
                gate = contract.Gate(task(protocol)['request_contract'])
                row, _ = gate.begin(gate.contract['path'], json.dumps(payload(protocol)).encode()); gate.forward(row, fixture=True)
                relay = contract.ResponseRelay(contract.ResponseObserver(gate, row))
                raw = chat_response() if protocol == 'openai-chat-completions' else client_response(protocol)
                ready = b''.join(relay.feed(bytes([byte])) for byte in raw)
                complete, tail = relay.end(); self.assertTrue(complete); self.assertTrue(tail)
                gate.finish(row, status=200, complete=complete)
                self.assertEqual(ready + tail, raw)
                self.assertEqual(gate.task_usage()['output_tokens_charged_or_reserved'], 5)

    def test_incomplete_or_invalid_trailer_never_releases_success(self):
        for trailer in [b'', b'data: {bad}\n\n']:
            gate = contract.Gate(task('openai-chat-completions')['request_contract'])
            row, _ = gate.begin(gate.contract['path'], json.dumps(payload('openai-chat-completions')).encode())
            relay = contract.ResponseRelay(contract.ResponseObserver(gate, row))
            self.assertNotIn(b'"finish_reason": "stop"', relay.feed(chat_response().replace(b'data: [DONE]\n\n', b'')))
            if trailer:
                with self.assertRaises(contract.Violation): relay.feed(trailer)
            else: self.assertEqual(relay.end(), (False, b''))

    def test_json_body_waits_for_settlement(self):
        gate = contract.Gate(task('anthropic-messages')['request_contract'])
        row, _ = gate.begin(gate.contract['path'], json.dumps(payload('anthropic-messages', stream=False)).encode())
        relay = contract.ResponseRelay(contract.ResponseObserver(gate, row))
        raw = b'{"stop_reason":"end_turn","usage":{"input_tokens":10,"output_tokens":5}}'
        self.assertEqual(relay.feed(raw), b''); self.assertEqual(relay.end(), (True, raw))

    def test_response_cap_remains_enforced_after_handoff(self):
        gate = contract.Gate(task('anthropic-messages')['request_contract'])
        row, _ = gate.begin(gate.contract['path'], json.dumps(payload('anthropic-messages')).encode())
        gate.forward(row, fixture=True)
        relay = contract.ResponseRelay(contract.ResponseObserver(gate, row))
        relay.feed(client_response('anthropic-messages').replace(b'end_turn', b'max_tokens'))
        complete, tail = relay.end(); self.assertTrue(tail)
        gate.finish(row, status=200, complete=complete)
        following = payload('anthropic-messages'); following['messages'][0]['content'] = 'next turn'
        with self.assertRaisesRegex(contract.Violation, 'response_output_limit_reached'):
            gate.begin(gate.contract['path'], json.dumps(following).encode())


if __name__ == '__main__': unittest.main()
