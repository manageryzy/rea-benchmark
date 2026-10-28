// Provider selection is nonsecret action input. Credentials are read only into
// memory at launch; rotating a token does not create a new inference identity.
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {homedir} from 'node:os';
import {parse} from 'yaml';
import {isDeepStrictEqual} from 'node:util';
const root=fileURLToPath(new URL('../..',import.meta.url)).replace(/\/$/,'');
async function json(path){try{return JSON.parse(await readFile(path,'utf8'));}catch(error){if(error.code==='ENOENT')return {};throw new Error('Invalid provider credential configuration');}}
export function endpoint(value){
 let url;try{url=new URL(value);}catch{throw new Error('Invalid provider endpoint');}
 if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash)throw new Error('Provider endpoint must be HTTPS without credentials, query or fragment');
 return url.href.replace(/\/$/,'');
}
function publicEndpoint(value,secret){
 const result=endpoint(value);
 if(typeof secret==='string'&&secret&&(result.includes(secret)||decodeURIComponent(result).includes(secret)))
  throw new Error('Credentials must not be embedded in the provider endpoint');
 return result;
}
export async function resolveProvider(model,{env=process.env,readJSON=json,readYAML=async()=>{try{return parse(await readFile(`${root}/.harness/.credentials.yaml`,'utf8'));}catch{throw new Error('Invalid DSH credential configuration');}},policy}={}){
 policy??=await json(`${root}/benchmarks/config/runtime-attestation.json`);
 let provider,source,secret;
 if(model.harness==='zcode'){
  provider='bigmodel-standard-api';
  if(Boolean(env.GLM_AUTH_TOKEN)!==Boolean(env.GLM_BASE_URL))throw new Error('GLM environment requires both endpoint and token');
  if(env.GLM_AUTH_TOKEN&&env.GLM_BASE_URL){source='glm-environment';secret={baseUrl:env.GLM_BASE_URL,apiKey:env.GLM_AUTH_TOKEN};}
  else{source='zcode-prepaid';const config=await readJSON(`${root}/.zcode-state/.zcode/v2/provider_config.json`);secret=config.config?.providerConfigRules?.providerRules?.find(x=>x.providerId==='zai-prepaid')?.config;secret={baseUrl:secret?.api?.baseUrl,apiKey:secret?.access?.apiKey};}
  if(!secret?.apiKey||!secret.baseUrl)throw new Error('Missing GLM standard API credentials');
  return {plan:{schema_version:1,provider,credential_mode:'api-key',credential_source:source,model:model.id,protocol:'openai-chat-completions',base_url:publicEndpoint(secret.baseUrl,secret.apiKey)},secret:secret.apiKey};
 }
 if(model.id.startsWith('deepseek-')){
  provider='deepseek-official';
  if(env.DEEPSEEK_API_KEY){source='deepseek-environment';secret=env.DEEPSEEK_API_KEY;}
  else{source='dsh-credentials';secret=(await readYAML())?.refs?.DEEPSEEK_API_KEY;}
  if(!secret)throw new Error('Missing DeepSeek API credentials');
 }else if(model.id.startsWith('gpt-')){
  const preferred=policy.gpt_provider;
  if(preferred!==undefined&&!['openai','openai-codex'].includes(preferred))throw new Error('Invalid configured GPT provider');
  if(preferred!=='openai-codex'&&env.OPENAI_API_KEY){provider='openai';source='openai-environment';secret=env.OPENAI_API_KEY;}
  else{
   // The selected locator is frozen, not searched again with a fallback at launch.
   source=env.CODEX_HOME?'codex-home':'codex-user-home';
   const auth=await readJSON(`${env.CODEX_HOME??`${env.HOME??homedir()}/.codex`}/auth.json`);
   if(preferred!=='openai-codex'&&auth.OPENAI_API_KEY){provider='openai';secret=auth.OPENAI_API_KEY;}
   else if(preferred!=='openai'&&auth.tokens?.access_token){provider='openai-codex';secret=auth.tokens;}
   else throw new Error('Missing OpenAI API key or Codex sign-in');
  }
 }else throw new Error('No reviewed provider for this model');
 const defaults=policy.provider_defaults[provider];
 const override=provider==='deepseek-official'?env.DEEPSEEK_BASE_URL:provider==='openai'?env.OPENAI_BASE_URL:undefined;
 const plan={schema_version:1,provider,credential_mode:provider==='openai-codex'?'oauth':'api-key',credential_source:source,model:model.id,protocol:defaults.protocol,base_url:publicEndpoint(override??defaults.base_url,secret)};
 return {plan,secret};
}
export async function credentialsFor(plan,options={}){
 const resolved=await resolveProvider({id:plan.model,harness:plan.provider==='bigmodel-standard-api'?'zcode':'deepseek-harness'},options);
 if(!isDeepStrictEqual(resolved.plan,plan))throw new Error('Provider selection changed after preparation; no fallback permitted');
 if(plan.credential_mode==='oauth'){
  let claims;try{claims=JSON.parse(Buffer.from(resolved.secret.access_token.split('.')[1],'base64url'));}catch{throw new Error('Invalid Codex sign-in');}
  if(!Number.isFinite(claims.exp)||claims.exp*1000<Date.now()+3600000)throw new Error('Codex sign-in expires too soon; refresh before benchmarking');
  return {version:1,refs:{},records:{'llm-pi-ai/openai-codex':{kind:'grant',payload:{type:'oauth',access:resolved.secret.access_token,refresh:resolved.secret.refresh_token??'',expires:claims.exp*1000}}}};
 }
 const name=plan.provider==='deepseek-official'?'DEEPSEEK_API_KEY':'OPENAI_API_KEY';
 return {version:1,refs:{[name]:resolved.secret},records:{}};
}
if(process.argv[2]==='--resolve'){
 try{const model=JSON.parse(process.argv[3]);process.stdout.write(JSON.stringify((await resolveProvider(model)).plan));}
 catch{process.stderr.write('Provider resolution failed: check the configured credential source and HTTPS endpoint.\n');process.exitCode=1;}
}
