import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {EvidencePager,canonicalText,evidenceTool} from './evidence-paging.mjs';
const input=JSON.parse(await readFile(process.argv[2],'utf8'));
const policy=JSON.parse(await readFile(input.root+'/benchmarks/config/evidence-paging.json','utf8'));
const sha=s=>createHash('sha256').update(s).digest('hex');
let paged=0,total=0,largest=0,maxProjection=0,maxPage=0;
for(const path of input.evidence){
  const result=JSON.parse(await readFile(input.root+'/'+path,'utf8'));
  const pager=new EvidencePager(policy),text=canonicalText(result),projected=pager.project(result);
  largest=Math.max(largest,Buffer.byteLength(text));total++;
  const modelText=projected.content.filter(b=>b.type==='text').map(b=>b.text).join('\n');
  if(projected===result){assert.equal(modelText,text);continue;}
  paged++;maxProjection=Math.max(maxProjection,Array.from(modelText).length);
  const doc=JSON.parse(modelText);assert.equal(doc.original_sha256,sha(text));
  assert.equal(doc.evidence_ref,'sha256:'+sha(text));
  assert(Array.from(modelText).length<=policy.inline_characters);
  let offset=0,assembled='';
  do{
    const page=pager.read({evidence_ref:doc.evidence_ref,offset,limit:policy.maximum_page_characters});
    assert(Array.from(page.content[0].text).length<=policy.inline_characters);
    maxPage=Math.max(maxPage,Array.from(page.content[0].text).length);
    const view=JSON.parse(page.content[0].text);assembled+=view.text;offset=view.next_offset;
  }while(offset!==null);
  assert.equal(assembled,text,path+': original evidence changed after reassembly');
  for(const view of doc.available_views.slice(0,2)){
    const page=JSON.parse(pager.read({evidence_ref:doc.evidence_ref,pointer:view.pointer,limit:policy.maximum_page_characters}).content[0].text);
    assert.equal(page.view_sha256,view.view_sha256);
  }
  assert.throws(()=>pager.read({evidence_ref:doc.evidence_ref,pointer:'/__proto__/constructor'}));
}
const synthetic=[
 {content:[{type:'text',text:'前🦀\u0000終'.repeat(9000)}]},
 {content:[{type:'text',text:JSON.stringify({'a/b':{'~key':'x'.repeat(40000)}})}]},
 {content:[{type:'text',text:'original'.repeat(5000)}],structuredContent:{unique:'DO_NOT_DROP'}},
 {isError:true,content:[{type:'text',text:'错误'.repeat(30000)}]},
];
for(const result of synthetic){
 const pager=new EvidencePager(policy),expected=canonicalText(result),projection=pager.project(result),ref=JSON.parse(projection.content[0].text).evidence_ref;
 let assembled='',offset=0;
 do{const response=pager.read({evidence_ref:ref,offset,limit:24000});assert(Array.from(response.content[0].text).length<=policy.inline_characters);const page=JSON.parse(response.content[0].text);assembled+=page.text;offset=page.next_offset;}while(offset!==null);
 assert.equal(assembled,expected);
 assert.equal(!!projection.isError,!!result.isError);
 assert.throws(()=>new EvidencePager(policy).read({evidence_ref:ref}),'Reference escaped its case');
 assert.throws(()=>pager.read({evidence_ref:ref,offset:-1}));
 assert.throws(()=>pager.read({evidence_ref:ref,limit:24001}));
 if(expected.includes('a/b'))assert(JSON.parse(pager.read({evidence_ref:ref,pointer:'/a~1b/~0key'}).content[0].text).text.startsWith('xxx'));
}
// Prepare three real-client steps from the provider-rejected cached result.
const raw=JSON.parse(await readFile(input.root+'/'+input.smoke_source,'utf8'));
const pager=new EvidencePager(policy),projection=pager.project(raw),doc=JSON.parse(projection.content[0].text);
assert.equal(doc.delivery,'explicit_lossless_paging');
const outputs=[projection,pager.read({evidence_ref:doc.evidence_ref,pointer:doc.primary_view.pointer,limit:24000}),pager.read({evidence_ref:doc.evidence_ref,offset:0,limit:24000})];
const steps=[{tool:'analyze_function',arguments:input.smoke_arguments,result_path:input.smoke_source},
 {tool:'read_evidence',arguments:{evidence_ref:doc.evidence_ref,pointer:doc.primary_view.pointer,limit:24000},result_path:input.smoke_source},
 {tool:'read_evidence',arguments:{evidence_ref:doc.evidence_ref,offset:0,limit:24000},result_path:input.smoke_source}];
for(let i=0;i<outputs.length;i++){steps[i].expected_result_path=input.temporary+'/expected-'+i+'.json';await writeFile(steps[i].expected_result_path,JSON.stringify(outputs[i]));}
await writeFile(input.output,JSON.stringify({passed:true,model_api_calls:0,results:total,paged_results:paged,exact_reassemblies:paged,largest_original_bytes:largest,max_projection_characters:maxProjection,max_page_characters:maxPage,synthetic_cases:synthetic.length,steps,expected_model_sha256:outputs.map(r=>sha(r.content[0].text)),extra_tool:evidenceTool.name,primary_view_complete:doc.primary_view.complete})+'\n');
console.log(`PASS: ${total} cached results; ${paged} paged originals reassemble exactly; all model pages <= ${policy.inline_characters} characters; zero model calls`);
