// Prepare a proposed future catalog without changing any running pilot toolset.
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../../..', import.meta.url)).replace(/\/$/, '');
const client = new Client({ name: 'benchmark-data-tool-inspection', version: '1' });
const transport = new StdioClientTransport({ command: `${root}/bin/rea`, args: ['mcp'], stderr: 'inherit' });
try {
  await client.connect(transport, { timeout: 60000 });
  let cursor;
  const candidates = [];
  do {
    const page = await client.listTools(cursor ? { cursor } : {}, { timeout: 60000 });
    candidates.push(...page.tools.filter(tool => /string|bytes|memory|data_at|address_info/.test(tool.name)));
    cursor = page.nextCursor;
  } while (cursor);
  await writeFile(`${root}/benchmarks/suites/complex-native-v1/config/proposed-data-tools.json`, JSON.stringify({
    status: 'Candidate capabilities only; not enabled in the frozen six-run pilot', tools: candidates,
  }, null, 2) + '\n');
  for (const tool of candidates) console.log(JSON.stringify({ name: tool.name, description: tool.description, input: tool.inputSchema.properties }));
} finally {
  await client.close();
}
