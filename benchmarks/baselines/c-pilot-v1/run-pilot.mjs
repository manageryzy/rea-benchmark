import { spawn } from 'node:child_process';
import { readFile,writeFile,mkdir,appendFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { root,bench,node,runtime,config,home,prepareHome,environment,makePatch } from './runtime.mjs';
const stamp=new Date().toISOString().replace(/[-:]/g,'').replace(/\.\d+Z$/,'Z');
const requestedId=process.argv[2]&&!process.argv[2].startsWith('--')?process.argv[2]:`pilot-${stamp}`;
if(!/^[a-zA-Z0-9_-]+$/.test(requestedId))throw new Error('Invalid run id');
const base=`${bench}/runs/${requestedId}`;await mkdir(base,{recursive:true});
const auth=await prepareHome();
const version=JSON.parse(await readFile(`${root}/.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/dsh/package.json`,'utf8')).version;
if(version!==config.harness_version)throw new Error('Harness version mismatch');
const order=process.argv.includes('--smoke')?[['cpp','deepseek-flash'],['cpp','gpt-6.1-sol']]:config.run_order;
const all=[];
await writeFile(`${base}/run-manifest.json`,JSON.stringify({started_at:new Date().toISOString(),configuration:config,harness_version:version,auth_modes:{deepseek:'saved API key',gpt:auth.gpt_transport==='openai-codex'?'existing Codex OAuth':'API key'},scope:process.argv.includes('--smoke')?'connectivity smoke':'six-run pilot'},null,2)+'\n');
for(const [language,id] of order){
 const model={...config.models.find(m=>m.id===id)};if(id==='gpt-6.1-sol')model.provider=auth.gpt_transport;
 const dir=`${base}/${id}/${language}`;await mkdir(dir,{recursive:true});
 try{const old=JSON.parse(await readFile(`${dir}/result.json`,'utf8'));all.push(old);console.log('SKIP existing attempt:',id,language);continue;}catch{}
 const manifest=JSON.parse(await readFile(`${bench}/public/${language}/manifest.json`,'utf8'));
 const binary=await readFile(`${bench}/public/${language}/target`);
 if(createHash('sha256').update(binary).digest('hex')!==manifest.binary_sha256)throw new Error('Target changed after validation');
 const patch=await makePatch(language,model,dir);
 const smoke=process.argv.includes('--smoke');
 const prompt=smoke?'Reply exactly READY. This is a connectivity check; do not call tools.':await readFile(`${bench}/public/${language}/prompt.txt`,'utf8');
 await writeFile(`${dir}/prompt.txt`,prompt);
 console.log(`START ${id} ${language}`);
 const start=performance.now();let buffer='',stderr='',timedOut=false,turnStartedMs=null,writes=Promise.resolve();const events=[];
 const child=spawn(node,[runtime,'--profile','headless','--patch',patch,'--json',prompt],{cwd:`${bench}/public/${language}`,env:environment(),stdio:['pipe','pipe','pipe'],detached:true});
 child.stdin.end();
 const log=(line)=>{if(!line.trim())return;try{const e=JSON.parse(line);events.push(e);if(e.type==='status'&&e.phase==='turn_start'&&turnStartedMs===null)turnStartedMs=performance.now()-start;if(e.type==='tool_call')console.log(`  ${id}/${language}: ${e.tool}`);if(e.type==='status'&&e.phase==='step_end')console.log(`  ${id}/${language}: step completed`);}catch{}};
 child.stdout.on('data',chunk=>{const t=chunk.toString();writes=writes.then(()=>appendFile(`${dir}/events.jsonl`,t));buffer+=t;let p;while((p=buffer.indexOf('\n'))>=0){log(buffer.slice(0,p));buffer=buffer.slice(p+1);}});
 child.stderr.on('data',chunk=>{stderr+=chunk.toString();});
 const timeout=setTimeout(()=>{timedOut=true;try{process.kill(-child.pid,'SIGTERM');}catch{};setTimeout(()=>{try{process.kill(-child.pid,'SIGKILL');}catch{}},10000).unref();},config.task_timeout_seconds*1000);
 const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',resolve);});clearTimeout(timeout);await writes;if(buffer)log(buffer);
 await writeFile(`${dir}/stderr.log`,stderr);
 const final=events.filter(e=>e.type==='final').at(-1)?.text??'';await writeFile(`${dir}/answer.md`,final);
 let trace=[];try{trace=(await readFile(`${dir}/tool-calls.jsonl`,'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);}catch{}
 const usages=events.filter(e=>e.type==='status'&&e.phase==='step_end'&&e.usage).map(e=>e.usage);
 const totals={};for(const usage of usages)for(const [k,v] of Object.entries(usage))if(typeof v==='number')totals[k]=(totals[k]??0)+v;
 const receipt={model:id,model_label:model.label,provider:model.provider,reasoning:model.reasoning,language,case_id:manifest.case_id,binary_sha256:manifest.binary_sha256,prompt_sha256:createHash('sha256').update(prompt).digest('hex'),harness_version:version,started_at:new Date(Date.now()-(performance.now()-start)).toISOString(),elapsed_seconds:(performance.now()-start)/1000,exit_code:code,timed_out:timedOut,tool_calls:trace.length,tool_errors:trace.filter(t=>t.is_error).length,tool_ms:trace.reduce((s,t)=>s+t.elapsed_ms,0),first_native_query_ms:trace.find(t=>!['open_binary','close_binary'].includes(t.tool))?.elapsed_ms??null,reported_usage:totals,actual_cost_usd:null,cost_note:model.provider==='openai-codex'?'Subscription usage; per-task currency charge unavailable':'Actual billed currency charge unavailable from headless event stream',final_present:!!final,turn_end:events.filter(e=>e.type==='status'&&e.phase==='turn_end').at(-1)?.reason??null};
 receipt.harness_startup_seconds=turnStartedMs===null?null:turnStartedMs/1000;
 receipt.active_task_seconds=turnStartedMs===null?null:receipt.elapsed_seconds-turnStartedMs/1000;
 await writeFile(`${dir}/result.json`,JSON.stringify(receipt,null,2)+'\n');all.push(receipt);
 console.log(`FINISH ${id} ${language}: exit=${code}, ${receipt.elapsed_seconds.toFixed(1)}s, ${trace.length} tool calls`);
 if(!smoke){const grade=spawn('/usr/bin/python3',[`${bench}/scripts/grade.py`,dir],{stdio:['ignore','pipe','pipe']});let out='',err='';grade.stdout.on('data',c=>out+=c);grade.stderr.on('data',c=>err+=c);const gc=await new Promise(r=>grade.on('close',r));if(gc)await writeFile(`${dir}/grading-error.log`,err);else console.log(`  Grade: ${JSON.parse(out).passed}/${JSON.parse(out).total}`);}
 await writeFile(`${base}/progress.json`,JSON.stringify(all,null,2)+'\n');
 if(smoke&&code!==0)console.log('Smoke failed; inspect recorded diagnostics before the pilot.');
}
console.log('RUN DIRECTORY:',base);
