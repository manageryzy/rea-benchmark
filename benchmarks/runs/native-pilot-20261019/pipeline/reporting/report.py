#!/usr/bin/python3
"""Report native reconstruction accuracy, provider usage and estimated API costs."""
import argparse,csv,datetime,hashlib,json,pathlib
from pricing import estimate,display
BENCH=pathlib.Path(__file__).resolve().parents[1]
TOKEN_KEYS={'input_tokens':'inputTokens','cache_read_tokens':'cacheReadTokens',
            'cache_write_tokens':'cacheWriteTokens','output_tokens':'outputTokens',
            'reasoning_tokens':'reasoningTokens','total_tokens':'totalTokens'}

def read(path,default=None):
 return json.loads(path.read_text()) if path.exists() else default

def summarize(rows):
 summaries=[]
 for model in ['deepseek-flash','gpt-6.1-sol']:
  group=[r for r in rows if r['model']==model]
  if not group:continue
  summary={'model':model,'attempts':len(group),'completed':sum(r['status']=='completed' for r in group),
           'compiled':sum(r['compiled'] for r in group),'passed':sum(r['passed'] for r in group),
           'tests':sum(r['tests'] for r in group),'tool_calls':sum(r['tool_calls'] for r in group),
           'active_task_seconds':sum(r['active_task_seconds'] or 0 for r in group),
           'actual_billed_usd':None}
  for key in TOKEN_KEYS:
   summary[key]=sum(r[key] or 0 for r in group) if any(r[key] is not None for r in group) else None
  for key in ['estimated_api_low_usd','estimated_api_high_usd']:
   summary[key]=sum(r[key] for r in group) if all(r[key] is not None for r in group) else None
  summaries.append(summary)
 return summaries

def collect(base,prices):
 rows=[]
 for path in base.glob('*/*/result.json'):
  d=read(path);g=read(path.parent/'grade.json',{});review=read(path.parent/'evidence-review.json',{})
  cost=estimate(d,prices)
  row={key:d[key] for key in ['model','language','provider','reasoning','tool_calls','tool_errors']}
  row.update(output_language=d.get('output_language','c'),
             status='completed' if d['exit_code']==0 and d['final_present'] else 'infrastructure_or_run_error',
             compiled=g.get('compiled',False),passed=g.get('passed',0),tests=g.get('total',0),pass_rate=g.get('pass_rate',0),
             wall_seconds=round(d['elapsed_seconds'],2),harness_startup_seconds=d.get('harness_startup_seconds'),
             active_task_seconds=d.get('active_task_seconds'),
             first_native_query_seconds=None if d['first_native_query_ms'] is None else d['first_native_query_ms']/1000,
             estimated_api_low_usd=cost['low_usd'],estimated_api_high_usd=cost['high_usd'],
             actual_billed_usd=None,billing_mode=cost.get('billing_mode','Unavailable'),
             evidence_review=review.get('verdict','pending manual review'),
             failure=g.get('failure') or (str(d.get('turn_end')) if d['exit_code'] else ''))
  row.update({key:d['reported_usage'].get(value) for key,value in TOKEN_KEYS.items()})
  rows.append(row)
 return sorted(rows,key=lambda r:(r['model']!='deepseek-flash',{'cpp':0,'rust':1,'go':2}.get(r['language'],3)))

def main():
 p=argparse.ArgumentParser();p.add_argument('run_id');a=p.parse_args()
 base=BENCH/'runs'/a.run_id;manifest=read(base/'run-manifest.json')
 if manifest is None:raise SystemExit('Run manifest does not exist')
 config=manifest['configuration'];pricing_name=config.get('pricing_file','pricing-20261019.json')
 pricing_path=base/'pipeline/config'/pricing_name
 if not pricing_path.exists():pricing_path=BENCH/'config'/pricing_name
 prices=read(pricing_path);rows=collect(base,prices);summaries=summarize(rows)
 out=BENCH/'reports'/a.run_id;out.mkdir(parents=True,exist_ok=True)
 now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat()
 report={'generated_at_jst':now,'run_id':a.run_id,'output_mode':config.get('output_mode','C reconstruction'),
         'runs':rows,'models':summaries,'expected_runs':len(config['run_order']),
         'completed_runs':sum(r['status']=='completed' for r in rows),
         'runtime':{name:read(BENCH.parent/'environment-lock.json')[name]['version'] for name in ['harness','rea','ghidra']},
         'auth_modes':manifest['auth_modes'],'pricing_snapshot':prices,
         'pricing_sha256':hashlib.sha256(pricing_path.read_bytes()).hexdigest(),
         'scope':'One fixture per language and one first attempt per model/case; this pilot cannot establish a general model ranking.',
         'timing_note':'Active task time excludes Harness startup and includes Ghidra analysis, model inference, tools and cleanup; it is not pure model inference time.',
         'usage_note':'Totals include cached input. Reasoning and cache-write counts are unavailable when the provider omits them; missing values are not evidence of zero.',
         'cost_note':'Costs are estimated API equivalents in USD, not observed invoice debits. GPT uses Codex plan/credits. Its range covers unknown cache writes. Excludes setup, this conversation, and local compute.'}
 baseline_id=config.get('baseline_run_id')
 if baseline_id:
  old=collect(BENCH/'runs'/baseline_id,prices);report['C_baseline']={'run_id':baseline_id,'models':summarize(old),'rates':'Repriced using the same verified pricing snapshot.'}
  baseline_audit=read(BENCH/'reports'/baseline_id/'protocol-audit.json',{})
  provenance=read(base/'input-provenance.json',{})
  same_vectors=baseline_audit.get('vectors_sha256')==provenance.get('vectors_sha256')
  same_binaries=all(read(BENCH/'runs'/baseline_id/r['model']/r['language']/'result.json')['binary_sha256']==read(base/r['model']/r['language']/'result.json')['binary_sha256'] for r in rows)
  report['C_baseline']['same_binary_hashes']=same_binaries;report['C_baseline']['same_vectors']=same_vectors
 for name,data in [('comparison',rows),('model-totals',summaries)]:
  if data:
   with (out/(name+'.csv')).open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
 (out/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
 lines=[f'# Original-language reconstruction pilot: {a.run_id}',f'\nGenerated {now} (JST).\n',
        f"Completed runs: {report['completed_runs']}/{report['expected_runs']}. Output mode: {report['output_mode']}.\n",
        f"Harness {report['runtime']['harness']}; REA {report['runtime']['rea']}; Ghidra {report['runtime']['ghidra']}; high reasoning effort.\n",
        '| Model | Original/output language | Compiled | Correct | Total tokens | Estimated API equivalent |',
        '|---|---|---|---:|---:|---:|']
 for r in rows:
  lines.append(f"| {r['model']} | {r['language']} / {r['output_language']} | {r['compiled']} | {r['passed']}/{r['tests']} | {r['total_tokens']} | {display(r['estimated_api_low_usd'],r['estimated_api_high_usd'])} |")
 lines+=['\nTotals across the three languages:\n','| Model | Correct | Fresh input | Cached input | Output | Total tokens | Estimated API equivalent |','|---|---:|---:|---:|---:|---:|---:|']
 for r in summaries:
  lines.append(f"| {r['model']} | {r['passed']}/{r['tests']} | {r['input_tokens']} | {r['cache_read_tokens']} | {r['output_tokens']} | {r['total_tokens']} | {display(r['estimated_api_low_usd'],r['estimated_api_high_usd'])} |")
 if baseline_id:
  lines+=['\nComparison with the earlier C-output pilot (same rates):\n','| Model | C correctness | Native correctness | C tokens | Native tokens | C estimated API | Native estimated API |','|---|---:|---:|---:|---:|---:|---:|']
  for r in summaries:
   old=next(x for x in report['C_baseline']['models'] if x['model']==r['model'])
   lines.append(f"| {r['model']} | {old['passed']}/{old['tests']} | {r['passed']}/{r['tests']} | {old['total_tokens']} | {r['total_tokens']} | {display(old['estimated_api_low_usd'],old['estimated_api_high_usd'])} | {display(r['estimated_api_low_usd'],r['estimated_api_high_usd'])} |")
  lines.append(f"\nBinary hashes match: {same_binaries}. Hidden vector hash matches: {same_vectors}. The output-language instructions differ between cohorts.")
 lines+=['\n'+report['cost_note'],'\n[DeepSeek pricing]('+prices['deepseek-flash']['source']+') · [GPT-6.1 Sol pricing]('+prices['gpt-6.1-sol']['source']+'). Pricing snapshot: '+prices['verified_at_utc']+'.',
         '\n'+report['usage_note'],'\n'+report['timing_note'],'\n'+report['scope'],
         '\nModels received only the selected ELF and eight analysis tools. Candidate compilation and hidden-test execution occurred after the final answer, without feedback to the model. Native compilation verifies language compatibility; behavior tests do not prove exact recovery of the original source.\n',
         'The full visible assistant response is graded; the terminal post-close acknowledgment is retained separately. The answer-capture amendment preserves original event streams, performs no model retry, and rejects multiple code blocks.\n','Run artifacts and evidence reviews:\n']
 for r in rows:
  prefix=f"../../runs/{a.run_id}/{r['model']}/{r['language']}"
  extension={"cpp":"cpp","rust":"rs","go":"go","c":"c"}[r["output_language"]]
  source=f", [native source]({prefix}/reconstructed.{extension})" if (base/r["model"]/r["language"]/f"reconstructed.{extension}").exists() else ""
  lines.append(f"- {r['model']} / {r['language']}: [answer]({prefix}/answer.md){source}, [grading]({prefix}/grade.json), [evidence review]({prefix}/evidence-review.json) ({r['evidence_review'].replace('_',' ')}).")
 if (out/'protocol-audit.json').exists():lines+=['\n[Protocol audit](protocol-audit.json): first attempts, fresh sessions, matching inputs, tool isolation, session closure, grading and credential cleanup.']
 (out/'report.md').write_text('\n'.join(lines)+'\n')
 print(json.dumps({'run_id':a.run_id,'completed_runs':report['completed_runs'],'models':summaries,'report':str(out/'report.md')},indent=2))
if __name__=='__main__':main()
