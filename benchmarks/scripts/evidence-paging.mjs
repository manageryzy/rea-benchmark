import {createHash} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';

export const evidenceTool={name:'read_evidence',description:'Read a complete, previously produced REA result by its evidence_ref. Large results include a view catalog of JSON pointers. A pointer selects an observed field (pseudocode strings are returned directly); offset and limit page its Unicode characters. Use limit=24000 for the largest allowed page and follow next_offset until complete. This reads only this case’s saved evidence, never source files or arbitrary paths.',annotations:{readOnlyHint:true,destructiveHint:false,idempotentHint:true},outputSchema:{type:'object',additionalProperties:true},inputSchema:{type:'object',properties:{evidence_ref:{type:'string'},pointer:{type:'string',description:'RFC 6901 JSON pointer from available_views; empty means the original full text'},offset:{type:'integer',minimum:0},limit:{type:'integer',minimum:1,maximum:24000}},required:['evidence_ref'],additionalProperties:false}};
const sha=text=>createHash('sha256').update(text).digest('hex');
const chars=text=>Array.from(text);

export function canonicalText(result){
  const blocks=result.content??[];
  const primary=blocks.filter(b=>b.type==='text').map(b=>b.text).join('\n');
  if(blocks.some(b=>b.type!=='text'))throw new Error('Evidence paging only supports textual REA results');
  if(result.structuredContent!==undefined){
    let equal=false;
    try{equal=isDeepStrictEqual(JSON.parse(primary),result.structuredContent);}catch{}
    if(!equal)return JSON.stringify({content:blocks,structuredContent:result.structuredContent});
  }
  return primary;
}

export class EvidencePager{
  constructor(policy){this.policy=policy;this.entries=new Map();}
  document(text){try{return JSON.parse(text);}catch{return undefined;}}
  page(entry,pointer='',offset=0,limit=this.policy.default_page_characters){
    if(typeof pointer!=='string'||!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(limit)||limit<1||limit>this.policy.maximum_page_characters)throw new Error('Invalid evidence page: offset/limit must be bounded integers');
    let text=entry.text;
    if(pointer){
      if(!pointer.startsWith('/'))throw new Error('Evidence pointer must be an RFC 6901 JSON pointer');
      let value=entry.document;
      for(const token of pointer.slice(1).split('/')){
        if(/~(?:[^01]|$)/.test(token))throw new Error('Invalid JSON pointer escape');
        const key=token.replaceAll('~1','/').replaceAll('~0','~');
        if(!value||typeof value!=='object'||!Object.hasOwn(value,key))throw new Error('Unknown evidence pointer');
        value=value[key];
      }
      text=typeof value==='string'?value:JSON.stringify(value);
    }
    entry.views??=new Map();
    let cached=entry.views.get(pointer);
    if(!cached){cached={points:chars(text),hash:sha(text)};entry.views.set(pointer,cached);}
    const points=cached.points;
    if(offset>points.length)throw new Error('Evidence offset exceeds selected view');
    const end=Math.min(points.length,offset+limit);
    return {evidence_ref:entry.ref,source_evidence_id:entry.document?.evidence_id??null,pointer,offset,end_offset:end,total_characters:points.length,view_sha256:cached.hash,text:points.slice(offset,end).join(''),complete:end===points.length,next_offset:end<points.length?end:null};
  }
  read(args){
    if(!args||Object.keys(args).some(k=>!['evidence_ref','pointer','offset','limit'].includes(k)))throw new Error('Unknown evidence page parameter');
    const entry=this.entries.get(args.evidence_ref);
    if(!entry)throw new Error('Evidence reference is unavailable in this case');
    let limit=args.limit??this.policy.default_page_characters,value;
    do{
      value=this.page(entry,args.pointer??'',args.offset??0,limit);
      const size=chars(JSON.stringify(value)).length;
      if(size<=this.policy.inline_characters)break;
      if(limit<=1)throw new Error('Evidence metadata exceeds page budget');
      limit=Math.max(1,Math.floor(limit*this.policy.inline_characters/size)-128);
    }while(true);
    return {content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value};
  }
  project(result){
    const text=canonicalText(result),points=chars(text);
    if(points.length<=this.policy.inline_characters){
      const primary=(result.content??[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
      if(primary===text)return result;
      return {...result,content:[{type:'text',text}],structuredContent:JSON.parse(text)};
    }
    const ref='sha256:'+sha(text),entry={text,ref,document:this.document(text)};
    this.entries.set(ref,entry);
    const views=[];
    const visit=(value,path='',depth=0)=>{
      if(depth>30)return;
      if(typeof value==='string'&&value.length>=512)views.push({pointer:path,characters:chars(value).length,view_sha256:sha(value)});
      else if(Array.isArray(value))value.forEach((v,i)=>visit(v,path+'/'+i,depth+1));
      else if(value&&typeof value==='object')for(const[k,v]of Object.entries(value))visit(v,path+'/'+k.replaceAll('~','~0').replaceAll('/','~1'),depth+1);
    };
    visit(entry.document);
    const priority=v=>(/\/pseudocode$|\/pseudo_code$/.test(v.pointer)?0:/assembly|instructions|code|text/.test(v.pointer)?1:2);
    views.sort((a,b)=>priority(a)-priority(b)||a.pointer.localeCompare(b.pointer));
    const selected=views[0]?.pointer??'';
    let pageLimit=this.policy.maximum_page_characters,catalog=views.slice(0,this.policy.catalog_entries);
    let value;
    do{
      value={delivery:'explicit_lossless_paging',evidence_ref:ref,source_evidence_id:entry.document?.evidence_id??null,source_operation:entry.document?.operation??null,
        source_target_sha256:entry.document?.subject?.digest?.sha256??null,original_utf8_bytes:Buffer.byteLength(text),original_characters:points.length,original_sha256:sha(text),
        root_fields:entry.document&&typeof entry.document==='object'?Object.keys(entry.document):[],available_views:catalog,view_count:views.length,
        primary_view:this.page(entry,selected,0,pageLimit),
        retrieval:'The original result is retained verbatim. Use read_evidence with this evidence_ref, pointer and next_offset; empty pointer pages the complete original text. No native filesystem tool is needed.'};
      const excess=chars(JSON.stringify(value)).length-this.policy.inline_characters;
      if(excess<=0)break;
      if(pageLimit>512)pageLimit=Math.max(512,pageLimit-excess-256);
      else if(catalog.length)catalog=catalog.slice(0,Math.floor(catalog.length/2));
      else throw new Error('Evidence reference metadata exceeds the explicit page budget');
    }while(true);
    return {...result,content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value};
  }
}
