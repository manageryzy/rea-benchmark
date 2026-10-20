import { appendFile } from 'node:fs/promises';
export const name='benchmark-policy';
export const inject=['tools'];
export async function apply(ctx, config){
 const allowed=new Set(config.allowedTools);
 ctx.effect(()=>ctx.tools.guard(execution=>allowed.has(execution.name)?undefined:'This benchmark exposes only the selected REA analysis tools.'));
 ctx.on('agent/created',async({agent})=>{
  agent.ctx.tools.restrict({allow:[...allowed]});
  const schemas=agent.ctx.tools.schemas(agent);
  const names=schemas.map(t=>t.name);
  if(names.length!==allowed.size||names.some(n=>!allowed.has(n)))throw new Error('Benchmark tool isolation audit failed: '+names.join(','));
  await appendFile(config.auditPath,JSON.stringify({event:'agent_tool_audit',tools:names,source_access:false,execution_access:false})+'\n');
 });
}
