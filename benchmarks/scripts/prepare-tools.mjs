import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const root=fileURLToPath(new URL('../..',import.meta.url)).replace(/\/$/,'');
const allowed=new Set(['open_binary','close_binary','procedure_pseudo_code','procedure_assembly','procedure_info','analyze_function','search_procedures','procedure_references']);
const client=new Client({name:'benchmark-catalog',version:'1'});
const transport=new StdioClientTransport({command:`${root}/bin/rea`,args:['mcp'],stderr:'inherit'});
try {
 await client.connect(transport,{timeout:60000});
 let cursor;const tools=[];
 do {const p=await client.listTools(cursor?{cursor}:{},{timeout:60000});tools.push(...p.tools.filter(t=>allowed.has(t.name)));cursor=p.nextCursor;}while(cursor);
 if(tools.length!==allowed.size)throw new Error(`Expected ${allowed.size} analysis tools, found ${tools.length}`);
 await writeFile(`${root}/benchmarks/config/rea-tools.json`,JSON.stringify({rea_version:'6.7.0',tools},null,2)+'\n');
 console.log(`Prepared ${tools.length} actual REA tool definitions.`);
}finally{await client.close();}
