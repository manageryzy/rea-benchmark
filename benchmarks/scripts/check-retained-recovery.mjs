// Actual REA encoder/projector and MCP stdio, synthetic target; no model API.
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {Server} from '@modelcontextprotocol/server';
import {StdioServerTransport} from '@modelcontextprotocol/server/stdio';
import {Client} from '@modelcontextprotocol/client';
import {StdioClientTransport} from '@modelcontextprotocol/client/stdio';
import {createToolResultDelivery} from '../../node_modules/rea-agents/dist/server/toolResult.js';
import {projectNativeFunctionView} from '../../node_modules/rea-agents/dist/domain/analysisView/nativeFunctionView.js';
import {EvidencePager,canonicalText} from './evidence-paging.mjs';
const id='ev_'+ 'a'.repeat(64),target='b'.repeat(64),procedure='0x1000';
const pseudocode='int f(){\n'+ '/* 😀 中 \\ \" */\n'.repeat(3000)+'return 0;\n}';
const dossier={procedure:{address:procedure},pseudocode,assembly:[{address:procedure,text:'ret'}],
 native_value_flow:null,native_api:null,limitations:[],callers:[],callees:[],incoming_references:[],outgoing_references:[]};
const parent={evidenceId:id,artifact:{path:'fixture-target',sha256:target},limitations:[]};
const delivery=createToolResultDelivery();
const original={evidence_id:id,operation:'analyze_function',normalized_result:dossier,raw_result:{retained_only:'x'.repeat(11*1024*1024)}};
function envelope(v){return {evidence_id:'ev_'+'c'.repeat(64),operation:'inspect_analysis_view',normalized_result:v,raw_result:null,evidence_links:[id]};}
function viewResult(args){
 assert.equal(args.source.evidence_id,id);
 const v=projectNativeFunctionView(parent,dossier,args.view);assert.equal(v.ok,true);
 return delivery.toEvidenceToolResult(envelope(v.value),{name:'inspect_analysis_view',returnsTargetContent:true},{ok:true});
}
if(process.argv.includes('--fixture-server')){
 const server=new Server({name:'retained-fixture',version:'1'},{capabilities:{tools:{}}});
 server.setRequestHandler('tools/call',async({params})=>{
  if(params.name==='analyze_function')return delivery.toEvidenceToolResult(original,{name:params.name,returnsTargetContent:true},{ok:true});
  assert.equal(params.name,'inspect_analysis_view');return viewResult(params.arguments);
 });
 await server.connect(new StdioServerTransport());
}else{
 const client=new Client({name:'retained-recovery-control',version:'1'});
 const transport=new StdioClientTransport({command:process.execPath,args:[fileURLToPath(import.meta.url),'--fixture-server'],stderr:'pipe'});
 await client.connect(transport);
 try{
  let analyses=0,pages=0;const audit=[];
  const raw=await client.callTool({name:'analyze_function',arguments:{procedure}});analyses++;
  assert.equal(raw.isError,true);assert.equal(JSON.parse(canonicalText(raw)).error.code,'resource_constraint');
  const policy={id:'lean-evidence-v4',compact_semantic_views:true,inline_characters:24000,default_page_characters:12000,maximum_page_characters:24000,catalog_entries:24};
  const pager=new EvidencePager(policy);
  const recovered=await pager.recover(raw,async(p,o)=>{pages++;return client.callTool(p,o);},{targetSha256:target,procedure,audit:row=>audit.push(row)});
  assert.ok(!recovered.isError);assert.ok(Array.from(canonicalText(recovered)).length<=24000);
  const ref=recovered.structuredContent.evidence_ref;
  let piece=recovered.structuredContent.primary_view,joined=piece.text;
  while(piece.next_offset!==null){piece=pager.read({evidence_ref:ref,pointer:piece.pointer,offset:piece.next_offset,limit:24000}).structuredContent;joined+=piece.text;}
  assert.equal(joined,pseudocode);assert.equal(analyses,1);assert.ok(pages>=3);assert.equal(audit.length,pages);
  const assembly=await pager.readRetained({evidence_ref:ref,pointer:'/retained/assembly',limit:32});
  assert.deepEqual(assembly.structuredContent.item,dossier.assembly);
  await assert.rejects(()=>pager.readRetained({evidence_ref:ref,pointer:'/retained/arbitrary'}),/Unapproved/);
  for(const mutation of ['artifact','parent','coverage']){
   await assert.rejects(()=>new EvidencePager(policy).recover(raw,async({arguments:args})=>{
    const r=viewResult(args),v=r.structuredContent.normalized_result;
    if(mutation==='artifact')v.artifact.sha256='0'.repeat(64);
    if(mutation==='parent')v.parent_evidence_id='ev_'+'0'.repeat(64);
    if(mutation==='coverage')v.coverage.next_offset=0;
    return {content:[{type:'text',text:JSON.stringify(r.structuredContent)}],structuredContent:r.structuredContent};
   },{targetSha256:target,procedure}),/mismatch|coverage/);
   parent.artifact.sha256=target;
  }
  await assert.rejects(()=>new EvidencePager(policy).recover(raw,()=>assert.fail('expired recovery must not send'),{targetSha256:target,procedure,timeoutMs:0}),/deadline/);
  console.log(JSON.stringify({passed:8,actual_mcp_stdio:true,actual_rea_encoder:true,original_fixture_bytes:Buffer.byteLength(JSON.stringify(original)),analysis_calls:analyses,retained_view_calls:pages,pseudocode_utf16:pseudocode.length,model_api_calls:0}));
 }finally{await client.close();}
}
