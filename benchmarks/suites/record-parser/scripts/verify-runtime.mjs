import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir,writeFile,readFile } from 'node:fs/promises';
import { createOpenAiModelFixture } from '../../../../upstream/rea/scripts/verify/clients/model-fixture.mjs';
import {root,bench,node,runtime,config,prepareHome,environment,makePatch} from '../../../harnesses/deepseek-harness.mjs';
const exec=promisify(execFile);
await prepareHome();
const directory=`${root}/.cache/benchmark-runtime/verification`;
await mkdir(directory,{recursive:true});
let step=0;
const fixture=await createOpenAiModelFixture({directory,prefix:'isolation',onRequest:async({body,tools})=>{
 assert.equal(tools.length,8,'Only eight analysis tools may reach a model');
 assert(tools.every(t=>t.name.startsWith('mcp__rea__')),'Unexpected native or filesystem tool');
 const calls=[
  ['mcp__rea__open_binary',{path:`${bench}/suites/record-parser/authoring/cpp/case.cpp`,provider_id:'ghidra'}],
  ['mcp__rea__open_binary',{path:'target',provider_id:'ghidra'}],
  ['mcp__rea__close_binary',{}]
 ];
 if(step<calls.length){const [name,args]=calls[step++];return {role:'assistant',content:null,tool_calls:[{id:`fixture_${step}`,type:'function',function:{name,arguments:JSON.stringify(args)}}]};}
 assert(body.messages.some(m=>m.role==='tool'&&m.tool_call_id==='fixture_3'));
 return {role:'assistant',content:'BENCHMARK_RUNTIME_OK'};
}});
try{
 const patch=await makePatch('cpp',config.models[0],directory,{fixtureBaseUrl:fixture.baseUrl});
 const promise=exec(node,[runtime,'--profile','headless','--patch',patch,'--json','Verify the restricted REA tool connection.'],{cwd:`${bench}/suites/record-parser/public/cpp`,env:{...environment(),BENCHMARK_FIXTURE_KEY:'local-fixture-only'},timeout:300000,maxBuffer:16*1024*1024});
 promise.child.stdin.end();
 const result=await promise;
 await writeFile(`${directory}/stdout.jsonl`,result.stdout);await writeFile(`${directory}/stderr.log`,result.stderr);
 assert.equal(fixture.failure,undefined);assert(result.stdout.includes('BENCHMARK_RUNTIME_OK'));
 const trace=(await readFile(`${directory}/tool-calls.jsonl`,'utf8')).trim().split('\n').map(JSON.parse).slice(-3);
 assert(trace[0].is_error&&!trace[1].is_error&&!trace[2].is_error,'Source must be rejected and the public binary accepted');
 await writeFile(`${bench}/config/isolation-validation.json`,JSON.stringify({passed:true,model_tools:8,source_path_rejected:true,public_binary_opened:true,session_closed:true,live_model:false},null,2)+'\n');
 console.log('PASS: same Harness runtime, eight tools, source denied, public ELF opened and closed.');
}catch(error){if(error.stdout)await writeFile(`${directory}/stdout.jsonl`,error.stdout);if(error.stderr)await writeFile(`${directory}/stderr.log`,error.stderr);throw new Error(error.message)}
finally{await fixture.close();}
