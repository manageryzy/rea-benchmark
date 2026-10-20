// Add the requested model route while retaining existing settings and secrets.
import {readFile,writeFile,mkdir,chmod,copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {parse,stringify} from 'yaml';
const root=fileURLToPath(new URL('..',import.meta.url)).replace(/\/$/,'');
const stamp=new Date().toISOString().replace(/[:.]/g,'-');
const credentialPath=`${root}/.harness/.credentials.yaml`;
const auth=JSON.parse(await readFile('/mnt/c/Users/mana/.codex/auth.json','utf8'));
const saved=parse(await readFile(credentialPath,'utf8'));
saved.refs??={};saved.records??={};
let provider='openai-codex';
if(process.env.OPENAI_API_KEY||auth.OPENAI_API_KEY){
 provider='openai';saved.refs.OPENAI_API_KEY=process.env.OPENAI_API_KEY??auth.OPENAI_API_KEY;
}else{
 const t=auth.tokens??{};
 if(!t.access_token)throw new Error('No existing Codex sign-in');
 const claims=JSON.parse(Buffer.from(t.access_token.split('.')[1],'base64url').toString());
 if(claims.exp*1000<Date.now()+3600000)throw new Error('Codex sign-in must be refreshed before import');
 saved.records['llm-pi-ai/openai-codex']={kind:'grant',payload:{type:'oauth',access:t.access_token,refresh:t.refresh_token??'',expires:claims.exp*1000}};
}
const backup=`${root}/.cache/backups/models-${stamp}`;
await mkdir(backup,{recursive:true,mode:0o700});await chmod(backup,0o700);
await copyFile(credentialPath,`${backup}/credentials.yaml`);await chmod(`${backup}/credentials.yaml`,0o600);
await writeFile(credentialPath,stringify(saved),{mode:0o600});await chmod(credentialPath,0o600);
for(const relative of ['config/cordis.patch.yml','.harness/cordis.patch.yml']){
 const path=`${root}/${relative}`;
 const entries=parse(await readFile(path,'utf8'));
 let entry=entries.find(x=>x.id==='llm-pi-ai');
 if(!entry){entry={id:'llm-pi-ai',config:{providers:{}}};entries.push(entry);}
 entry.config??={};entry.config.providers??={};
 const previous=entry.config.providers[provider]??{};
 entry.config.providers[provider]={...previous,reasoning:'high',...(provider==='openai'?{apiKeyEnv:'OPENAI_API_KEY',api:'openai-responses'}:{}),models:[...(previous.models??[]).filter(m=>m.id!=='gpt-6.1-sol'),{id:'gpt-6.1-sol',name:'GPT-6.1 Sol',contextWindow:1050000,maxTokens:8192,reasoningEfforts:{low:'low',medium:'medium',high:'high',xhigh:'xhigh',max:'max'}}]};
 await copyFile(path,`${backup}/${relative.replaceAll('/','-')}`);
 await writeFile(path,stringify(entries));
}
console.log(JSON.stringify({configured:true,provider,model:'gpt-6.1-sol',secrets:'saved privately; existing values preserved'}));
