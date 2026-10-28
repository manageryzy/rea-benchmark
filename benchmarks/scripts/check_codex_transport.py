#!/usr/bin/python3
"""Codex account transport controls using dummy OAuth and loopback only."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import request_contract as contract
from check_requests import ROOT, NODE, Response, backend, client_response


def task():
    provider = {'protocol': 'openai-codex-responses', 'provider': 'openai-codex',
                'model': 'gpt-6.1-sol', 'base_url': 'https://frozen.invalid/backend-api'}
    gateway = {'max_tool_calls': 8}
    harness = {'max_output_tokens': 8192, 'timeout_seconds': 90,
               'tool_timeout_ms': 10000, 'allowed_tool_names': []}
    harness['request_contract'] = contract.build(provider, {'id': provider['model'], 'reasoning': 'high'}, gateway, harness)
    return {'model': provider['model'], 'reasoning': 'high', 'request_contract': harness['request_contract'],
            'runtime_attestation': {'provider': provider, 'configuration': {'gateway': gateway, 'harness': harness},
                'runtime': {'manifest': {'environment': {'SSL_CERT_FILE': '/etc/ssl/certs/ca-certificates.crt'}}}}}


def body(**changes):
    return json.dumps({'model': 'gpt-6.1-sol', 'stream': True, 'store': False,
                      'reasoning': {'effort': 'high'}, 'input': [], **changes}).encode()


def compress(raw):
    return subprocess.run([str(NODE), '-e', "process.stdout.write(require('node:zlib').zstdCompressSync(require('node:fs').readFileSync(0)))"],
                          input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def send(proxy, raw, encoding='zstd', **extra_headers):
    return urllib.request.urlopen(urllib.request.Request(proxy.base_url + '/codex/responses', data=raw,
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer offline-dummy',
                 'chatgpt-account-id': 'offline-fixture-account', 'Content-Encoding': encoding, **extra_headers}))


class CodexTransportTests(unittest.TestCase):
    def test_compressed_requests_validate_before_forward_with_frozen_headers(self):
        module, called = backend(), []
        def upstream(request, row):
            called.append(request)
            return Response(client_response('openai-responses'))
        with tempfile.TemporaryDirectory() as temporary, module.transport(temporary, task(), fixture_upstream=upstream) as proxy:
            raw = compress(body())
            with send(proxy, raw, originator='pi', **{'OpenAI-Beta': 'responses=experimental',
                      'session-id': 'offline-session', 'X-Credential-Canary': 'must-not-forward'}) as response:
                response.read()
            self.assertEqual(len(called), 1)
            request = called[0]
            self.assertEqual(request.full_url, 'https://frozen.invalid/backend-api/codex/responses')
            self.assertEqual(request.data, raw)
            headers = {k.lower(): v for k, v in request.header_items()}
            self.assertEqual(headers['content-encoding'], 'zstd')
            self.assertEqual(headers['chatgpt-account-id'], 'offline-fixture-account')
            self.assertEqual(headers['openai-beta'], 'responses=experimental')
            self.assertNotIn('x-credential-canary', headers)
            summary = proxy.gate.summary()
            self.assertEqual(summary['status'], 'observed_limits_enforced')
            self.assertEqual(summary['provider_forwards'], 0)
            self.assertEqual(summary['validated_forwards'], 1)
            self.assertIsNone(summary['requests'][0]['cap'])
            self.assertEqual(summary['requests'][0]['wire_encoding'], 'zstd')

    def test_invalid_or_oversized_compression_never_forwards(self):
        module = backend()
        for raw in (b'not-zstd', compress(b'X' * 2048)):
            with self.subTest(length=len(raw)), tempfile.TemporaryDirectory() as temporary, \
                 module.transport(temporary, task(), fixture_upstream=lambda *_: self.fail('Invalid request forwarded')) as proxy:
                proxy.gate.contract['max_request_bytes'] = 1024
                with self.assertRaises(urllib.error.HTTPError) as error:
                    send(proxy, raw)
                self.assertIn(b'invalid_or_oversized_compressed_request', error.exception.read())
                self.assertEqual(proxy.gate.summary()['provider_forwards'], 0)
                self.assertEqual(proxy.gate.summary()['validated_forwards'], 0)

    def test_wrong_model_and_unknown_encoding_never_forward(self):
        module = backend()
        for raw, encoding, category in ((compress(body(model='gpt-other')), 'zstd', b'request_model_changed'),
                                        (body(), 'gzip', b'unsupported_request_content_encoding')):
            with tempfile.TemporaryDirectory() as temporary, \
                 module.transport(temporary, task(), fixture_upstream=lambda *_: self.fail('Invalid request forwarded')) as proxy:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    send(proxy, raw, encoding)
                self.assertIn(category, error.exception.read())
                self.assertEqual(proxy.gate.summary()['validated_forwards'], 0)

    def test_actual_installed_dsh_codex_serialization_is_sse_without_cap(self):
        module, called, websocket_attempts = backend(), [], []
        def upstream(request, row):
            decoded = module.decoded_body(request.data, request.headers.get('Content-encoding'), current['request_contract'], 30)
            called.append(json.loads(decoded))
            return Response(client_response('openai-responses'))
        original_get = module.Handler.do_GET
        def get(handler):
            websocket_attempts.append(handler.path)
            return original_get(handler)
        with tempfile.TemporaryDirectory(prefix='rea-codex-client-') as temporary:
            directory = Path(temporary); current = task()
            catalog = directory / 'tools.json'; catalog.write_bytes(b'{"tools":[]}')
            config = json.loads((ROOT / 'benchmarks/config/pilot.json').read_bytes())
            config.update(max_output_tokens=8192, request_contract=current['request_contract'], analysis_tools_file=str(catalog))
            config_path = directory / 'config.json'; config_path.write_text(json.dumps(config))
            with patch.object(module.Handler, 'do_GET', get), module.transport(directory, current, fixture_upstream=upstream) as proxy:
                spec = {'baseURL': proxy.base_url, 'provider': 'openai-codex', 'model': current['model'],
                        'directory': str(directory), 'tools': str(catalog), 'prompt': 'Reply REQUEST_BUDGET_OK.'}
                path = directory / 'spec.json'; path.write_text(json.dumps(spec))
                child = subprocess.Popen([str(NODE), '--require', str(ROOT / 'benchmarks/scripts/offline-network.cjs'),
                    str(ROOT / 'benchmarks/scripts/request-client.mjs'), str(path)], cwd=ROOT,
                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': temporary, 'REA_BENCHMARK_CONFIG': str(config_path)},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                try:
                    stdout, stderr = child.communicate(timeout=150)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL); child.communicate(timeout=5)
                    self.fail('Offline DSH Codex fixture timed out')
                self.assertEqual(child.returncode, 0, stderr.decode()[-3000:])
                self.assertIn(b'actual openai-codex DSH request', stdout)
                self.assertEqual(len(called), 1)
                self.assertEqual(websocket_attempts, [])
                self.assertFalse(contract.CAP_FIELDS.intersection(called[0]))
                self.assertEqual(called[0]['reasoning']['effort'], 'high')
                self.assertFalse(called[0].get('tools'))
                summary = proxy.gate.summary()
                self.assertEqual(summary['status'], 'observed_limits_enforced')
                self.assertEqual(summary['provider_forwards'], 0)
                self.assertEqual(summary['validated_forwards'], 1)
                self.assertEqual(summary['requests'][0]['wire_encoding'], 'zstd')

    def test_explicit_codex_preference_never_falls_back_to_api_key(self):
        script = r'''
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolveProvider,credentialsFor} from './benchmarks/harnesses/provider-auth.mjs';
const policy=JSON.parse(await readFile('./benchmarks/config/runtime-attestation.json','utf8'));
policy.gpt_provider='openai-codex';
const claims={exp:Date.now()/1000+7200,'https://api.openai.com/auth':{chatgpt_account_id:'offline-fixture'}};
const tokens={access_token:'offline.'+Buffer.from(JSON.stringify(claims)).toString('base64url')+'.dummy',refresh_token:'OFFLINE-DUMMY'};
const options={policy,env:{HOME:'/offline',OPENAI_API_KEY:'OFFLINE-IGNORED'},readJSON:async()=>({OPENAI_API_KEY:'OFFLINE-ALSO-IGNORED',tokens})};
const model={id:'gpt-6.1-sol',harness:'deepseek-harness'};
const resolved=await resolveProvider(model,options);
assert.equal(resolved.plan.provider,'openai-codex');
assert.equal(resolved.plan.credential_source,'codex-user-home');
const credentials=await credentialsFor(resolved.plan,options);
assert.equal(credentials.records['llm-pi-ai/openai-codex'].kind,'grant');
await assert.rejects(resolveProvider(model,{...options,readJSON:async()=>({OPENAI_API_KEY:'OFFLINE-IGNORED'})}),/Missing/);
await assert.rejects(credentialsFor(resolved.plan,{...options,policy:{...policy,gpt_provider:'openai'}}),/no fallback/);
process.stdout.write('PASS: explicit Codex OAuth preference and no fallback');
'''
        child = subprocess.run([str(NODE), '--require', str(ROOT / 'benchmarks/scripts/offline-network.cjs'),
            '--input-type=module', '-e', script], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(child.returncode, 0, child.stderr.decode()[-2000:])
        self.assertIn(b'PASS:', child.stdout)


if __name__ == '__main__':
    unittest.main()
