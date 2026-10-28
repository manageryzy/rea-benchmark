import { readFile,writeFile,mkdir,chmod } from 'node:fs/promises';
import { unlinkSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { stringify } from 'yaml';
import { credentialsFor } from './provider-auth.mjs';
import { loadToolCatalog } from '../scripts/tool-catalog.mjs';
import { spawn, execFile } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { promisify } from 'node:util';
import { performance } from 'node:perf_hooks';
import { createHash } from 'node:crypto';
import { assertExecutionAllowed } from '../scripts/execution-control.mjs';
export const root=fileURLToPath(new URL('../..',import.meta.url)).replace(/\/$/,'');
export const bench=`${root}/benchmarks`;
export const node=`${root}/.tools/node-v24.22.0-linux-x64/bin/node`;
export const runtime=`${root}/.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/dsh/lib/bin.js`;
export const home=`${root}/.cache/benchmark-runtime/home-${randomUUID()}`;
process.on('exit',()=>{try{unlinkSync(`${home}/.credentials.yaml`);}catch{}});
export const configPath=process.env.REA_BENCHMARK_CONFIG??`${bench}/config/pilot.json`;
export const config=JSON.parse(await readFile(configPath,'utf8'));
export const evidenceDelivery=JSON.parse(await readFile(`${bench}/config/evidence-delivery.json`,'utf8'));
export const {path:catalogPath,catalog}=await loadToolCatalog(root,config);
export const tools=catalog.tools;
export async function prepareHome(plan){
 await mkdir(home,{recursive:true,mode:0o700});await chmod(home,0o700);
 const credentials=await credentialsFor(plan);
 await writeFile(`${home}/.credentials.yaml`,stringify(credentials),{mode:0o600});await chmod(`${home}/.credentials.yaml`,0o600);
 await mkdir(`${home}/empty-skills`,{recursive:true});
 return plan;
}
export function environment(sealed={}){
 // Only an explicit launch subset is passed; no API keys enter child environments.
 const env={PATH:`${root}/.tools/node-v24.22.0-linux-x64/bin:/usr/bin:/bin`,HOME:home,TMPDIR:'/tmp',DSH_HOME:home,DSH_AGENTS_HOME:`${home}/empty-skills`,DSH_TOOLS_MODE:'native',NO_COLOR:'1',LANG:'C.UTF-8',...sealed};
 return env;
}

// The coordinator supplies existing suite tasks; this adapter defines no cases.
export async function executeTask(task) {
  await assertExecutionAllowed();
  if(!task.runtime_attestation||process.env.REA_ATTESTED_DISPATCH!==task.runtime_attestation.identity)
    throw new Error('Runtime-attested coordinator dispatch is required');
  if(!task.environment_profile?.files_sha256)throw new Error('Benchmark task lacks environment identity');
  for(const [path,expected] of Object.entries(task.environment_profile.files_sha256)) {
    if(!path.startsWith('benchmarks/'))throw new Error('Invalid benchmark profile input');
    if(createHash('sha256').update(await readFile(root+'/'+path)).digest('hex')!==expected)
      throw new Error('Benchmark adapter/config changed after task preparation');
  }
  const publicDir = task.input_directory;
  const directory = root + '/' + task.directory;
  await mkdir(directory, {recursive:true});
  const auth = await prepareHome(task.runtime_attestation.provider);
  const localTransport=process.env.REA_BENCHMARK_TRANSPORT_URL;
  if(!/^http:\/\/127\.0\.0\.1:[0-9]+\/v1$/.test(localTransport??'')||!task.request_contract)
    throw new Error('Validated request-budget transport is required');
  if(auth.provider==='openai-codex'&&(task.request_contract.protocol!=='openai-codex-responses'||
     task.request_contract.mode!=='observed_task_limits'||task.request_contract.cap_field!==null))
    throw new Error('Codex account requires the explicit observed-limits condition');
  const model = {id:task.model,label:task.label,reasoning:task.reasoning,
    provider:auth.provider,baseURL:localTransport};
  const sealed=Object.fromEntries(Object.entries(task.runtime_attestation.runtime.manifest.environment)
    .map(([key,value])=>[key,value.replaceAll('@workspace',root)]));
  const patch = await makePatch(task.language,model,directory,{publicDir});
  const prompt = await readFile(publicDir+'/prompt.txt','utf8');
  await writeFile(directory+'/prompt.txt',prompt,{flag:'wx'});
  const events = [];
  const stream = createWriteStream(directory+'/events.jsonl',{flags:'wx'});
  const startedAt = new Date().toISOString(), started = performance.now();
  let buffer = '', errors = '', timedOut = false;
  const deadline = Number(process.env.REA_BENCHMARK_DEADLINE_MS);
  if(!Number.isFinite(deadline)||deadline>Date.now()+task.timeout_seconds*1000||deadline<=Date.now())
    throw new Error('Invalid frozen execution deadline');
  const child = spawn(node,[runtime,'--profile','headless','--patch',patch,'--json',prompt],
    {cwd:publicDir,env:environment(sealed),stdio:['pipe','pipe','pipe'],detached:true});
  child.stdin.end();
  let forceStop;
  const stop = () => {
    try {process.kill(-child.pid,'SIGTERM');} catch {}
    forceStop ??= setTimeout(()=>{try{process.kill(-child.pid,'SIGKILL');}catch{}},15000);
  };
  process.on('SIGINT',stop); process.on('SIGTERM',stop);
  const timer = setTimeout(()=>{timedOut=true;stop();},Math.max(0,deadline-Date.now()));
  const parse = line => {
    try {
      const event=JSON.parse(line);
      if(event.type==='status'&&event.phase==='step_end'&&event.usage)event.recorded_at=new Date().toISOString();
      events.push(event);
    } catch {}
  };
  child.stdout.on('data',chunk=>{
    stream.write(chunk);buffer+=chunk.toString();
    let end;while((end=buffer.indexOf('\n'))>=0){parse(buffer.slice(0,end));buffer=buffer.slice(end+1);}
  });
  child.stderr.on('data',chunk=>{errors+=chunk.toString();});
  const exitCode=await new Promise((done,reject)=>{child.on('error',reject);child.on('close',done);});
  timedOut ||= Date.now() >= deadline;
  clearTimeout(timer);clearTimeout(forceStop);process.off('SIGINT',stop);process.off('SIGTERM',stop);
  await new Promise(done=>stream.end(done));
  if(buffer.trim())parse(buffer);
  await writeFile(directory+'/stderr.log',errors);
  let trace=[];try{trace=(await readFile(directory+'/tool-calls.jsonl','utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);}catch{}
  const observations=events.filter(e=>e.type==='status'&&e.phase==='step_end'&&e.usage).map(e=>({finished_at:e.recorded_at,usage:e.usage}));
  const usage={};for(const observation of observations)for(const [key,value]of Object.entries(observation.usage))if(typeof value==='number')usage[key]=(usage[key]??0)+value;
  const receipt={model:task.model,model_label:task.label,provider:model.provider,harness:'DeepSeek Harness',harness_version:config.harness_version,
    reasoning:task.reasoning,case_id:task.id,language:task.language,output_language:task.language,
    environment_profile:task.environment_profile,condition:task.condition,
    runtime_attestation_id:task.runtime_attestation.identity,
    binary_sha256:task.binary_sha256,prompt_sha256:createHash('sha256').update(prompt).digest('hex'),
    started_at:startedAt,completed_at:new Date().toISOString(),elapsed_seconds:(performance.now()-started)/1000,
    exit_code:exitCode,timed_out:timedOut,reported_usage:usage,usage_observations:observations,
    tool_calls:trace.length,tool_errors:trace.filter(t=>t.is_error).length,
    explicit_close:trace.some(t=>t.tool==='close_binary'&&!t.is_error),actual_cost_usd:null};
  await writeFile(directory+'/harness-result.json',JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
  await promisify(execFile)('/usr/bin/python3',[bench+'/scripts/collect_answer.py',directory]);
  try{unlinkSync(home+'/.credentials.yaml');}catch{}
  const responseLimit=exitCode===1&&responseLimitStop(events);
  // The transport ledger independently certifies this stop. Do not create a
  // misleading immutable harness-failure marker for DSH's normal limit exit.
  if(exitCode!==0&&!timedOut&&!responseLimit) {
    await writeFile(directory+'/harness-failure.json',JSON.stringify({...receipt,classification:'harness_or_provider_failure',model_correctness_grade_allowed:false},null,2)+'\n',{flag:'wx'});
    throw new Error('DSH/provider failed; preserve traces without a model correctness grade');
  }
  return receipt;
}
export function responseLimitStop(events) {
  return events.findLast(e=>e.type==='status'&&e.phase==='turn_end')?.reason?.kind==='max-tokens';
}
// The attested per-task deadline owns cancellation. A second, shorter SDK
// timer mislabels quiet reasoning as TIMEOUT and prevents transport recovery.
export function codexStreamIdleTimeoutMs(contract, timeoutSeconds) {
 const seconds=contract?.wall_seconds??timeoutSeconds;
 if(!Number.isFinite(seconds)||seconds<=0)throw new Error('Invalid frozen task deadline');
 return Math.min(2147483647,Math.ceil(seconds*1000)+15000);
}
export async function makePatch(language,model,runDir,{fixtureBaseUrl,publicDir=`${bench}/suites/record-parser/public/${language}`}={}){
 await mkdir(runDir,{recursive:true});
 const outputCeiling=config.request_contract?.output_ceiling??config.max_output_tokens;
 const defaultProvider=model.provider;
 const disabled=['agent-instructions','skill-filesystem','skill-badge','tool-skill','session-title-llm','tool-plugin-manager','plugin-manager','tool-bash','tool-pwsh','tool-jobs','tool-fs','tool-fs-search','tool-subagent','tool-subagent-fork','tool-subagent-control','tool-subagent-list-agents','tool-workflow','workflow-ptc','tool-todo','tool-goal','tool-ralph','tool-web','subagent','subagent-spawn-in-process','subagent-fork-in-process','plan-mode'];
 const providers={};
 // Codex maxTokens is SDK catalogue metadata, not a serialized response cap.
 // SSE keeps all requests on the loopback gate; websocket fallback is disabled.
 const retry=config.request_contract?.transport_recovery;
 const codexRetry=retry?{mode:'normal',maxRetries:retry.max_retries_per_request,
  retryableCodes:['TRANSPORT'],backoff:{initialDelayMs:retry.initial_delay_ms,maxDelayMs:retry.max_delay_ms,jitterRatio:0.1}}:
  {mode:'normal',maxRetries:0};
 if(defaultProvider==='openai-codex')providers['openai-codex']={...(model.baseURL?{baseURL:model.baseURL}:{}),reasoning:'high',transport:'sse',streamIdleTimeoutMs:codexStreamIdleTimeoutMs(config.request_contract,config.timeout_seconds),retryPolicy:codexRetry,models:[{id:model.id,name:model.label,contextWindow:1050000,maxTokens:outputCeiling,reasoningEfforts:{low:'low',medium:'medium',high:'high',xhigh:'xhigh',max:'max'}}]};
 if(defaultProvider==='openai')providers.openai={...(model.baseURL?{baseURL:model.baseURL}:{}),apiKeyEnv:'OPENAI_API_KEY',api:'openai-responses',reasoning:'high',models:[{id:model.id,contextWindow:1050000,maxTokens:outputCeiling,reasoningEfforts:{high:'high'}}]};
 let selected={provider:defaultProvider,model:model.id,reasoningEffort:model.reasoning};
 if(fixtureBaseUrl){
  providers['benchmark-fixture']={apiKeyEnv:'BENCHMARK_FIXTURE_KEY',api:'openai-completions',baseURL:fixtureBaseUrl,defaultMaxTokens:outputCeiling,retryPolicy:{mode:'normal',maxRetries:0},models:[{id:'rea-client-fixture',contextWindow:1000000}]};
  selected={provider:'benchmark-fixture',model:'rea-client-fixture'};
 }
 // Withheld-evidence controls have no tools. Do not launch an unused analysis
 // server against their empty target, including any inherited REA connector.
 const patches=[...[...disabled,...evidenceDelivery.dsh.disabled_plugins,...(tools.length?[]:['rea-mcp'])].map(id=>({id,disabled:true})),
  {id:'compaction-basic',config:{auto:evidenceDelivery.dsh.compaction_auto}},
  {id:'credentials',disabled:false,config:{path:`${home}/.credentials.yaml`,watch:false}},
  {id:'tools',config:{mode:'native',maxParallelSubCalls:1}},
  {id:'system-prompt',config:{personaPrefix:'You are a binary reverse-engineering analyst. Use only the supplied REA tools. Reconstruct behavior from evidence and obey the task limits.',personaSuffix:`Your workspace is ${publicDir}. The only input artifact is target. No source code or test answers are available.`}},
  {id:'llm-pi-ai',config:{providers}},
  {id:'llm-deepseek',config:{...(defaultProvider==='deepseek-official'&&model.baseURL?{baseURL:model.baseURL}:{}),apiKeyEnv:'DEEPSEEK_API_KEY',reasoningEffort:'high',maxTokens:outputCeiling,models:[{id:'deepseek-flash',name:'DeepSeek V4.1 Flash 1017',contextWindow:1000000,maxTokens:outputCeiling}],retryPolicy:{mode:'normal',maxRetries:0}}},
  {id:'agent-default-model',config:selected},
  {insert:[...(config.schema_version>=3?[{id:'benchmark-lossless-output',name:`${bench}/suites/complex-native-v1/scripts/lossless-output.mjs`,config:{path:`${runDir}/assistant-messages.jsonl`}}]:[]),
   {id:'benchmark-policy',name:`${bench}/scripts/policy.mjs`,config:{allowedTools:tools.map(t=>`mcp__rea__${t.name}`),auditPath:`${runDir}/tool-audit.jsonl`}},
   ...(tools.length?[{id:'rea-mcp',name:'@deepseek-ai/dsh-mcp-client',config:{serverName:'rea',transport:'stdio',command:node,args:[`${bench}/scripts/rea-gateway.mjs`,`${publicDir}/target`,`${runDir}/tool-calls.jsonl`,`${publicDir}/gateway.json`],cwd:publicDir,toolCallTimeoutMs:config.tool_timeout_ms,failOnStartupError:true}}]:[])]}
 ];
 const path=`${runDir}/runtime.patch.yml`;await writeFile(path,stringify(patches));return path;
}

if (process.argv[2] === '--task') {
  await executeTask(JSON.parse(await readFile(process.argv[3], 'utf8')));
}
