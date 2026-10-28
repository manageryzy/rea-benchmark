// Offline REA/Ghidra transport smoke over a trusted installed system fixture.
import {Client} from '@modelcontextprotocol/client';
import {StdioClientTransport} from '@modelcontextprotocol/client/stdio';
import {fileURLToPath} from 'node:url';
const root = fileURLToPath(new URL('../..', import.meta.url)).replace(/\/$/, '');
const client = new Client({name: 'runtime-image-acceptance', version: '1'});
const transport = new StdioClientTransport({command: root + '/bin/rea', args: ['mcp'],
  stderr: 'inherit', env: {...process.env, REA_ANALYSIS_PROVIDER: 'ghidra', REA_LOG_LEVEL: 'error',
    REA_MCP_INPUT_SCHEMA_PROFILE: 'compact', GHIDRA_HEADLESS_MAXMEM: '2G'}});
let opened = false;
async function call(name, args) {
  const result = await client.callTool({name, arguments: args}, {timeout: 120000});
  if (result.isError) throw new Error(name + ' failed: ' + JSON.stringify(result).slice(0, 1200));
  return result;
}
try {
  await client.connect(transport);
  const tools = await client.listTools();
  for (const name of ['open_binary', 'search_procedures', 'close_binary']) {
    if (!tools.tools.some(t => t.name === name)) throw new Error('Missing REA tool: ' + name);
  }
  await call('open_binary', {path: '/usr/bin/true', provider_id: 'ghidra'}); opened = true;
  await call('search_procedures', {pattern: '.', mode: 'regex'});
  await call('close_binary', {}); opened = false;
  console.log('REA MCP: open, query and close passed; model calls=0');
} finally {
  if (opened) { try { await call('close_binary', {}); } catch {} }
  await client.close();
}
