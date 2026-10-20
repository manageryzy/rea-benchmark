#!/usr/bin/python3
import argparse,csv,json,pathlib,datetime
BENCH=pathlib.Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('run_id');a=p.parse_args()
 base=BENCH/'runs'/a.run_id
 if not base.is_dir(): raise SystemExit('Run directory does not exist')
 run_manifest=json.loads((base/'run-manifest.json').read_text())
 environment=json.loads((BENCH.parent/'environment-lock.json').read_text())
 rows=[]
 for path in sorted(base.glob('*/*/result.json')):
  d=json.loads(path.read_text());gpath=path.parent/'grade.json';g=json.loads(gpath.read_text()) if gpath.exists() else {}
  reviewpath=path.parent/'evidence-review.json';review=json.loads(reviewpath.read_text()) if reviewpath.exists() else {}
  rows.append({'model':d['model'],'language':d['language'],'provider':d['provider'],'reasoning':d['reasoning'],'status':'completed' if d['exit_code']==0 and d['final_present'] else 'infrastructure_or_run_error','compiled':g.get('compiled',False),'passed':g.get('passed',0),'tests':g.get('total',0),'pass_rate':g.get('pass_rate',0),'wall_seconds':round(d['elapsed_seconds'],2),'harness_startup_seconds':round(d.get('harness_startup_seconds') or 0,2),'active_task_seconds':round(d.get('active_task_seconds') or 0,2),'tool_calls':d['tool_calls'],'tool_errors':d['tool_errors'],'first_native_query_seconds':round((d['first_native_query_ms'] or 0)/1000,2),'input_tokens':d['reported_usage'].get('inputTokens'),'output_tokens':d['reported_usage'].get('outputTokens'),'reasoning_tokens':d['reported_usage'].get('reasoningTokens'),'cache_read_tokens':d['reported_usage'].get('cacheReadTokens'),'cache_write_tokens':d['reported_usage'].get('cacheWriteTokens'),'total_tokens':d['reported_usage'].get('totalTokens'),'actual_cost_usd':d['actual_cost_usd'],'evidence_review':review.get('verdict','pending manual review'),'failure':g.get('failure') or (str(d.get('turn_end')) if d['exit_code'] else '')})
 rows.sort(key=lambda r:(r['model']!='deepseek-flash',{'cpp':0,'rust':1,'go':2}.get(r['language'],3)))
 out=BENCH/'reports'/a.run_id;out.mkdir(parents=True,exist_ok=True)
 if rows:
  with (out/'comparison.csv').open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat()
 report={'generated_at_jst':now,'run_id':a.run_id,'runs':rows,'expected_runs':6,'completed_runs':sum(r['status']=='completed' for r in rows),'scope':'One small fixture per language, one first attempt per model; this is a pilot, not a general model ranking.','timing_note':'Wall time includes Harness startup, model inference, tools, and cleanup. First native query includes Ghidra import/auto-analysis. Reasoning-effort labels do not establish equal compute.','usage_note':'Token fields retain provider-reported semantics. Cached and reasoning token accounting can differ between providers; compare the raw usage receipts alongside totals.','cost_note':'Actual per-task monetary billing is unavailable; currency cost remains null. GPT may use Codex subscription OAuth.'}
 report['runtime']={name:environment[name]['version'] for name in ['harness','rea','ghidra']}
 report['auth_modes']=run_manifest['auth_modes']
 report['cost_note']='Actual per-task monetary billing is unavailable; currency cost remains null. GPT authentication: '+run_manifest['auth_modes']['gpt']+'.'
 (out/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
 lines=[f'# Six-run reverse-engineering pilot: {a.run_id}',f'\nGenerated {now} (JST).\n',f"Recorded attempts: {len(rows)}/6; completed: {report['completed_runs']}/6.\n",f"Harness {report['runtime']['harness']}; REA {report['runtime']['rea']}; Ghidra {report['runtime']['ghidra']}. Both models requested high reasoning effort.\n",'| Model | Language | Result | Compile | Tests passed | Active task (s) | Tools | Evidence review |','|---|---|---|---|---:|---:|---:|---|']
 for r in rows: lines.append(f"| {r['model']} | {r['language']} | {r['status']} | {r['compiled']} | {r['passed']}/{r['tests']} | {r['active_task_seconds']} | {r['tool_calls']} | {r['evidence_review'].replace('_',' ')} |")
 lines+=['\n'+report['scope'],'\n'+report['timing_note'],'\n'+report['usage_note'],'\n'+report['cost_note'],'\nBuild/oracle validation and grader self-tests are separate from model scores. Sources and hidden tests were unavailable through the model tool surface. Full prompts, final answers, evidence, tool traces, token usage, and grading receipts are retained in the run directory.']
 lines+=['\nRun artifacts (final answers and evidence reviews):\n']
 for r in rows:
  prefix=f"../../runs/{a.run_id}/{r['model']}/{r['language']}"
  lines.append(f"- {r['model']} / {r['language']}: [answer]({prefix}/answer.md), [evidence review]({prefix}/evidence-review.json), [grading]({prefix}/grade.json).")
 if (out/'protocol-audit.json').exists(): lines+=['\n[Protocol audit](protocol-audit.json) records fresh sessions, identical per-language prompts/binaries, eight allowed tools, explicit session closure, and temporary credential cleanup.']
 (out/'report.md').write_text('\n'.join(lines)+'\n')
 print(json.dumps(report,indent=2))
if __name__=='__main__':main()
