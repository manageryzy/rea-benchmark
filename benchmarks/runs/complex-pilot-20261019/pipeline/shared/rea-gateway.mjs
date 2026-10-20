// A benchmark-only MCP surface. No filesystem, execution, mutation, or source tools.
import { Server } from '@modelcontextprotocol/server';
import { StdioServerTransport } from '@modelcontextprotocol/server/stdio';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { readFile,writeFile,appendFile,mkdir } from 'node:fs/promises';
import { resolve,dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const root=fileURLToPath(new URL('../..',import.meta.url)).replace(/\/$/,'');
const target=resolve(process.argv[2]);const trace=resolve(process.argv[3]);
const config=JSON.parse(await readFile(process.argv[4]??`${root}/benchmarks/config/pilot.json`,'utf8'));
const catalog=JSON.parse(await readFile(`${root}/benchmarks/config/rea-tools.json`,'utf8'));
const names=new Set(catalog.tools.map(t=>t.name));
let backend,backendTransport,connecting,opened=false,calls=0,closing;
async function connectBackend(){
 if(backend)return backend;
 if(!connecting)connecting=(async()=>{
  const c=new Client({name:'benchmark-analysis-gateway',version:'1'});
  backendTransport=new StdioClientTransport({command:`${root}/bin/rea`,args:['mcp'],stderr:'inherit',env:{PATH:process.env.PATH??'/usr/bin:/bin',REA_ANALYSIS_PROVIDER:'ghidra',REA_LOG_LEVEL:'error',REA_MCP_INPUT_SCHEMA_PROFILE:'compact',GHIDRA_HEADLESS_MAXMEM:'2G'}});
  await c.connect(backendTransport,{timeout:60000});backend=c;return c;
 })();
 return connecting;
}
await mkdir(dirname(trace),{recursive:true});
const server=new Server({name:'rea-benchmark',version:'1'},{capabilities:{tools:{}},instructions:'Analyze only the selected Linux ELF. Tools forward to REA 6.7.0 and Ghidra 12.1.4. Source, hidden tests, other targets, execution and mutations are unavailable.'});
server.setRequestHandler('tools/list',async()=>({tools:catalog.tools}));
server.setRequestHandler('tools/call',async(request)=>{
 const start=performance.now();const {name,arguments:raw={}}=request.params;const args={...raw};
 const sequence=++calls;let result;
 try {
  if(!names.has(name))throw new Error('Tool is outside the benchmark analysis allowlist');
  if(sequence>config.max_tool_calls)throw new Error('Benchmark tool budget exhausted');
  if(args.document!==undefined)throw new Error('Document overrides are unavailable in this benchmark');
  if(name==='open_binary'){
   const selected=resolve(dirname(target),args.path??'target');
   if(selected!==target)throw new Error('Only the selected benchmark target may be opened');
   for(const k of Object.keys(args))if(!['path','provider_id'].includes(k))throw new Error('Loader and snapshot overrides are unavailable');
   args.path=target;args.provider_id='ghidra';
  }
  if(name==='close_binary'&&Object.keys(args).length)throw new Error('Snapshot writes are unavailable');
  const c=await connectBackend();result=await c.callTool({name,arguments:args},{timeout:config.tool_timeout_ms});
  if(!result.isError&&name==='open_binary')opened=true;
  if(!result.isError&&name==='close_binary')opened=false;
 }catch(error){result={isError:true,content:[{type:'text',text:error.message}]};}
 const elapsed_ms=performance.now()-start;
 await writeFile(resolve(dirname(trace),`evidence-${String(sequence).padStart(2,'0')}-${name.replace(/[^a-z_]/g,'_')}.json`),JSON.stringify(result,null,2)+'\n');
 await appendFile(trace,JSON.stringify({sequence,tool:name,arguments:args,elapsed_ms,is_error:!!result.isError})+'\n');
 return result;
});
async function close(){
 if(closing)return closing;
 closing=(async()=>{try{if(backend&&opened)await backend.callTool({name:'close_binary',arguments:{}},{timeout:60000});}finally{await backend?.close();}})();
 return closing;
}
const transport=new StdioServerTransport();
transport.onclose=()=>{void close().finally(()=>process.exit(0));};
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>{void close().finally(()=>process.exit(0));});
await server.connect(transport);
process.stdin.on('end',()=>{void close().finally(()=>process.exit(0));});
