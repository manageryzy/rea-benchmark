import {createHash} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';

export const evidenceTool={name:'read_evidence',description:'Read a complete, previously produced REA result by its evidence_ref. Large results include a view catalog of JSON pointers. A pointer selects an observed field (pseudocode strings are returned directly); offset and limit page its Unicode characters. Use limit=24000 for the largest allowed page and follow next_offset until complete. An already_provided reference means that exact view was fully delivered earlier; reuse it, or explicitly read it again if needed. This reads only this case’s saved evidence, never source files or arbitrary paths.',annotations:{readOnlyHint:true,destructiveHint:false,idempotentHint:true},outputSchema:{type:'object',additionalProperties:true},inputSchema:{type:'object',properties:{evidence_ref:{type:'string'},pointer:{type:'string',description:'RFC 6901 JSON pointer from available_views; empty means the original full text'},offset:{type:'integer',minimum:0},limit:{type:'integer',minimum:1,maximum:24000}},required:['evidence_ref'],additionalProperties:false}};
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
  constructor(policy){this.policy=policy;this.entries=new Map();this.delivered=new Map();}
  get lean(){return this.policy.id==='lean-evidence-v4';}
  async recover(result,call,{targetSha256,procedure,timeoutMs=60000,audit=async()=>{}}){
    if(!this.lean||!result.isError)return result;
    let error;try{error=JSON.parse(canonicalText(result)).error;}catch{return result;}
    const limits=error?.details?.reported_limits,continuation=limits?.continuation;
    if(error?.code!=='resource_constraint'||error.details?.operation!=='analyze_function'||
       error.details?.resource!=='transport'||limits?.boundary!=='mcp-response')return result;
    const source=continuation?.arguments?.source,id=limits.evidence_id;
    if(continuation?.name!=='inspect_analysis_view'||source?.kind!=='retained-evidence'||
       !/^ev_[a-f0-9]{64}$/.test(id??'')||source.evidence_id!==id||
       limits.evidence_reference?.evidence_id!==id||limits.evidence_reference?.kind!=='retained-evidence'||
       continuation.arguments.view?.kind!=='native'||continuation.arguments.view?.facet!=='pseudocode')
      throw new Error('Invalid retained analysis continuation');
    const recoveryStarted=performance.now(),pages=[];let offset=0,total=null,text='';
    const fetch=async(facet,offset,limit,timeout=timeoutMs)=>{
      if(!(timeout>0))throw new Error('Retained evidence retrieval deadline reached');
      const arguments_={source:{kind:'retained-evidence',evidence_id:id},view:{kind:'native',facet,offset,limit}};
      const started=performance.now(),response=await call({name:'inspect_analysis_view',arguments:arguments_},{timeout});
      await audit({arguments:arguments_,elapsed_ms:performance.now()-started,response});
      if(response.isError)throw new Error('Retained analysis view failed');
      const document=JSON.parse(canonicalText(response)),v=document.normalized_result;
      if(document.operation!=='inspect_analysis_view'||v?.parent_evidence_id!==id||v.parent_operation!=='analyze_function'||
         v.artifact?.sha256!==targetSha256||v.kind!=='native'||v.view?.facet!==facet||v.view?.offset!==offset||
         !document.evidence_links?.includes(id)||
         (/^0x[a-f0-9]+$/i.test(procedure??'')&&BigInt(v.procedure_address)!==BigInt(procedure)))
        throw new Error('Retained analysis identity mismatch');
      const c=v.coverage;
      if(!Number.isSafeInteger(c?.total)||c.total<0||!Number.isSafeInteger(c.examined)||c.examined<0||
         (c.next_offset!==null&&(!Number.isSafeInteger(c.next_offset)||c.next_offset<=offset||c.next_offset>c.total)))
        throw new Error('Invalid retained analysis coverage');
      return {document,v};
    };
    do{
      const {document,v}=await fetch('pseudocode',offset,16384,timeoutMs-(performance.now()-recoveryStarted)),c=v.coverage;
      if(v.item?.unit!=='utf16-code-units'||typeof v.item.text!=='string'||
         v.item.text.length!==c.examined||(total!==null&&total!==c.total)||
         (c.next_offset??c.total)!==offset+c.examined||c.total>64*1024*1024)
        throw new Error('Incomplete retained pseudocode coverage');
      total=c.total;text+=v.item.text;pages.push(document);offset=c.next_offset;
    }while(offset!==null);
    const document={evidence_id:id,operation:'analyze_function',
      normalized_result:{pseudocode:text},raw_result:{transport_error:JSON.parse(canonicalText(result)),recovery_pages:pages},
      limitations:['Transport recovery: pseudocode is complete; the full dossier remains in REA. Other retained facets are available through /retained/<facet>.'],
      recovery:{kind:'retained-native-view',parent_evidence_id:id,artifact_sha256:targetSha256}};
    // This is a labelled derived delivery, never a fabricated full dossier.
    const recovered={content:[{type:'text',text:JSON.stringify(document)}],structuredContent:document};
    const projected=this.project(recovered),ref=projected.structuredContent.evidence_ref;
    const entry=this.entries.get(ref);entry.remote=fetch;
    return projected;
  }
  async readRetained(args){
    const entry=this.entries.get(args.evidence_ref);
    if(!entry?.remote||!args.pointer?.startsWith('/retained/'))return null;
    if(Object.keys(args).some(k=>!['evidence_ref','pointer','offset','limit'].includes(k)))throw new Error('Unknown evidence page parameter');
    const facet=args.pointer.slice('/retained/'.length);
    if(!['assembly','procedure','callers','callees','incoming_references','outgoing_references'].includes(facet))throw new Error('Unapproved retained facet');
    const offset=args.offset??0,limit=Math.min(args.limit??32,32);
    if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(limit)||limit<1)throw new Error('Invalid retained page');
    const {v}=await entry.remote(facet,offset,limit);
    const value={evidence_ref:args.evidence_ref,pointer:args.pointer,offset,next_offset:v.coverage.next_offset,
      unit:'retained-facet-rows',item:v.item,coverage:v.coverage,limitations:v.limitations,unknowns:v.unknowns};
    // Preserve the bounded page as local evidence if a row itself is large.
    return this.project({content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value});
  }
  leanPage(page){
    return {evidence_ref:page.evidence_ref,pointer:page.pointer,offset:page.offset,
      next_offset:page.next_offset,text:page.text};
  }
  remember(entry,pointer,page){
    // Only a fully delivered view may replace a later cross-tool response.
    // Hash lookup is followed by equality; incomplete pages never deduplicate.
    if(page.offset===0&&page.complete){
      const key=sha(page.text),prior=this.delivered.get(key)??[];
      if(!prior.some(p=>p.text===page.text))prior.push({text:page.text,evidence_ref:entry.ref,pointer});
      this.delivered.set(key,prior);
    }
  }
  leanProject(result,entry,compact){
    const normalized=entry.document?.normalized_result;
    const keys=compact&&normalized&&typeof normalized==='object'&&!Array.isArray(normalized)?Object.keys(normalized):[];
    const primary=keys.find(k=>k==='pseudocode'||k==='pseudo_code');
    const pointer=compact?'/normalized_result'+(primary?'/'+primary:''):'';
    let limit=this.policy.maximum_page_characters,page,value;
    do{
      page=this.page(entry,pointer,0,limit);
      const previous=page.complete?this.delivered.get(sha(page.text))?.find(p=>p.text===page.text):null;
      value={evidence_ref:entry.ref,operation:entry.document?.operation??null,
        ...(previous?{already_provided:{evidence_ref:previous.evidence_ref,pointer:previous.pointer}}:
          {primary_view:this.leanPage(page)}),
        // Catalogs contain pointers only. Full metadata, provenance and raw
        // output remain in the exact original, addressable with pointer="".
        available_views:['',...(compact?['/raw_result','/normalized_result']:[]),
          ...(entry.document?.recovery?.kind==='retained-native-view'?['/retained/assembly','/retained/procedure','/retained/callers','/retained/callees','/retained/incoming_references','/retained/outgoing_references']:[]),
          ...keys.slice(0,this.policy.catalog_entries).map(k=>'/normalized_result/'+k.replaceAll('~','~0').replaceAll('/','~1'))],
        ...(keys.length>this.policy.catalog_entries?{view_catalog_truncated:true}:{}),
        ...(entry.document?.limitations?{limitations:entry.document.limitations}:{})};
      const excess=chars(JSON.stringify(value)).length-this.policy.inline_characters;
      if(excess<=0)break;
      // Very large metadata is retained rather than silently discarded.
      if(value.limitations){delete value.limitations;value.limitations_pointer='/limitations';}
      const remaining=chars(JSON.stringify(value)).length-this.policy.inline_characters;
      if(remaining<=0)break;
      if(limit<=1)throw new Error('Evidence metadata exceeds page budget');
      limit=Math.max(1,limit-remaining-128);
    }while(true);
    if(!value.already_provided)this.remember(entry,pointer,page);
    return {...result,content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value};
  }
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
      if(this.lean)value=this.leanPage(value);
      const size=chars(JSON.stringify(value)).length;
      if(size<=this.policy.inline_characters)break;
      if(limit<=1)throw new Error('Evidence metadata exceeds page budget');
      limit=Math.max(1,Math.floor(limit*this.policy.inline_characters/size)-128);
    }while(true);
    return {content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value};
  }
  project(result){
    const text=canonicalText(result),points=chars(text);
    const document=this.document(text);
    // A semantic view is an exact field of a retained REA envelope, not a
    // generated summary. The historical v2 policy keeps its original behavior.
    const compact=this.policy.compact_semantic_views===true&&document&&typeof document==='object'&&
      typeof document.evidence_id==='string'&&Object.hasOwn(document,'normalized_result')&&Object.hasOwn(document,'raw_result');
    if(!compact&&points.length<=this.policy.inline_characters){
      const primary=(result.content??[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
      if(primary===text)return result;
      return {...result,content:[{type:'text',text}],structuredContent:JSON.parse(text)};
    }
    const ref=this.lean?'e'+(this.entries.size+1):'sha256:'+sha(text),entry={text,ref,document};
    this.entries.set(ref,entry);
    if(this.lean)return this.leanProject(result,entry,compact);
    const views=[];
    const visit=(value,path='',depth=0)=>{
      if(depth>30)return;
      if(typeof value==='string'&&value.length>=512)views.push({pointer:path,characters:chars(value).length,view_sha256:sha(value)});
      else if(Array.isArray(value))value.forEach((v,i)=>visit(v,path+'/'+i,depth+1));
      else if(value&&typeof value==='object')for(const[k,v]of Object.entries(value))visit(v,path+'/'+k.replaceAll('~','~0').replaceAll('/','~1'),depth+1);
    };
    if(compact){
      // Do not inline both the raw and normalized copies. Every original field
      // remains addressable through read_evidence, including omitted metadata.
      const add=(pointer,value)=>{
        const view=typeof value==='string'?value:JSON.stringify(value);
        views.push({pointer,characters:chars(view).length,view_sha256:sha(view)});
      };
      const normalized=document.normalized_result;
      add('/normalized_result',normalized);
      if(normalized&&typeof normalized==='object'&&!Array.isArray(normalized)){
        for(const[key,value]of Object.entries(normalized))
          add('/normalized_result/'+key.replaceAll('~','~0').replaceAll('/','~1'),value);
      }
    }else visit(entry.document);
    const priority=v=>(/\/pseudocode$|\/pseudo_code$/.test(v.pointer)?0:/assembly|instructions|code|text/.test(v.pointer)?1:2);
    // Short and empty pseudocode is still a complete, useful semantic view.
    // The v2 >=512-character catalog filter must not select root JSON instead.
    views.sort((a,b)=>priority(a)-priority(b)||
      (compact?(a.pointer==='/normalized_result'?-1:b.pointer==='/normalized_result'?1:0):0)||a.pointer.localeCompare(b.pointer));
    const selected=views[0]?.pointer??'';
    let pageLimit=this.policy.maximum_page_characters,catalog=views.slice(0,this.policy.catalog_entries);
    let value;
    do{
      value={delivery:'explicit_lossless_paging',evidence_ref:ref,source_evidence_id:entry.document?.evidence_id??null,source_operation:entry.document?.operation??null,
        ...(compact?{projection:'normalized_view_v1'}:{}),
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
