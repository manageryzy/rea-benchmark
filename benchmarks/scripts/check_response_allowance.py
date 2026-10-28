"""Focused response-allocation checks; loopback fixtures, no model requests."""
import copy
import hashlib
import json
import threading
import subprocess
import unittest
import urllib.request
import urllib.error

import request_contract as rc
from check_requests import payload
from check_stream_handoff import proxy
from check_wallet_budget import usage


def contract(model):
    ds = model == 'deepseek-flash'
    return rc.build(dict(provider='deepseek-official' if ds else 'bigmodel-standard-api',
        protocol='anthropic-messages' if ds else 'openai-chat-completions',
        base_url='https://api.deepseek.com/anthropic' if ds else 'https://open.bigmodel.cn/api/paas/v4'),
        {'id': model}, {'max_tool_calls':60}, dict(max_output_tokens=128000,
        timeout_seconds=1800, tool_timeout_ms=60000, evidence_paging={'id':'lean-evidence-v4'}))


def body(policy, turn, cap=32768):
    value = payload(policy['protocol'], maximum=cap)
    value.update(model=policy['model'], messages=[{'role':'user','content':f'Offline turn {turn}'}])
    return json.dumps(value).encode()


class ResponseAllowanceControls(unittest.TestCase):
    def test_wallet_routes_get_larger_cap_without_raising_task_or_wallet_limits(self):
        expected={'deepseek-flash':41611514, 'glm-5.3':1236720000}
        for model, cost in expected.items():
            p=contract(model)
            self.assertEqual(p['output_ceiling'],32768)
            self.assertEqual(p['task_limits']['max_response_tokens'],32768)
            self.assertEqual(p['task_limits']['max_output_tokens'],32768)
            self.assertEqual(p['max_requests'],12)
            self.assertEqual(p['max_retries'],0)
            self.assertEqual(p['wallet']['limit_nano'],cost)

    def test_each_proxy_forwards_the_actual_remaining_cap_and_records_both_hashes(self):
        for kind in ('dsh','glm'):
            with self.subTest(kind=kind):
                release=threading.Event();release.set()
                with proxy(kind,release,wallet=True,maximum=32768) as (server,url,calls):
                    client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    raws=[]
                    for turn in (1,2):
                        raw=body(server.gate.contract,turn);raws.append(raw)
                        with client.open(urllib.request.Request(url,data=raw,
                                headers={'Content-Type':'application/json'}),timeout=5) as response:
                            response.read()
                    self.assertEqual(len(calls),2)
                    first,second=map(lambda x:json.loads(x.data),calls)
                    self.assertEqual(first['max_tokens'],32768)
                    self.assertEqual(second['max_tokens'],32763)
                    row=server.gate.requests[1]
                    self.assertEqual(row['requested_cap'],32768)
                    self.assertEqual(row['cap'],32763)
                    self.assertEqual(row['original_request_sha256'],hashlib.sha256(raws[1]).hexdigest())
                    self.assertEqual(row['request_sha256'],hashlib.sha256(calls[1].data).hexdigest())
                    self.assertEqual(server.gate.summary()['task_usage']['output_tokens_charged_or_reserved'],10)

    def test_last_token_is_usable_and_zero_allowance_stops_before_forward(self):
        for model in ('deepseek-flash','glm-5.3'):
            g=rc.Gate(contract(model))
            row,_=g.begin(g.contract['path'],body(g.contract,1));g.forward(row,fixture=True)
            usage(g,row,fresh=0,cached=0,output=32767);g.finish(row,status=200)
            row,request=g.begin(g.contract['path'],body(g.contract,2))
            self.assertEqual(request['max_tokens'],1)
            g.forward(row,fixture=True);usage(g,row,fresh=0,cached=0,output=1);g.finish(row,status=200)
            with self.assertRaisesRegex(rc.Violation,'task_output_budget_reached'):
                g.begin(g.contract['path'],body(g.contract,3))
            self.assertEqual(g.summary()['validated_forwards'],2)
            self.assertEqual(g.task_usage()['output_tokens_charged_or_reserved'],32768)

    def test_bad_client_cap_is_not_repaired_and_retries_remain_denied(self):
        g=rc.Gate(contract('deepseek-flash'))
        with self.assertRaisesRegex(rc.Violation,'invalid_output_cap'):
            g.begin(g.contract['path'],body(g.contract,1,32769))
        g=rc.Gate(contract('deepseek-flash'))
        first,_=g.begin(g.contract['path'],body(g.contract,1));g.forward(first,fixture=True)
        usage(g,first,output=5);g.finish(first,status=200)
        raw=body(g.contract,2);second,_=g.begin(g.contract['path'],raw);g.forward(second,fixture=True)
        usage(g,second,output=5);g.finish(second,status=200)
        with self.assertRaisesRegex(rc.Violation,'duplicate_request_retry_denied'):
            g.begin(g.contract['path'],raw)

    def test_provider_exceeding_reduced_cap_is_still_rejected(self):
        g=rc.Gate(contract('glm-5.3'));row,_=g.begin(g.contract['path'],body(g.contract,1));g.forward(row,fixture=True)
        usage(g,row,fresh=0,cached=0,output=32767);g.finish(row,status=200)
        row,_=g.begin(g.contract['path'],body(g.contract,2));g.forward(row,fixture=True)
        with self.assertRaisesRegex(rc.Violation,'reported_output_exceeds_cap'):
            usage(g,row,output=2)

    def test_unsupported_wallet_compression_remains_rejected_before_forward(self):
        from check_requests import NODE
        release=threading.Event();release.set()
        with proxy('dsh',release,wallet=True,maximum=32768) as (server,url,calls):
            client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            compressed=subprocess.run([str(NODE),'-e',
                "process.stdout.write(require('node:zlib').zstdCompressSync(require('node:fs').readFileSync(0)))"],
                input=body(server.gate.contract,1),stdout=subprocess.PIPE,check=True,timeout=10).stdout
            with self.assertRaises(urllib.error.HTTPError) as error:
                client.open(urllib.request.Request(url,data=compressed,
                    headers={'Content-Type':'application/json','Content-Encoding':'zstd'}),timeout=5)
            with error.exception as response:
                self.assertIn(b'unsupported_request_content_encoding',response.read())
            self.assertEqual(calls,[])

    def test_public_report_distinguishes_larger_and_remaining_allowance(self):
        import public_report,report,report_budget
        g=rc.Gate(contract('glm-5.3'));row,_=g.begin(g.contract['path'],body(g.contract,1));g.forward(row,fixture=True)
        usage(g,row,output=100);row['final_response']=True;g.finish(row,status=200)
        value=report_budget.summarize({'request_contract':g.contract},g.summary())
        self.assertEqual(value['per_response_output_tokens'],32768)
        self.assertEqual(value['response_allocation'],'remaining_task_output_v1')
        self.assertEqual(report_budget.project(value),value)
        saved=json.loads(public_report.SNAPSHOT.read_bytes())
        saved['records'][0]['budget_condition']=value
        saved['task_budget_policy']['wallets']=[{k:g.contract['wallet'][k] for k in
            ('model','currency','limit_nano','semantics','max_response_tokens')}]
        public_report.validate(saved)
        view=report.Report.__new__(report.Report);view.data=saved
        self.assertIn('32,768 tokens, reduced to remaining',view.task_budget_section())
        self.assertIn('without increasing the cumulative output limit',report_budget.detail(value))

    def test_legacy_condition_keeps_original_response_cap_and_reservation(self):
        from check_requests import task
        p=task('anthropic-messages',maximum=128000)['request_contract']
        self.assertEqual(p['output_ceiling'],8192)
        self.assertNotIn('response_allocation',p)


if __name__ == '__main__': unittest.main()
