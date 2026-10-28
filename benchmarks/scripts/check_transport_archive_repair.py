"""Focused controls for historical bytes and provider transport; zero model API."""
import errno
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from archive_profiles import exact_source
import archive_profiles
from check_requests import backend, Response, client_response, ROOT, NODE
from check_codex_transport import task, send, compress, body


def frame(value):
    return b'event: ' + value['type'].encode() + b'\ndata: ' + json.dumps(value).encode() + b'\n\n'


class ArchiveTests(unittest.TestCase):
    def test_exact_cas_bytes_despite_changed_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); name = 'benchmarks/scripts/run.py'; raw = b'old source\n'
            sha = hashlib.sha256(raw).hexdigest()
            source = root / name; source.parent.mkdir(parents=True); source.write_bytes(b'new source')
            cas = root / '.cache/benchmark-actions/v3/cas' / sha
            cas.parent.mkdir(parents=True); cas.write_bytes(raw)
            self.assertEqual(exact_source(name, sha, root=root), raw)
            cas.write_bytes(b'corrupt')
            with self.assertRaisesRegex(AssertionError, 'missing'):
                exact_source(name, sha, root=root)
            archive = root / 'archived'; archive.write_bytes(raw)
            self.assertEqual(exact_source(name, sha, root=root, archived=[archive]), raw)

    def test_current_missing_and_path_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p = root / 'benchmarks/current.py'; p.parent.mkdir(); p.write_bytes(b'ok')
            sha = hashlib.sha256(b'ok').hexdigest()
            self.assertEqual(exact_source('benchmarks/current.py', sha, root=root), b'ok')
            with self.assertRaises(AssertionError): exact_source('../outside', sha, root=root)
            p.unlink()
            with self.assertRaisesRegex(AssertionError, 'missing'): exact_source('benchmarks/current.py', sha, root=root)

    def test_missing_historical_source_is_explicit_not_fabricated_or_verified(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); bench=root/'benchmarks'; (bench/'config').mkdir(parents=True)
            profile={'id':'fixture','harness':'offline','files_sha256':{'benchmarks/missing.py':'a'*64}}
            profile['fingerprint']=hashlib.sha256(json.dumps(profile,sort_keys=True).encode()).hexdigest()
            task=bench/'runs/suite/conditions/old/model/case/task.json';task.parent.mkdir(parents=True)
            task.write_text(json.dumps({'environment_profile':profile}));output=bench/'config/archive.json'
            with patch.object(archive_profiles,'ROOT',root),patch.object(archive_profiles,'BENCH',bench),patch.object(archive_profiles,'OUTPUT',output):
                archive_profiles.main()
            item=json.loads(output.read_text())['profiles'][profile['fingerprint']]
            self.assertFalse(item['complete']);self.assertEqual(item['missing_files_sha256'],profile['files_sha256'])
            self.assertFalse((root/item['directory']/'benchmarks/missing.py').exists())


class ToolJSONTests(unittest.TestCase):
    def setUp(self): self.module = backend()

    def test_recorded_hex_value_and_quoted_strings(self):
        raw = '{"address": "0x40c000", "length": 0xa0}'
        self.assertEqual(json.loads(self.module.normalize_tool_hex(raw)), {'address':'0x40c000','length':160})
        self.assertEqual(json.loads(self.module.normalize_tool_hex('{"v":[0x10,-0Xf],"s":"0xaa \\\"quoted\\\""}')),
                         {'v':[16,-15],'s':'0xaa "quoted"'})

    def test_ambiguous_invalid_truncated_and_large_values_unchanged(self):
        for raw in ['{"x":1}', '{"x":"0xa0"}', '{"x":0xa0', '{"x":0xa0 "y":2}',
                    '{"x":0x20000000000001}', '{"x":0xa0,"y":NaN}', '[0xa0]', '{0xa0:1}', '{"x":1+0xa0}']:
            with self.subTest(raw=raw): self.assertIsNone(self.module.normalize_tool_hex(raw))

    def test_fragmented_sse_preserves_non_tool_bytes_and_private_evidence(self):
        original = '{"address": "0x40c000", "length": 0xa0}'
        start = frame({'type':'content_block_start','index':1,'content_block':{'type':'tool_use','id':'fixture','name':'read_bytes','input':{}}})
        delta = b''.join(frame({'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':p}}) for p in [original[:25],original[25:]])
        stop = frame({'type':'content_block_stop','index':1})
        non_tool = frame({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'文字 0xa0'}})
        with tempfile.TemporaryDirectory() as d:
            row={'sequence':12}; relay=self.module.ToolJSONRelay(Path(d),row)
            wire=non_tool+start+delta+stop; output=b''
            for byte in wire: output += relay.feed(bytes([byte]))
            relay.end()
            self.assertTrue(output.startswith(non_tool+start)); self.assertTrue(output.endswith(stop))
            events=[json.loads(line[5:]) for line in output.splitlines() if line.startswith(b'data:')]
            args=[e['delta']['partial_json'] for e in events if e.get('delta',{}).get('type')=='input_json_delta']
            self.assertEqual(json.loads(''.join(args))['length'],160)
            evidence=Path(d)/row['tool_json_normalizations'][0]['evidence_file']
            self.assertEqual(json.loads(evidence.read_text())['original'],original)
            self.assertEqual(evidence.stat().st_mode & 0o777,0o600)

    def test_valid_wire_is_byte_identical_and_incomplete_is_not_repaired(self):
        with tempfile.TemporaryDirectory() as d:
            relay=self.module.ToolJSONRelay(Path(d),{'sequence':1})
            wire=frame({'type':'content_block_start','index':0,'content_block':{'type':'tool_use'}})
            wire+=frame({'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':'{"v":16}'}})
            wire+=frame({'type':'content_block_stop','index':0})
            self.assertEqual(relay.feed(wire),wire); relay.end()
            self.assertEqual(list(Path(d).iterdir()),[])

    def test_installed_deepseek_sdk_replays_recorded_failure_and_accepts_normalization(self):
        raw = b''.join(frame(e) for e in [
            {'type':'message_start','message':{'usage':{'input_tokens':10,'output_tokens':0}}},
            {'type':'content_block_start','index':0,'content_block':{'type':'tool_use','id':'fixture','name':'mcp__rea__read_bytes','input':{}}},
            {'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':'{"address": "0x40c000", "length": 0xa0}'}},
            {'type':'content_block_stop','index':0},
            {'type':'message_delta','delta':{'stop_reason':'tool_use'},'usage':{'output_tokens':20}},
            {'type':'message_stop'}])
        script=r'''
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {DeepSeekAdapter,resolveAdapterOptions} from './.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/dsh-llm-deepseek/lib/index.js';
const connection=resolveAdapterOptions({baseURL:'https://fixture.invalid/anthropic',thinking:'enabled',reasoningEffort:'high',streamIdleTimeoutMs:3000,models:[{id:'deepseek-v4-pro'}]});
const adapter=new DeepSeekAdapter({options:()=>connection,resolveAuth:async()=>({headers:{}}),resolveUserId:()=> 'offline',resolveFiles:()=>({}),prepareExtensions:async()=>({fields:{},accept:async()=>{}})});
const options={provider:'deepseek',model:'deepseek-v4-pro',messages:[{role:'user',content:[{type:'text',text:'fixture'}]}]};
let calls=0;
globalThis.fetch=async()=>{calls++;return new Response(readFileSync(process.argv[1]),{headers:{'content-type':'text/event-stream'}});};
await assert.rejects(async()=>{for await(const c of adapter.stream(options)){}},/tool input is invalid JSON/);
globalThis.fetch=async()=>{calls++;return new Response(readFileSync(process.argv[2]),{headers:{'content-type':'text/event-stream'}});};
let blocks=[],usage,finish;
for await(const c of adapter.stream(options)){if(c.type==='block-end')blocks.push(c.block);if(c.type==='usage')usage=c.usage;if(c.type==='finish')finish=c;}
assert.deepEqual(JSON.parse(blocks[0].arguments),{address:'0x40c000',length:160});
assert.equal(usage.totalTokens,30);assert.equal(finish.reason.kind,'tool-calls');assert.equal(calls,2);
console.log('Installed DeepSeek SDK: original failure reproduced; lossless normalization accepted; zero network');
'''
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d);relay=self.module.ToolJSONRelay(directory,{'sequence':12})
            normalized=relay.feed(raw);relay.end()
            (directory/'original.sse').write_bytes(raw);(directory/'normalized.sse').write_bytes(normalized)
            child=subprocess.run([str(NODE),'--input-type=module','-e',script,str(directory/'original.sse'),str(directory/'normalized.sse')],
                cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
            self.assertEqual(child.returncode,0,child.stderr.decode()[-1800:])


class TransportTests(unittest.TestCase):
    def test_codex_and_deepseek_ignore_inherited_proxy_settings(self):
        module=backend()
        with patch.object(module.urllib.request,'getproxies',side_effect=AssertionError('Inherited proxy must not be consulted')):
            direct=module.direct_opener('/etc/ssl/certs/ca-certificates.crt')
            self.assertFalse(any(isinstance(h,urllib.request.ProxyHandler) for h in direct.handlers))
            with tempfile.TemporaryDirectory() as d, module.transport(d,task(),fixture_upstream=lambda *_: None) as proxy:
                self.assertFalse(any(isinstance(h,urllib.request.ProxyHandler) for h in proxy.opener.handlers))

    def test_connection_error_records_cause_without_secret_or_replay(self):
        module=backend(); calls=[]
        def fail(request,row):
            calls.append(1)
            raise urllib.error.URLError(ConnectionResetError(errno.ECONNRESET,'SECRET https://user:pass@host'))
        with tempfile.TemporaryDirectory() as d, module.transport(d,task(),fixture_upstream=fail) as proxy:
            with self.assertRaises(urllib.error.HTTPError): send(proxy,compress(body()))
            summary=proxy.gate.summary(); row=summary['requests'][0]
            self.assertEqual(row['transport_diagnostic']['cause'],'ConnectionResetError')
            self.assertEqual(row['transport_diagnostic']['errno'],errno.ECONNRESET)
            self.assertNotIn('SECRET',json.dumps(summary)); self.assertNotIn('pass@',json.dumps(summary))
            with self.assertRaises(urllib.error.HTTPError): send(proxy,compress(body(metadata={'next':True})))
            self.assertEqual(len(calls),1)


if __name__=='__main__': unittest.main()
