// Real DSH process + MCP + loopback model transport; never a model API request.
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import {promisify} from 'node:util';
import {execFile} from 'node:child_process';
import {createHash} from 'node:crypto';
import {stringify,parse} from 'yaml';
import {createOpenAiModelFixture} from '../../upstream/rea/scripts/verify/clients/model-fixture.mjs';
import {root,bench,node,runtime,home,environment,makePatch} from '../harnesses/deepseek-harness.mjs';
const input=JSON.parse(await readFile(process.argv[2],'utf8'));
await mkdir(home,{recursive:true,mode:0o700});
await mkdir(home+'/empty-skills',{recursive:true});
await writeFile(home+'/.credentials.yaml',stringify({version:1,refs:{BENCHMARK_FIXTURE_KEY:'offline-only'},records:{}}),{mode:0o600});
let count=0;
const delivered=[];
const fixture=await createOpenAiModelFixture({directory:input.directory,prefix:'dsh',onRequest:async({body,tools})=>{
  assert(tools.every(t=>t.name.startsWith('mcp__rea__')),'Native tool leaked');
  if(count>0) {
    const message=body.messages.find(m=>m.role==='tool'&&m.tool_call_id==='replay_'+count);
    const expected=input.steps[count-1].expected_result_path??root+'/'+input.steps[count-1].result_path;
    const raw=JSON.parse(await readFile(expected,'utf8'));
    const primary=raw.content.filter(b=>b.type==='text').map(b=>b.text).join('\n');
    assert(message?.content===primary,'Actual DSH wire input changed evidence at step '+count);
    delivered.push(Buffer.byteLength(primary));
  }
  if(count<input.steps.length) {
    const step=input.steps[count++];
    return {role:'assistant',content:null,tool_calls:[{id:'replay_'+count,type:'function',function:{name:'mcp__rea__'+step.tool,arguments:JSON.stringify(step.arguments||{})}}]};
  }
  return {role:'assistant',content:'OFFLINE_EVIDENCE_OK'};
}});
try {
  const patch=await makePatch('rust',{id:'deepseek-flash',provider:'deepseek-official',reasoning:'high'},input.directory,{fixtureBaseUrl:fixture.baseUrl,publicDir:input.directory});
  const entries=parse(await readFile(patch,'utf8'));
  const mcp=entries.find(p=>p.insert).insert.find(p=>p.id==='rea-mcp');
  mcp.config.args=[bench+'/scripts/replay-mcp.mjs',input.spec];
  await writeFile(patch,stringify(entries));
  const child=promisify(execFile)(node,[runtime,'--profile','headless','--patch',patch,'--json','Replay the supplied analysis tools.'],{
    cwd:input.directory,env:{...environment(),REA_OFFLINE_REPLAY:'1',BENCHMARK_FIXTURE_KEY:'offline-only',NODE_OPTIONS:'--require '+bench+'/scripts/offline-network.cjs'},timeout:120000,maxBuffer:4*1024*1024});
  child.child.stdin.end();
  let result;
  try {result=await child;} catch(error) {
    await writeFile(input.directory+'/dsh-events.jsonl',error.stdout||'');
    await writeFile(input.directory+'/dsh-stderr.log',error.stderr||'');
    throw new Error('DSH offline fixture failed: '+(error.stderr||error.message).slice(0,1800));
  }
  await writeFile(input.directory+'/dsh-events.jsonl',result.stdout);
  await writeFile(input.directory+'/dsh-stderr.log',result.stderr);
  assert.equal(fixture.failure,undefined);
  assert.equal(delivered.length,input.steps.length);
  assert(result.stdout.includes('OFFLINE_EVIDENCE_OK'));
  const hashes=await Promise.all(input.steps.map(async step=>{
    const raw=JSON.parse(await readFile(step.expected_result_path??root+'/'+step.result_path,'utf8'));
    return createHash('sha256').update(raw.content.filter(b=>b.type==='text').map(b=>b.text).join('\n')).digest('hex');
  }));
  await writeFile(input.output,JSON.stringify({passed:true,model_api_calls:0,requests:fixture.requests.length,tool_results:delivered.length,primary_bytes:delivered,delivered_sha256:hashes,verification_layer:'Actual DSH CLI outgoing loopback HTTP messages',headless_logs_may_still_shorten:true})+'\n');
  console.log('PASS: DSH CLI model transport preserves all '+delivered.length+' large cached bodies');
} finally {
  await fixture.close();
  await rm(home,{recursive:true,force:true});
}
