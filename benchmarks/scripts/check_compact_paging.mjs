import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import test from 'node:test';
import {EvidencePager,canonicalText} from './evidence-paging.mjs';

const root=new URL('../../',import.meta.url);
const policy=JSON.parse(readFileSync(new URL('benchmarks/config/evidence-paging-compact.json',root)));
const legacy=JSON.parse(readFileSync(new URL('benchmarks/config/evidence-paging.json',root)));
const hash=text=>createHash('sha256').update(text).digest('hex');
const text=result=>result.content.map(block=>block.text).join('\n');
const envelope=(normalized,raw=normalized)=>{
  const value={evidence_id:'fixture',operation:'analyze_function',subject:{digest:{sha256:'0'.repeat(64)}},
    raw_result:{value:raw},normalized_result:normalized,limitations:['Retained original metadata']};
  return {content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value};
};
function project(result,settings=policy){
  const pager=new EvidencePager(settings),response=pager.project(result),view=JSON.parse(text(response));
  assert(Array.from(text(response)).length<=settings.inline_characters);
  assert.deepEqual(response.structuredContent,view);
  return {pager,response,view};
}
function reassemble(pager,ref,pointer=''){
  let offset=0,complete='';
  do{
    const response=pager.read({evidence_ref:ref,pointer,offset,limit:24000});
    assert(Array.from(text(response)).length<=policy.inline_characters);
    const page=JSON.parse(text(response));
    assert.equal(page.offset,offset);
    complete+=page.text;offset=page.next_offset;
  }while(offset!==null);
  return complete;
}

test('short pseudocode is selected before a large root envelope',()=>{
  const pseudo='return value;';
  const result=envelope({pseudocode:pseudo,outgoing_references:'reference '.repeat(40000)});
  const {view}=project(result);
  assert.equal(view.primary_view.pointer,'/normalized_result/pseudocode');
  assert.equal(view.primary_view.text,pseudo);
  assert.equal(view.primary_view.complete,true);
  assert.equal(view.projection,'normalized_view_v1');
});
test('empty pseudocode remains an exact complete view',()=>{
  const {view}=project(envelope({pseudocode:'',assembly:'ret'}));
  assert.equal(view.primary_view.pointer,'/normalized_result/pseudocode');
  assert.equal(view.primary_view.text,'');
  assert.equal(view.primary_view.complete,true);
});
test('small raw and normalized duplicates appear once in model text',()=>{
  const pseudo='UNIQUE_SEMANTIC_FIXTURE_MARKER';
  const {pager,response,view}=project(envelope(pseudo));
  assert.equal(text(response).split(pseudo).length-1,1);
  assert.equal(view.primary_view.pointer,'/normalized_result');
  assert.equal(reassemble(pager,view.evidence_ref,'/raw_result/value'),pseudo);
});
test('different raw observations remain retrievable without inline duplication',()=>{
  const result=envelope('normalized observation','different raw observation');
  const {pager,response,view}=project(result);
  assert(!text(response).includes('different raw observation'));
  assert.equal(reassemble(pager,view.evidence_ref,'/raw_result/value'),'different raw observation');
  assert.equal(reassemble(pager,view.evidence_ref),canonicalText(result));
  assert.equal(view.original_sha256,hash(canonicalText(result)));
});
test('long Unicode and null-bearing views page and reconstruct byte exactly',()=>{
  const pseudo='前🦀\u0000終\\"\n'.repeat(9000),result=envelope({pseudocode:pseudo});
  const {pager,view}=project(result);
  assert.equal(view.primary_view.complete,false);
  assert.equal(reassemble(pager,view.evidence_ref,'/normalized_result/pseudocode'),pseudo);
  assert.equal(reassemble(pager,view.evidence_ref),canonicalText(result));
});
test('non-code normalized objects retain a bounded exact view and escaped pointers',()=>{
  const normalized={'a/b':{'~key':'A'.repeat(50000)},count:7};
  const {pager,view}=project(envelope(normalized));
  assert.equal(view.primary_view.pointer,'/normalized_result');
  assert.equal(reassemble(pager,view.evidence_ref,'/normalized_result/a~1b/~0key'),'A'.repeat(50000));
  assert.deepEqual(JSON.parse(reassemble(pager,view.evidence_ref,'/normalized_result')),normalized);
});
test('small non-REA tool responses are unchanged',()=>{
  const result={content:[{type:'text',text:'{"result":"opened"}'}]};
  assert.equal(new EvidencePager(policy).project(result),result);
});
test('unequal structured and textual responses keep both original representations',()=>{
  const result={content:[{type:'text',text:'original '.repeat(5000)}],structuredContent:{unique:'different'}};
  const {pager,view}=project(result);
  assert.equal(reassemble(pager,view.evidence_ref),canonicalText(result));
});
test('error status, case isolation and pointer bounds remain enforced',()=>{
  const result={...envelope({pseudocode:'partial'}),isError:true};
  const {pager,response,view}=project(result);
  assert.equal(response.isError,true);
  assert.throws(()=>new EvidencePager(policy).read({evidence_ref:view.evidence_ref}));
  assert.throws(()=>pager.read({evidence_ref:view.evidence_ref,pointer:'/__proto__/constructor'}));
  assert.throws(()=>pager.read({evidence_ref:view.evidence_ref,offset:-1}));
  assert.throws(()=>pager.read({evidence_ref:view.evidence_ref,limit:24001}));
});
test('historical v2 keeps its original inline and root-fallback interpretation',()=>{
  const small=envelope('unchanged');
  assert.equal(new EvidencePager(legacy).project(small),small);
  const large=envelope({pseudocode:'ret',refs:Array.from({length:20000},()=>({address:123}))});
  const {view}=project(large,legacy);
  assert.equal(view.primary_view.pointer,'');
  assert.equal(view.projection,undefined);
});
test('cached basic GCC-O0 analysis is bounded and still reconstructs exactly',t=>{
  const path=new URL('benchmarks/runs/native-pilot-20261019/conditions/lossless-evidence-v1/deepseek-flash/cpp--gcc13--O0/evidence-02-analyze_function.json',root);
  if(!existsSync(path)){t.skip('Optional historical cache absent');return;}
  const result=JSON.parse(readFileSync(path)),{pager,view}=project(result);
  assert.equal(view.primary_view.pointer,'/normalized_result/pseudocode');
  assert.equal(view.primary_view.text,result.structuredContent.normalized_result.pseudocode);
  assert.equal(reassemble(pager,view.evidence_ref),canonicalText(result));
});
test('nine cached Luna short helpers select pseudocode instead of root pages',t=>{
  const directory=new URL('benchmarks/runs/complex-pilot-common-budget-20261019/conditions/paged-evidence-v2/gpt-6-luna/cpp--gcc13--O0/',root);
  if(!existsSync(directory)){t.skip('Optional historical cache absent');return;}
  let delivered=0,pseudocode=0;
  for(const sequence of [10,13,18,25,26,27,29,31,32]){
    const result=JSON.parse(readFileSync(new URL(`evidence-${sequence}-analyze_function.json`,directory)));
    const {response,view}=project(result);
    assert.equal(view.primary_view.pointer,'/normalized_result/pseudocode');
    assert.equal(view.primary_view.complete,true);
    assert.equal(view.primary_view.text,result.structuredContent.normalized_result.pseudocode);
    delivered+=Array.from(text(response)).length;pseudocode+=Array.from(view.primary_view.text).length;
  }
  assert.equal(pseudocode,2256);
  assert(delivered<208520/2,'Compact delivery must remove the observed root-page overhead');
});
