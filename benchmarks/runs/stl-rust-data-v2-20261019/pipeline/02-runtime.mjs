import { readFile,writeFile,mkdir,chmod } from 'node:fs/promises';
import { unlinkSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { stringify,parse } from 'yaml';
import { loadToolCatalog } from './tool-catalog.mjs';
export const root=fileURLToPath(new URL('../..',import.meta.url)).replace(/\/$/,'');
export const bench=`${root}/benchmarks`;
export const node=`${root}/.tools/node-v24.22.0-linux-x64/bin/node`;
export const runtime=`${root}/.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/dsh/lib/bin.js`;
export const home=`${root}/.cache/benchmark-runtime/home-${randomUUID()}`;
process.on('exit',()=>{try{unlinkSync(`${home}/.credentials.yaml`);}catch{}});
export const configPath=process.env.REA_BENCHMARK_CONFIG??`${bench}/config/pilot.json`;
export const config=JSON.parse(await readFile(configPath,'utf8'));
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
export async function makePatch(language,model,runDir,{fixtureBaseUrl,publicDir=`${bench}/public/${language}`}={}){
 await mkdir(runDir,{recursive:true});
 const defaultProvider=model.provider;
 const disabled=['agent-instructions','skill-filesystem','skill-badge','tool-skill','session-title-llm','tool-plugin-manager','plugin-manager','tool-bash','tool-pwsh','tool-jobs','tool-fs','tool-fs-search','tool-subagent','tool-subagent-fork','tool-subagent-control','tool-subagent-list-agents','tool-workflow','workflow-ptc','tool-todo','tool-goal','tool-ralph','tool-web','subagent','subagent-spawn-in-process','subagent-fork-in-process','plan-mode'];
 const providers={};
 if(defaultProvider==='openai-codex')providers['openai-codex']={reasoning:'high',transport:'sse',streamIdleTimeoutMs:180000,retryPolicy:{mode:'normal',maxRetries:0},models:[{id:'gpt-6.1-sol',name:'GPT-6.1 Sol',contextWindow:1050000,maxTokens:config.max_output_tokens,reasoningEfforts:{low:'low',medium:'medium',high:'high',xhigh:'xhigh',max:'max'}}]};
 if(defaultProvider==='openai')providers.openai={apiKeyEnv:'OPENAI_API_KEY',api:'openai-responses',reasoning:'high',models:[{id:'gpt-6.1-sol',contextWindow:1050000,maxTokens:config.max_output_tokens,reasoningEfforts:{high:'high'}}]};
 let selected={provider:defaultProvider,model:model.id,reasoningEffort:model.reasoning};
 if(fixtureBaseUrl){
  providers['benchmark-fixture']={apiKeyEnv:'BENCHMARK_FIXTURE_KEY',api:'openai-completions',baseURL:fixtureBaseUrl,defaultMaxTokens:config.max_output_tokens,retryPolicy:{mode:'normal',maxRetries:0},models:[{id:'rea-client-fixture',contextWindow:1000000}]};
  selected={provider:'benchmark-fixture',model:'rea-client-fixture'};
 }
 const patches=[...disabled.map(id=>({id,disabled:true})),
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
