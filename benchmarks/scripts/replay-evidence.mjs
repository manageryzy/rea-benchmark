// Offline regression replay of the actual installed projection/serialization code.
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import Module from 'node:module';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { makePatch, evidenceDelivery } from '../harnesses/deepseek-harness.mjs';

const root=fileURLToPath(new URL('../..',import.meta.url)).replace(/\/$/,'');
const require=createRequire(import.meta.url);
const {patchSource,cli}=require('../harnesses/zcode-evidence.cjs');
const input=JSON.parse(await readFile(process.argv[2],'utf8'));
const hash=text=>createHash('sha256').update(text).digest('hex');
// This bulk replay cannot open a socket or invoke a provider, even accidentally.
net.Socket.prototype.connect=()=>{throw new Error('Network forbidden during cached evidence replay');};
const source=await readFile(cli,'utf8');
const overlay=patchSource(source);
assert.throws(()=>patchSource(source+'\n'),/bundle changed/);
function loadBundle(code) {
  const anchor='\nvoid main();\n';
  assert.equal(code.split(anchor).length,2,'CLI main anchor changed');
  // Initialize upstream serializer and MCP modules, replacing only application boot.
  code=code.replace(anchor,`\ninit_mcp4(); init_result_serialization();
module.exports={createMcpToolEntry,serializeOutput,formatMcpToolResult};\n`);
  const loaded=new Module(cli);
  loaded.filename=cli;
  loaded.paths=Module._nodeModulePaths(cli.slice(0,cli.lastIndexOf('/')));
  loaded._compile(code,cli);
  return loaded.exports;
}
const before=loadBundle(source), after=loadBundle(overlay.source);
const descriptor={serverName:'rea',toolName:'procedure_pseudo_code',inputSchema:{type:'object',properties:{}},annotations:{readOnlyHint:true}};
const oldEntry=before.createMcpToolEntry('mcp__rea__procedure_pseudo_code',descriptor,{},false);
const entry=after.createMcpToolEntry('mcp__rea__procedure_pseudo_code',descriptor,{},false);
assert.equal(oldEntry.resultBudget.maxModelBytes,50000);
assert.equal(entry.resultBudget.maxModelBytes,Number.MAX_SAFE_INTEGER);
const other=after.createMcpToolEntry('mcp__other__procedure_pseudo_code',{...descriptor,serverName:'other'},{},false);
assert.equal(other.resultBudget.maxModelBytes,50000,'Overlay must be restricted to benchmark REA');
const dshPath=root+'/.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/dsh-mcp-client/lib/index.js';
const {createMcpToolDefinition}=await import(dshPath);
async function dshText(value) {
  const definition=createMcpToolDefinition({}, {name:'mcp__rea__cached',rawName:'cached',inputSchema:{type:'object',properties:{}},call:async()=>value});
  const output=await definition.execute({},{});
  return definition.output.render({},output).map(block=>block.text).join('\n');
}
const serialize=(runtime,tool,value)=>runtime.serializeOutput({},value,tool,{},'offline',new AbortController().signal);
let clips=0,duplicates=0,large=0,errors=0,maxBytes=0;
const checks=[];
for (const path of input.evidence) {
  const raw=await readFile(root+'/'+path);
  const value=JSON.parse(raw);
  const primary=value.content.filter(b=>b.type==='text').map(b=>b.text).join('\n');
  const old=await serialize(before,oldEntry,value);
  const current=await serialize(after,entry,value);
  assert.equal(current.truncated,false,path+': ZCode clipped evidence');
  // Cached REA results have one primary text block; retain its exact bytes.
  assert(current.content.includes(primary),path+': ZCode primary evidence missing');
  let duplicate=false;
  if(value.structuredContent!==undefined) {
    duplicate=value.content.some(b=>{try{return b.type==='text'&&require('node:util').isDeepStrictEqual(JSON.parse(b.text),value.structuredContent);}catch{return false;}});
    if(duplicate) {
      const {structuredContent,...canonical}=value;
      assert.deepEqual(entry.formatModelContent(value),after.formatMcpToolResult(canonical));
      duplicates++;
    } else {
      assert.deepEqual(entry.formatModelContent(value),after.formatMcpToolResult(value),'Unique structured content lost');
    }
  }
  if(value.isError) {
    await assert.rejects(()=>dshText(value));
    errors++;
  } else {
    assert.equal(await dshText(value),primary,path+': DSH changed primary evidence');
  }
  const bytes=Buffer.byteLength(primary);
  maxBytes=Math.max(maxBytes,bytes);
  if(bytes>50000)large++;
  if(old.truncated)clips++;
  checks.push({path,sha256:hash(raw),primary_bytes:bytes,primary_sha256:hash(primary),
    zcode_model_sha256:hash(current.content),zcode_model_bytes:current.returnedBytes,
    baseline_zcode_clipped:old.truncated,deduplicated:duplicate,is_error:!!value.isError});
}
// Boundary and semantic regressions use the real serializer too, including Unicode.
const synthetic=[
  {content:[{type:'text',text:'前🦀\u0000終'.repeat(30000)+'COMPLETE_BODY_END'}]},
  {content:[{type:'text',text:'{"b":2,"a":1}'},{type:'text',text:'keep this additional block'}],structuredContent:{a:1,b:2}},
  {content:[{type:'text',text:'{"same":true}'}],structuredContent:{same:true,unique:'DO_NOT_DROP'}},
  {content:[{type:'text',text:'not json'}],structuredContent:{unique:'DO_NOT_DROP'}},
  {isError:true,content:[{type:'text',text:'错误'.repeat(40000)+'ERROR_END'}]},
  {content:[{type:'text',text:'{"a":1}'},{type:'image',mimeType:'image/png',data:'eA=='}],structuredContent:{a:1}},
];
for(const value of synthetic) {
  const result=await serialize(after,entry,value);
  assert.equal(result.truncated,false);
  if(value.structuredContent?.unique)assert(JSON.stringify(result.modelContent).includes('DO_NOT_DROP'));
  for(const block of value.content)if(block.type==='text')assert(JSON.stringify(result.modelContent).includes(JSON.stringify(block.text).slice(1,-1)));
}
await mkdir(input.temporary,{recursive:true});
const patch=JSON.parse(JSON.stringify(evidenceDelivery));
for (const model of [{id:'deepseek-flash',provider:'deepseek-official',reasoning:'high'},{id:'gpt-6.1-sol',provider:'openai',reasoning:'high'}]) {
  const path=await makePatch('rust',model,input.temporary);
  const {parse}=require('yaml');
  const entries=parse(await readFile(path,'utf8'));
  for(const id of patch.dsh.disabled_plugins)assert.equal(entries.find(p=>p.id===id)?.disabled,true);
  assert.equal(entries.find(p=>p.id==='compaction-basic').config.auto,false);
  const mcp=entries.find(p=>p.insert)?.insert.find(p=>p.id==='rea-mcp');
  assert(mcp,'Restricted REA gateway must still be installed');
}
const result={passed:true,model_api_calls:0,profile:evidenceDelivery.id,
  verification_layer:'Actual installed DSH MCP projection and ZCode MCP entry + result serializer; application boot disabled for bulk replay',
  dsh:{successful_results:checks.length-errors,error_results:errors,primary_mismatches:0,spill_policy_disabled:true,tool_result_pruner_disabled:true,automatic_compaction:false},
  zcode:{results:checks.length,baseline_clipped:clips,corrected_clipped:0,proven_duplicate_copies_removed:duplicates,results_above_old_primary_cap:large,max_primary_bytes:maxBytes,synthetic_cases:synthetic.length,unique_structured_fields_preserved:true,other_servers_unchanged:true,
    upstream_bundle_sha256:overlay.upstream_sha256,patched_bundle_sha256:overlay.patched_sha256},
  installed_dsh_mcp_sha256:hash(await readFile(dshPath)),checks};
await writeFile(input.output,JSON.stringify(result,null,2)+'\n');
console.log(`PASS: ${checks.length} cached results; ${clips} baseline ZCode clips → 0; ${duplicates} duplicate copies removed; ${maxBytes} largest primary bytes; no model API calls`);
