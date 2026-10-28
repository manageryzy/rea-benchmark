// Actual installed DSH provider serialization; dummy credentials, loopback only.
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import {promisify} from 'node:util';
import {execFile} from 'node:child_process';
import {createWriteStream} from 'node:fs';
import {parse,stringify} from 'yaml';
import {root,node,runtime,makePatch,environment} from '../harnesses/deepseek-harness.mjs';
const spec=JSON.parse(await readFile(process.argv[2],'utf8'));
const home=spec.directory+'/client-home';
assert.match(spec.baseURL,/^http:\/\/127\.0\.0\.1:[0-9]+\/v1$/);
await mkdir(home,{recursive:true,mode:0o700});
await mkdir(home+'/empty-skills',{recursive:true});
const records={};
if(spec.provider==='openai-codex'){
 const claims={exp:Date.now()/1000+7200,'https://api.openai.com/auth':{chatgpt_account_id:'offline-fixture-account'}};
 const token='offline.'+Buffer.from(JSON.stringify(claims)).toString('base64url')+'.dummy';
 records['llm-pi-ai/openai-codex']={kind:'grant',payload:{type:'oauth',access:token,refresh:'offline-dummy',expires:claims.exp*1000}};
}
await writeFile(home+'/.credentials.yaml',stringify({version:1,refs:{OPENAI_API_KEY:'sk-offline-dummy-only',DEEPSEEK_API_KEY:'offline-dummy-only'},records}),{mode:0o600});
try{
 const model={id:spec.model,provider:spec.provider,reasoning:'high',baseURL:spec.baseURL,label:'Offline request control'};
 const path=await makePatch('cpp',model,spec.directory,{publicDir:spec.directory});
 const patch=parse(await readFile(path,'utf8'));
 patch.find(entry=>entry.id==='credentials').config.path=home+'/.credentials.yaml';
 // Evidence-bearing fixtures use a replay server. Withheld-evidence fixtures
 // must exercise production's no-connector path, not hide a startup failure
 // behind an unnecessary empty replay service.
 const mcp=patch.find(entry=>entry.insert).insert.find(item=>item.id==='rea-mcp');
 const catalog=JSON.parse(await readFile(spec.tools??root+'/benchmarks/config/rea-tools.json','utf8'));
 if(catalog.tools.length){
  assert(mcp,'Evidence fixture lost its MCP connector');
  const replay=spec.directory+'/replay.json';
  await writeFile(replay,JSON.stringify({tools:spec.tools??root+'/benchmarks/config/rea-tools.json',steps:[]}));
  mcp.config.args=[root+'/benchmarks/scripts/replay-mcp.mjs',replay];
 }else{
  assert.equal(mcp,undefined,'Premises-only fixture started an analysis connector');
  assert(patch.some(entry=>entry.id==='rea-mcp'&&entry.disabled===true),'Inherited REA connector is not disabled');
 }
 await writeFile(path,stringify(patch));
 const child=promisify(execFile)(node,[runtime,'--profile','headless','--patch',path,'--json',spec.prompt??'Reply REQUEST_BUDGET_OK.'],{
  cwd:spec.directory,env:{...environment(),HOME:home,DSH_HOME:home,DSH_AGENTS_HOME:home+'/empty-skills',
   REA_OFFLINE_REPLAY:'1',NODE_OPTIONS:'--require '+root+'/benchmarks/scripts/offline-network.cjs'},
  timeout:120000,maxBuffer:2*1024*1024});
 child.child.stdin.end();
 child.child.stdout.pipe(createWriteStream(spec.directory+'/runtime-stdout.jsonl'));
 child.child.stderr.pipe(createWriteStream(spec.directory+'/runtime-stderr.log'));
 const result=await child;
 assert(result.stdout.includes('REQUEST_BUDGET_OK'),result.stderr.slice(-1000));
 process.stdout.write('PASS: actual '+spec.provider+' DSH request and response on loopback\n');
}finally{await rm(home,{recursive:true,force:true});}
