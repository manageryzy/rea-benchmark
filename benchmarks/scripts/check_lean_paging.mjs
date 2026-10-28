import assert from 'node:assert/strict';
import {readFileSync,readdirSync,writeFileSync,existsSync} from 'node:fs';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {EvidencePager,canonicalText} from './evidence-paging.mjs';
const root=new URL('../../',import.meta.url);
const policy=JSON.parse(readFileSync(new URL('benchmarks/config/evidence-paging-lean.json',root)));
const old=JSON.parse(readFileSync(new URL('benchmarks/config/evidence-paging-compact.json',root)));
const wrap=(n,operation='analyze_function')=>{
 const v={evidence_id:'fixture',operation,normalized_result:n,raw_result:{value:n},limitations:['fixture']};
 return {content:[{type:'text',text:JSON.stringify(v)}],structuredContent:v};
};
const value=r=>JSON.parse(r.content[0].text);
function read(p,ref,pointer=''){
 let offset=0,text='';do{const v=value(p.read({evidence_ref:ref,pointer,offset,limit:24000}));text+=v.text;offset=v.next_offset;}while(offset!==null);return text;
}
test('cross-tool exact views reference prior complete evidence and remain retrievable',()=>{
 const p=new EvidencePager(policy),pseudo='return x;'.repeat(800),a=value(p.project(wrap({pseudocode:pseudo}))),original=wrap(pseudo,'procedure_pseudo_code'),b=value(p.project(original));
 assert.equal(b.already_provided.evidence_ref,a.evidence_ref);
 assert(!JSON.stringify(b).includes(pseudo));
 assert.equal(read(p,b.evidence_ref,'/normalized_result'),pseudo);
 assert.equal(read(p,b.evidence_ref),canonicalText(original));
});
test('different content and incomplete pages are never suppressed',()=>{
 const p=new EvidencePager(policy),n='前🦀\u0000"\\\n'.repeat(12000);
 const a=value(p.project(wrap({pseudocode:n}))),b=value(p.project(wrap(n)));
 assert(a.primary_view.next_offset!==null);assert(b.primary_view);
 assert.equal(read(p,a.evidence_ref,a.primary_view.pointer),n);
 assert(value(p.project(wrap(n+'changed'))).primary_view);
});
test('case isolation, original metadata, error status and bounds survive',()=>{
 const p=new EvidencePager(policy),r={...wrap({pseudocode:''}),isError:true},out=p.project(r),v=value(out);
 assert(out.isError);assert.equal(v.primary_view.text,'');
 assert.deepEqual(JSON.parse(read(p,v.evidence_ref)),r.structuredContent);
 assert.throws(()=>new EvidencePager(policy).read({evidence_ref:v.evidence_ref}));
 assert.throws(()=>p.read({evidence_ref:v.evidence_ref,pointer:'/__proto__'}));
 assert.throws(()=>p.read({evidence_ref:v.evidence_ref,limit:24001}));
});
test('22 cached attempts replay losslessly with smaller projections',t=>{
 const url=new URL('.cache/benchmark-runtime/token-trace-audit-20261026/attempt-inventory.json',root);
 let inventory;try{inventory=JSON.parse(readFileSync(url));}catch{t.skip('Private audit caches unavailable');return;}
 const rows=[];
 for(const attempt of inventory.attempts){
  const dir=new URL(attempt.directory+'/',root),p=new EvidencePager(policy),previous=new EvidencePager(old),refs=new Map();
  const calls=new Map((existsSync(new URL('tool-calls.jsonl',dir))?readFileSync(new URL('tool-calls.jsonl',dir),'utf8'):'').trim().split('\n').filter(Boolean).map(line=>{const c=JSON.parse(line);return [c.sequence,c];}));
  let before=0,after=0,duplicates=0,views=0;
  for(const name of readdirSync(dir).filter(n=>/^evidence-\d+-/.test(n)).sort((a,b)=>Number(a.split('-')[1])-Number(b.split('-')[1]))){
   const original=JSON.parse(readFileSync(new URL(name,dir)));
   const a=JSON.parse(readFileSync(new URL('model-'+name,dir)));
   let b;
   if(name.endsWith('-read_evidence.json')){
    const args=calls.get(Number(name.split('-')[1])).arguments;
    if(refs.has(args.evidence_ref))b=p.read({...args,evidence_ref:refs.get(args.evidence_ref)});
    else b=original;
   }else b=p.project(original);
   before+=Array.from(a.content.map(x=>x.text??'').join('\n')).length;
   after+=Array.from(b.content.map(x=>x.text??'').join('\n')).length;
   let v;try{v=value(b);}catch{assert.equal(b,original);continue;}
   if(v.evidence_ref&&!name.endsWith('-read_evidence.json')){
    assert.equal(read(p,v.evidence_ref),canonicalText(original));views++;
    refs.set('sha256:'+createHash('sha256').update(canonicalText(original)).digest('hex'),v.evidence_ref);
   }
   if(v.already_provided)duplicates++;
  }
  rows.push({model:attempt.model,case:attempt.case,before_characters:before,after_characters:after,duplicates,views});
 }
 const sum=k=>rows.reduce((n,r)=>n+r[k],0),summary={attempts:rows.length,before_characters:sum('before_characters'),after_characters:sum('after_characters'),duplicates:sum('duplicates'),exact_reassemblies:sum('views'),rows};
 assert(summary.after_characters<summary.before_characters);assert(summary.duplicates>0);
 writeFileSync(new URL('.cache/benchmark-runtime/wallet-evidence-repair/paging-replay.json',root),JSON.stringify(summary,null,2));
 console.log(JSON.stringify({...summary,rows:undefined}));
});
