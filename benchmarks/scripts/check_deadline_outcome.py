"""Focused deadline classification and grading controls; no model APIs."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch
import request_contract as rc
import report_budget
from check_adaptive_budget import contract, begin, baseline
from check_wallet_budget import usage


def expired(model='deepseek-flash', *, inflight=False, error='wall_limit_reached', status=200):
    clock=[0.0];g=rc.Gate(contract(model),clock=lambda:clock[0])
    row=begin(g,1);usage(g,row,fresh=100,cached=100,output=10);g.finish(row,status=200)
    if inflight:
        row=begin(g,2);row['http_status']=status
        clock[0]=1801;g.finish(row,complete=False,error=error)
    else:clock[0]=1801
    return g


class DeadlineControls(unittest.TestCase):
    def test_both_models_deadline_between_requests_is_negative_completion(self):
        for model in ('deepseek-flash','glm-5.3'):
            g=expired(model);r=g.summary()
            self.assertEqual(r['status'],'deadline_exceeded')
            self.assertTrue(rc.grading_allowed(r,g.contract));self.assertFalse(r['queue_stop_required'])
            self.assertFalse(r['final_response_complete'])
            self.assertTrue(r['budget_usage_complete'])

    def test_wall_cancel_midstream_retains_incomplete_usage(self):
        for model in ('deepseek-flash','glm-5.3'):
            g=expired(model,inflight=True);r=g.summary()
            self.assertEqual(r['status'],'deadline_exceeded');self.assertFalse(r['budget_usage_complete'])
            self.assertFalse(r['transport_complete']);self.assertEqual(r['requests'][-1]['http_status'],200)
            self.assertTrue(rc.deadline_grade_allowed(r,g.contract))

    def test_api_auth_quota_disconnect_and_no_response_are_not_negative_grades(self):
        for model in ('deepseek-flash','glm-5.3'):
            for status,error in [(401,'provider_http_error'),(429,'provider_http_error'),(503,'provider_http_error'),
                                  (200,'provider_transport_interrupted'),(200,'provider_response_error'),
                                  (None,'wall_limit_reached')]:
                g=expired(model,inflight=True,error=error,status=status)
                self.assertNotEqual(g.summary()['status'],'deadline_exceeded',(model,status,error))
                self.assertFalse(rc.grading_allowed(g.summary(),g.contract))

    def test_no_api_early_manual_cancel_and_historical_contract_stay_ungraded(self):
        g=rc.Gate(contract());g.started-=1801
        self.assertNotEqual(g.summary()['status'],'deadline_exceeded')
        g=expired(inflight=True);g.clock=lambda:10
        self.assertNotEqual(g.summary()['status'],'deadline_exceeded')
        g=expired();del g.contract['deadline_outcome']
        self.assertNotEqual(g.summary()['status'],'deadline_exceeded')

    def test_invalid_receipts_do_not_certify_deadlines(self):
        g=expired();r=g.summary()
        for key,value in [('deadline_elapsed_seconds',float('inf')),('deadline_elapsed_seconds',1799),
                          ('request_ledger_complete',False),('final_response_complete',True),
                          ('requests',[None]),('contract_sha256','incorrect')]:
            self.assertFalse(rc.deadline_grade_allowed({**r,key:value},g.contract),key)
        broken=copy.deepcopy(r);broken['requests'][0]['budget_violation']='malformed_output'
        self.assertFalse(rc.deadline_grade_allowed(broken,g.contract))

    def test_real_both_loopback_transports_label_deadline_only_after_http_200(self):
        from check_stream_handoff import proxy,HeldEOF
        from check_requests import payload
        for kind,model,ending in [(k,m,e) for k,m in [('dsh','deepseek-flash'),('glm','glm-5.3')] for e in ('timeout','eof')]:
            release=threading.Event();release.set();clock=[0]
            with proxy(kind,release,wallet=True,budget_context=baseline(model)) as (server,url,calls):
                server.gate.clock=lambda:clock[0];server.gate.started=0
                def stall(_self,_size=-1):
                    clock[0]=server.gate.contract['wall_seconds']+1
                    if ending=='eof':return b''
                    raise TimeoutError('Local deadline in simulated upstream stream')
                value=payload(server.gate.contract['protocol'],maximum=65536);value['model']=model
                with patch.object(HeldEOF,'read1',stall):
                    client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    try:
                        with client.open(urllib.request.Request(url,data=json.dumps(value).encode(),headers={'Content-Type':'application/json'}),timeout=5) as response:response.read()
                    except (OSError, __import__('http.client',fromlist=['IncompleteRead']).IncompleteRead):pass
                # Shutdown waits for the request handler to settle before inspecting.
                server.shutdown()
                receipt=server.gate.summary()
                self.assertEqual(len(calls),1);self.assertEqual(receipt['provider_forwards'],0)
                self.assertEqual(receipt['status'],'deadline_exceeded',receipt['requests'])
                self.assertTrue(rc.deadline_grade_allowed(receipt,server.gate.contract))

    def test_partial_valid_native_code_cannot_gain_correctness(self):
        from grading import store
        with tempfile.TemporaryDirectory() as temporary:
            d=Path(temporary);g=expired()
            (d/'answer.md').write_text('```cpp\nint reconstructed(){return 0;}\n```\n')
            (d/'reconstructed.cpp').write_text('int reconstructed(){return 0;}')
            (d/'request-budget.json').write_text(json.dumps(g.summary()))
            self.assertEqual(store.source(d,'cpp'),(None,None))
            r=g.summary();r['deadline_elapsed_seconds']=1
            (d/'request-budget.json').write_text(json.dumps(r))
            with self.assertRaisesRegex(ValueError,'Invalid deadline'):store.source(d,'cpp')

    def test_typed_deadline_preserves_answer_but_never_captures_artifacts(self):
        from capabilities import store
        with tempfile.TemporaryDirectory() as temporary:
            d=Path(temporary);g=expired();answer=b'complete-looking candidate'
            (d/'answer.md').write_bytes(answer);(d/'request-budget.json').write_text(json.dumps(g.summary()))
            with patch.object(store,'fixed_inputs',return_value=({'kind':'layout'}, {}, {})), \
                 patch.object(store,'extract',side_effect=AssertionError('Partial answer must not be captured')):
                action,saved,_=store.prepare(d,{'request_contract':g.contract})
            self.assertEqual(saved,answer);self.assertIn(b'invalid-artifact-set',action.blobs.values())
            self.assertIn((d/'request-budget.json').read_bytes(),action.blobs.values())

    def test_both_dispatchers_return_completed_deadline_without_failure_marker(self):
        import run
        from types import SimpleNamespace
        from contextlib import contextmanager
        module=run.load_module(Path(rc.ROOT)/'benchmarks/harnesses/dispatch.py','deadline_dispatch_control')
        for harness,model in [('deepseek-harness','deepseek-flash'),('zcode','glm-5.3')]:
            g=expired(model,inflight=True);receipt=g.summary()
            with tempfile.TemporaryDirectory() as temporary:
                d=Path(temporary);(d/'request-budget.json').write_text(json.dumps(receipt))
                raw=dict(timed_out=True,exit_code=143,request_budget=receipt,requests=receipt['requests'],
                         usage_observations_raw=[],policy_violations=[],upstream_error=None)
                (d/'harness-result.json').write_text(json.dumps(raw))
                @contextmanager
                def transport(*args,**kwargs):
                    yield SimpleNamespace(base_url='http://offline.invalid',deadline_ms=1800000,gate=g)
                backend=SimpleNamespace(transport=transport,launch=lambda *a,**kw:raw,
                                        collect_wire=lambda _d:('',[]))
                task=dict(model=model,request_contract=g.contract,runtime_attestation={'provider':{'provider':'offline'},'identity':'fixture'},
                    environment_profile='fixture',condition='lean-evidence-v4',language='cpp',id='fixture',binary_sha256='0'*64,prompt_sha256='1'*64)
                records=SimpleNamespace(normalize_glm_usage=lambda _:({},[]),read_trace=lambda _:[])
                with patch.object(module,'load_module',return_value=backend), \
                     patch.object(module.runtime_attestation,'glm_credentials',return_value=('offline','dummy')), \
                     patch.object(module.usage_accounting,'derive',side_effect=lambda x:(x,{})), \
                     patch.object(module,'_launch_dsh',return_value=None):
                    module._launch(d,task,{'id':model,'harness':harness,'label':model,'reasoning':'high'},records=records)
                self.assertEqual(json.loads((d/'deadline-exceeded.json').read_text())['classification'],'completed_not_passed')
                self.assertFalse((d/'harness-failure.json').exists());self.assertFalse((d/'budget-exhausted.json').exists())
                self.assertEqual(json.loads((d/'result.json').read_text())['exit_code'],143)

    def test_public_outcome_discloses_zero_grade_and_partial_usage(self):
        g=expired(inflight=True);r=g.summary();r['provider_forwards']=2 # synthetic public projection only
        value=report_budget.summarize({'request_contract':g.contract},r)
        self.assertEqual(report_budget.project(value),value)
        self.assertIn('completed, not passed',report_budget.description(value))
        text=report_budget.detail(value)
        self.assertIn('Correctness is zero',text);self.assertIn('Usage may be incomplete',text)
        self.assertNotIn('A complete final candidate may retain',text)
        invalid=copy.deepcopy(r);invalid['requests'][0]['http_status']=429
        self.assertIsNone(report_budget.outcome(invalid,g.contract))

if __name__=='__main__':unittest.main(verbosity=2)
