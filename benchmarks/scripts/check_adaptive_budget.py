"""Focused adaptive policy controls: synthetic ledgers and loopback, zero APIs."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import nullcontext
from types import SimpleNamespace
import urllib.request

import adaptive_budget as ab
import request_contract as rc
from check_requests import payload
from check_wallet_budget import usage


def baseline(model='deepseek-flash', *, basic=False, samples=()):
    grouping = ab.kind('record-parser' if basic else 'capabilities-v1',
                       dict(kind='matched_buffer', source_language='cpp', output_language='c'))
    value = dict(schema_version=1, policy_id='type-mean2x-v1', model=model,
                 mode='basic_turn_only' if basic else 'type_mean', type=grouping, samples=list(samples))
    value['sha256'] = hashlib.sha256(ab.canonical(value)).hexdigest()
    return value


def contract(model='deepseek-flash', *, basic=False, samples=(), frozen=None):
    ds, gpt = model == 'deepseek-flash', model.startswith('gpt')
    provider = dict(provider='openai-codex' if gpt else 'deepseek-official' if ds else 'bigmodel-standard-api',
        protocol='openai-codex-responses' if gpt else 'anthropic-messages' if ds else 'openai-chat-completions',
        base_url='https://frozen.invalid/backend-api' if gpt else 'https://api.deepseek.com/anthropic' if ds else 'https://open.bigmodel.cn/api/paas/v4')
    return rc.build(provider, {'id':model}, {'max_tool_calls':60}, dict(max_output_tokens=8192,
        timeout_seconds=1800, tool_timeout_ms=60000, evidence_paging={'id':'lean-evidence-v4'},
        budget_context=frozen or baseline(model, basic=basic, samples=samples)))


def begin(g, turn):
    body = payload(g.contract['protocol'], maximum=65536)
    body['model'] = g.contract['model']
    body['input' if 'input' in body else 'messages'][0]['content'] = f'Offline turn {turn}'
    row, _ = g.begin(g.contract['path'], json.dumps(body).encode())
    g.forward(row, fixture=True)
    return row


def sample(model='deepseek-flash', *, case='first', fresh=100, cached=1000, output=100):
    return dict(case_id=case, type=baseline(model)['type'],
        provider='openai-codex' if model.startswith('gpt') else 'deepseek-official' if model == 'deepseek-flash' else 'bigmodel-standard-api',
        tokens=dict(inputTokens=fresh, cacheReadTokens=cached, cacheWriteTokens=0, outputTokens=output,
                    totalTokens=fresh+cached+output))


class AdaptiveControls(unittest.TestCase):
    def test_basic_has_64k_and_no_turn_or_hidden_request_ceiling(self):
        for model in ('deepseek-flash','glm-5.3'):
            g=rc.Gate(contract(model,basic=True))
            self.assertEqual(g.contract['output_ceiling'],65536)
            self.assertNotIn('wallet',g.contract)
            for field in ('max_output_tokens','max_observed_total_tokens','max_model_turns'):
                self.assertIsNone(g.contract['task_limits'][field])
            self.assertIsNone(g.contract['max_requests'])
            for turn in range(70):
                row=begin(g,turn);usage(g,row,fresh=500000,cached=1000000,output=60000);g.finish(row,status=200)
            self.assertEqual(g.summary()['validated_forwards'],70)
            self.assertIsNone(g.summary()['stop_reason'])

    def test_bootstrap_has_no_aggregate_but_keeps_response_guard(self):
        for model in ('deepseek-flash','glm-5.3'):
            g=rc.Gate(contract(model));self.assertIsNone(g.contract['wallet']['limit_nano'])
            row=begin(g,1);usage(g,row,fresh=1000000,output=60000);g.finish(row,status=200)
            self.assertEqual(g.summary()['status'],'wire_enforced')
            row=begin(g,2)
            with self.assertRaisesRegex(rc.Violation,'reported_output_exceeds_cap'):usage(g,row,output=65537)

    def test_typed_bootstrap_can_pass_twelve_turns_while_time_guard_remains(self):
        for model in ('deepseek-flash','glm-5.3','gpt-6.1-sol'):
            clock=[0]
            g=rc.Gate(contract(model),clock=lambda:clock[0])
            self.assertIsNone(g.contract['max_requests'])
            self.assertIsNone(g.contract['task_limits']['max_model_turns'])
            for n in range(14):
                row=begin(g,n)
                if model.startswith('gpt'):
                    g.observe(row,dict(input_tokens=100,input_tokens_details={'cached_tokens':50},output_tokens=10,total_tokens=110))
                else:usage(g,row,fresh=50,cached=50,output=10)
                g.finish(row,status=200)
            clock[0]=g.contract['wall_seconds']+1
            with self.assertRaisesRegex(rc.Violation,'wall_limit_reached'):begin(g,14)
            self.assertEqual(g.summary()['validated_forwards'],14)

    def test_double_mean_component_pricing_stops_next_request_only(self):
        for model in ('deepseek-flash','glm-5.3'):
            samples=[sample(model),sample(model,case='second',fresh=101,output=103)]
            g=rc.Gate(contract(model,samples=samples));wallet=g.contract['wallet']
            expected=sum(s['tokens'][k]*r for s in samples for k,r in wallet['rates_nano_per_token'].items())
            self.assertEqual(wallet['limit_nano'],expected)
            self.assertEqual(g.contract['adaptive_budget']['sample_count'],2)
            row=begin(g,1);usage(g,row,fresh=10000,cached=0,output=1000);g.finish(row,status=200)
            with self.assertRaisesRegex(rc.Violation,'task_wallet_limit_reached'):begin(g,2)
            self.assertEqual(g.summary()['validated_forwards'],1)

    def test_complete_over_budget_keeps_separate_correctness_admission(self):
        g=rc.Gate(contract(samples=[sample()]));row=begin(g,1)
        usage(g,row,fresh=10000,cached=0,output=1000);row['final_response']=True;g.finish(row,status=200)
        self.assertEqual(g.summary()['status'],'completed_over_budget')
        self.assertTrue(rc.grading_allowed(g.summary(),g.contract))

    def test_bootstrap_missing_wallet_usage_cannot_be_treated_as_free(self):
        g=rc.Gate(contract());row=begin(g,1);usage(g,row,complete=False);g.finish(row,status=200)
        with self.assertRaisesRegex(rc.Violation,'wallet_usage_unavailable'):begin(g,2)

    def test_codex_keeps_no_wire_cap_and_observed_64k(self):
        model='gpt-6.1-sol'
        g=rc.Gate(contract(model,samples=[sample(model)]))
        self.assertIsNone(g.contract['output_ceiling'])
        self.assertEqual(g.contract['task_limits']['max_observed_total_tokens'],2400)
        self.assertEqual(g.contract['observed_response_ceiling'],65536)
        g=rc.Gate(contract(model,basic=True));row=begin(g,1)
        g.observe(row,dict(input_tokens=100, input_tokens_details={'cached_tokens':50},output_tokens=65537,total_tokens=65637))
        g.finish(row,status=200)
        with self.assertRaisesRegex(rc.Violation,'response_observed_output_limit_reached'):begin(g,2)
        self.assertFalse(g.summary()['provider_output_cap_enforced'])

    def test_mutated_baseline_and_mixed_types_are_rejected(self):
        b=baseline();b['model']='changed'
        with self.assertRaises(rc.Violation):contract(frozen=b)
        s=sample();s['type']['output_language']='rust'
        with self.assertRaises(rc.Violation):contract(samples=[s])
        with self.assertRaises(rc.Violation):contract(samples=[sample(),sample()])

    def test_model_provider_and_codegen_groups_are_separate(self):
        g=contract(samples=[sample('glm-5.3')]);self.assertEqual(g['adaptive_budget']['sample_count'],0)
        case=dict(kind='matched_buffer',source_language='cpp',output_language='c')
        self.assertNotEqual(ab.kind('capabilities-v1',case),ab.kind('capabilities-v1',{**case,'profile_id':'cpp_codegen'}))

    def test_both_production_proxies_pass_twelve_turns_with_65536_despite_legacy_case_8192(self):
        from check_stream_handoff import proxy
        for kind,model in [('dsh','deepseek-flash'),('glm','glm-5.3')]:
            release=threading.Event();release.set()
            with proxy(kind,release,wallet=True,maximum=8192,budget_context=baseline(model)) as (server,url,calls):
                client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for turn in range(1,15):
                    b=payload(server.gate.contract['protocol'],maximum=65536)
                    b.update(model=model,messages=[{'role':'user','content':f'Offline {turn}'}])
                    with client.open(urllib.request.Request(url,data=json.dumps(b).encode(),headers={'Content-Type':'application/json'}),timeout=5) as r:r.read()
                self.assertEqual([json.loads(c.data)['max_tokens'] for c in calls],[65536]*14)
                self.assertEqual(server.gate.summary()['provider_forwards'],0)

    def test_public_projection_and_old_snapshots(self):
        import report_budget,public_report,report
        for model,basic,samples in [('deepseek-flash',False,[]),('glm-5.3',False,[sample('glm-5.3')]),('glm-5.3',True,[]),('gpt-6.1-sol',False,[])]:
            c=contract(model,basic=basic,samples=samples)
            value=report_budget.summarize({'request_contract':c},{})
            self.assertEqual(report_budget.project(value),value)
            self.assertIn('first case',report_budget.detail(value))
            saved=json.loads(public_report.SNAPSHOT.read_bytes());saved['records'][0]['budget_condition']=value
            saved['task_budget_policy']['adaptive_budget']=json.loads(rc.POLICY.read_bytes())['adaptive_budget']
            public_report.validate(saved)
            view=report.Report.__new__(report.Report);view.data=saved
            self.assertIn('65,536',view.task_budget_section())
            saved['records'][0]['budget_condition']['adaptive']['samples']=['forbidden raw baseline']
            with self.assertRaises(ValueError):public_report.validate(saved)

    def test_actual_environment_profile_covers_every_declared_inference_input(self):
        import action_cache, environment_profile
        for harness in ('deepseek-harness','zcode'):
            profile=environment_profile.current(harness,'lean-evidence-v4')
            for name in action_cache.dependencies('inference',harness=harness,condition='lean-evidence-v4'):
                self.assertEqual(profile['files_sha256'].get(name),hashlib.sha256((ab.ROOT/name).read_bytes()).hexdigest(),name)

    def test_worker_returns_settled_directory_for_next_workers_baseline(self):
        import parallel_cells, run
        entry = {'directory':'benchmarks/runs/fixture/model/case','case':'case'}
        state = {'cancel':object(), 'recorder':SimpleNamespace(span=lambda _:nullcontext())}
        with patch.object(parallel_cells,'_STATE',state), patch.object(run,'_execution_allowed'), \
                patch.object(run,'prepare_next_cell',return_value=entry), \
                patch.object(run,'execute',return_value={'status':'inferred'}):
            result = parallel_cells.execute(entry,{'id':'model'},None,True)
        self.assertEqual(result['directory'],entry['directory'])

    def test_history_filters_updates_and_freezes_per_case(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'benchmarks/config').mkdir(parents=True);(root/'benchmarks/runs').mkdir()
            (root/'benchmarks/config/request-contract.json').write_bytes(rc.POLICY.read_bytes())
            case=dict(id='next',kind='matched_buffer',source_language='cpp',output_language='c',profile_id='recover')
            def save(name,when,*,complete=True,provider='deepseek-official'):
                directory=root/'benchmarks/runs/capabilities-v1/deepseek-flash'/name/'public-digest'/when
                directory.mkdir(parents=True)
                g=rc.Gate(contract());row=begin(g,1);usage(g,row,fresh=100,cached=1000,output=100)
                row['upstream_kind']='provider' # Synthetic saved-ledger fixture; no upstream opened.
                row['final_response']=complete;g.finish(row,status=200)
                (directory/'task.json').write_text(json.dumps({**case,'id':name,'track':'capability','request_contract':g.contract}))
                (directory/'result.json').write_text(json.dumps(dict(model='deepseek-flash',condition='lean-evidence-v4',provider=provider,
                    started_at=when,usage_complete=True,request_budget=g.summary())))
                return directory
            save('first','2026-01-01');save('first','2026-01-02');save('incomplete','2026-01-03',complete=False)
            with patch.dict('os.environ', {'PATH': ''}):
                b=ab.context({'id':'deepseek-flash'},'capabilities-v1',case,'lean-evidence-v4',root=root)
            self.assertEqual(len(b['samples']),1);self.assertEqual(b['samples'][0]['started_at'],'2026-01-02')
            directory=save('last','2026-01-04')
            self.assertEqual(ab.context({'id':'deepseek-flash'},'capabilities-v1',case,'lean-evidence-v4',root=root),b)
            next_b=ab.context({'id':'deepseek-flash'},'capabilities-v1',{**case,'id':'later'},'lean-evidence-v4',root=root,completed=[str(directory.relative_to(root))])
            self.assertEqual(len(next_b['samples']),2)


if __name__=='__main__':unittest.main()
