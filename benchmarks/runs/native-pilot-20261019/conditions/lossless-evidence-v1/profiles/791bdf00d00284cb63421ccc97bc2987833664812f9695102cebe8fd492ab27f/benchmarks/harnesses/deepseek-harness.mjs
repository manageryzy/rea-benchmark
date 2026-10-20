import { readFile,writeFile,mkdir,chmod } from 'node:fs/promises';
import { unlinkSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { stringify,parse } from 'yaml';
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
export async function prepareHome(){
 await mkdir(home,{recursive:true,mode:0o700});await chmod(home,0o700);
 const saved=parse(await readFile(`${root}/.harness/.credentials.yaml`,'utf8'));
 const deepseek=process.env.DEEPSEEK_API_KEY??saved.refs?.DEEPSEEK_API_KEY;
 if(!deepseek)throw new Error('Missing DeepSeek credentials in Harness Settings → Models');
 const auth=JSON.parse(await readFile('/mnt/c/Users/mana/.codex/auth.json','utf8'));
 const credentials={version:1,refs:{DEEPSEEK_API_KEY:deepseek},records:{}};
 if(process.env.OPENAI_API_KEY||auth.OPENAI_API_KEY){
  credentials.refs.OPENAI_API_KEY=process.env.OPENAI_API_KEY??auth.OPENAI_API_KEY;
 }else{
  const t=auth.tokens??{};if(!t.access_token)throw new Error('Missing OpenAI/Codex sign-in');
  const claims=JSON.parse(Buffer.from(t.access_token.split('.')[1],'base64url').toString());
  if(claims.exp*1000<Date.now()+3600000)throw new Error('Codex sign-in expires too soon; refresh it before the benchmark');
  credentials.records['llm-pi-ai/openai-codex']={kind:'grant',payload:{type:'oauth',access:t.access_token,refresh:t.refresh_token??'',expires:claims.exp*1000}};
 }
 await writeFile(`${home}/.credentials.yaml`,stringify(credentials),{mode:0o600});await chmod(`${home}/.credentials.yaml`,0o600);
 await mkdir(`${home}/empty-skills`,{recursive:true});
 return {deepseek:true,gpt_transport:credentials.refs.OPENAI_API_KEY?'openai':'openai-codex'};
}
export function environment(){
 // Only an explicit launch subset is passed; no API keys enter child environments.
 const env={PATH:`${root}/.tools/node-v24.22.0-linux-x64/bin:/usr/bin:/bin`,DSH_HOME:home,DSH_AGENTS_HOME:`${home}/empty-skills`,DSH_TOOLS_MODE:'native',NO_COLOR:'1',LANG:'C.UTF-8'};
 for(const key of ['HOME','USER','LOGNAME','TMPDIR','SSL_CERT_FILE','SSL_CERT_DIR','HTTP_PROXY','HTTPS_PROXY','NO_PROXY'])if(process.env[key])env[key]=process.env[key];
 return env;
}

// The coordinator supplies existing suite tasks; this adapter defines no cases.
export async function executeTask(task) {
  await assertExecutionAllowed();
  if(!task.environment_profile?.files_sha256)throw new Error('Benchmark task lacks environment identity');
  for(const [path,expected] of Object.entries(task.environment_profile.files_sha256)) {
    if(!path.startsWith('benchmarks/'))throw new Error('Invalid benchmark profile input');
    if(createHash('sha256').update(await readFile(root+'/'+path)).digest('hex')!==expected)
      throw new Error('Benchmark adapter/config changed after task preparation');
  }
  const publicDir = task.input_directory;
  const directory = root + '/' + task.directory;
  await mkdir(directory, {recursive:true});
  const auth = await prepareHome();
  const model = {id:task.model,label:task.label,reasoning:task.reasoning,
    provider:task.model.startsWith('gpt-') ? auth.gpt_transport : 'deepseek-official'};
  const patch = await makePatch(task.language,model,directory,{publicDir});
  const prompt = await readFile(publicDir+'/prompt.txt','utf8');
  await writeFile(directory+'/prompt.txt',prompt,{flag:'wx'});
  const events = [];
  const stream = createWriteStream(directory+'/events.jsonl',{flags:'wx'});
  const startedAt = new Date().toISOString(), started = performance.now();
  let buffer = '', errors = '', timedOut = false;
  const child = spawn(node,[runtime,'--profile','headless','--patch',patch,'--json',prompt],
    {cwd:publicDir,env:environment(),stdio:['pipe','pipe','pipe'],detached:true});
  child.stdin.end();
  let forceStop;
  const stop = () => {
    try {process.kill(-child.pid,'SIGTERM');} catch {}
    forceStop ??= setTimeout(()=>{try{process.kill(-child.pid,'SIGKILL');}catch{}},15000);
  };
  process.on('SIGINT',stop); process.on('SIGTERM',stop);
  const timer = setTimeout(()=>{timedOut=true;stop();},task.timeout_seconds*1000);
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
    binary_sha256:task.binary_sha256,prompt_sha256:createHash('sha256').update(prompt).digest('hex'),
    started_at:startedAt,completed_at:new Date().toISOString(),elapsed_seconds:(performance.now()-started)/1000,
    exit_code:exitCode,timed_out:timedOut,reported_usage:usage,usage_observations:observations,
    tool_calls:trace.length,tool_errors:trace.filter(t=>t.is_error).length,
    explicit_close:trace.some(t=>t.tool==='close_binary'&&!t.is_error),actual_cost_usd:null};
  await writeFile(directory+'/result.json',JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
  await promisify(execFile)('/usr/bin/python3',[bench+'/scripts/collect_answer.py',directory]);
  try{unlinkSync(home+'/.credentials.yaml');}catch{}
  if(exitCode!==0&&!timedOut) {
    await writeFile(directory+'/harness-failure.json',JSON.stringify({...receipt,classification:'harness_or_provider_failure',model_correctness_grade_allowed:false},null,2)+'\n',{flag:'wx'});
    throw new Error('DSH/provider failed; preserve traces without a model correctness grade');
  }
  return receipt;
}
export async function makePatch(language,model,runDir,{fixtureBaseUrl,publicDir=`${bench}/suites/record-parser/public/${language}`}={}){
 await mkdir(runDir,{recursive:true});
 const defaultProvider=model.provider;
 const disabled=['agent-instructions','skill-filesystem','skill-badge','tool-skill','session-title-llm','tool-plugin-manager','plugin-manager','tool-bash','tool-pwsh','tool-jobs','tool-fs','tool-fs-search','tool-subagent','tool-subagent-fork','tool-subagent-control','tool-subagent-list-agents','tool-workflow','workflow-ptc','tool-todo','tool-goal','tool-ralph','tool-web','subagent','subagent-spawn-in-process','subagent-fork-in-process','plan-mode'];
 const providers={};
 if(defaultProvider==='openai-codex')providers['openai-codex']={reasoning:'high',transport:'sse',streamIdleTimeoutMs:180000,retryPolicy:{mode:'normal',maxRetries:0},models:[{id:model.id,name:model.label,contextWindow:1050000,maxTokens:config.max_output_tokens,reasoningEfforts:{low:'low',medium:'medium',high:'high',xhigh:'xhigh',max:'max'}}]};
 if(defaultProvider==='openai')providers.openai={apiKeyEnv:'OPENAI_API_KEY',api:'openai-responses',reasoning:'high',models:[{id:model.id,contextWindow:1050000,maxTokens:config.max_output_tokens,reasoningEfforts:{high:'high'}}]};
 let selected={provider:defaultProvider,model:model.id,reasoningEffort:model.reasoning};
 if(fixtureBaseUrl){
  providers['benchmark-fixture']={apiKeyEnv:'BENCHMARK_FIXTURE_KEY',api:'openai-completions',baseURL:fixtureBaseUrl,defaultMaxTokens:config.max_output_tokens,retryPolicy:{mode:'normal',maxRetries:0},models:[{id:'rea-client-fixture',contextWindow:1000000}]};
  selected={provider:'benchmark-fixture',model:'rea-client-fixture'};
 }
 const patches=[...[...disabled,...evidenceDelivery.dsh.disabled_plugins].map(id=>({id,disabled:true})),
  {id:'compaction-basic',config:{auto:evidenceDelivery.dsh.compaction_auto}},
  {id:'credentials',disabled:false,config:{path:`${home}/.credentials.yaml`,watch:false}},
  {id:'tools',config:{mode:'native',maxParallelSubCalls:1}},
  {id:'system-prompt',config:{personaPrefix:'You are a binary reverse-engineering analyst. Use only the supplied REA tools. Reconstruct behavior from evidence and obey the task limits.',personaSuffix:`Your workspace is ${publicDir}. The only input artifact is target. No source code or test answers are available.`}},
  {id:'llm-pi-ai',config:{providers}},
  {id:'llm-deepseek',config:{apiKeyEnv:'DEEPSEEK_API_KEY',reasoningEffort:'high',maxTokens:config.max_output_tokens,models:[{id:'deepseek-flash',name:'DeepSeek V4.1 Flash 1017',contextWindow:1000000,maxTokens:config.max_output_tokens}],retryPolicy:{mode:'normal',maxRetries:0}}},
  {id:'agent-default-model',config:selected},
  {insert:[...(config.schema_version>=3?[{id:'benchmark-lossless-output',name:`${bench}/suites/complex-native-v1/scripts/lossless-output.mjs`,config:{path:`${runDir}/assistant-messages.jsonl`}}]:[]),
   {id:'benchmark-policy',name:`${bench}/scripts/policy.mjs`,config:{allowedTools:tools.map(t=>`mcp__rea__${t.name}`),auditPath:`${runDir}/tool-audit.jsonl`}},
   {id:'rea-mcp',name:'@deepseek-ai/dsh-mcp-client',config:{serverName:'rea',transport:'stdio',command:node,args:[`${bench}/scripts/rea-gateway.mjs`,`${publicDir}/target`,`${runDir}/tool-calls.jsonl`,configPath],cwd:publicDir,toolCallTimeoutMs:config.tool_timeout_ms,failOnStartupError:true}}]}
 ];
 const path=`${runDir}/runtime.patch.yml`;await writeFile(path,stringify(patches));return path;
}

if (process.argv[2] === '--task') {
  await executeTask(JSON.parse(await readFile(process.argv[3], 'utf8')));
}
