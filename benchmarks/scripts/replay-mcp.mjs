// Local fixture only: serve recorded MCP responses without opening a binary.
import {Server} from '@modelcontextprotocol/server';
import {StdioServerTransport} from '@modelcontextprotocol/server/stdio';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {EvidencePager} from './evidence-paging.mjs';
if(process.env.REA_OFFLINE_REPLAY!=='1')throw new Error('Offline replay fixture is not enabled');
const root=fileURLToPath(new URL('../..',import.meta.url));
const config=JSON.parse(await readFile(process.argv[2],'utf8'));
const catalog=JSON.parse(await readFile(config.tools,'utf8'));
const pager=config.evidence_paging?new EvidencePager(config.evidence_paging):null;
let step=0;
const server=new Server({name:'rea-offline-replay',version:'1'},{capabilities:{tools:{}}});
server.setRequestHandler('tools/list',async()=>({tools:catalog.tools}));
server.setRequestHandler('tools/call',async request=>{
  const current=config.steps[step++];
  if(!current||request.params.name!==current.tool)throw new Error('Unexpected offline replay call');
  if(request.params.name==='read_evidence'&&pager)return pager.read(request.params.arguments);
  const path=resolve(root,current.result_path);
  if(!path.startsWith(resolve(root,'benchmarks/runs')+'/')||!path.endsWith('.json'))throw new Error('Replay input is outside saved evidence');
  const result=JSON.parse(await readFile(path,'utf8'));
  return pager?pager.project(result):result;
});
await server.connect(new StdioServerTransport());
process.stdin.on('end',()=>process.exit(0));
