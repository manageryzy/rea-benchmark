import assert from 'node:assert/strict';
import { writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const root = fileURLToPath(new URL('..', import.meta.url)).replace(/\/$/, '');
const windows = process.argv.includes('--windows');
const catalogOnly = process.argv.includes('--catalog');
const transport = new StdioClientTransport({
  command: windows ? '/mnt/c/Windows/System32/wsl.exe' : `${root}/bin/rea`,
  args: windows ? ['--distribution', 'Ubuntu-24.04', '--exec', `${root}/bin/rea`, 'mcp'] : ['mcp'],
  cwd: root,
  stderr: 'inherit',
});
const client = new Client({ name: 'rea-environment-verification', version: '1.0.0' });
const options = { timeout: 450000 };
let opened = false;
try {
  await client.connect(transport, { timeout: 30000 });
  const tools = [];
  let cursor;
  do {
    const page = await client.listTools(cursor ? { cursor } : {}, options);
    tools.push(...page.tools);
    cursor = page.nextCursor;
  } while (cursor);
  console.log(`Connected to REA; discovered ${tools.length} tools (${windows ? 'Windows/WSL' : 'Linux'}).`);
  if (catalogOnly) {
    for (const tool of tools.filter(t => /pseudo_code|decompil|open_binary|close_binary|find.*function/.test(t.name))) {
      console.log(tool.name, JSON.stringify(tool.inputSchema));
    }
  } else {
    const call = async (name, args) => {
      const result = await client.callTool({ name, arguments: args }, options);
      assert(!result.isError, JSON.stringify(result));
      await writeFile(`${root}/results/mcp-${windows ? 'windows' : 'linux'}-${name}.json`, JSON.stringify(result, null, 2) + '\n');
      return result;
    };
    await mkdir(`${root}/results`, { recursive: true });
    await call('open_binary', { path: `${root}/examples/build/hello`, provider_id: 'ghidra' });
    opened = true;
    const result = await call('procedure_pseudo_code', { procedure: 'calculate_total' });
    const serialized = JSON.stringify(result);
    assert(serialized.includes('calculate_total') && serialized.includes('return'), 'Expected decompiled C in MCP result');
    await call('close_binary', {});
    opened = false;
    console.log('PASS: Opened, decompiled and closed a Linux ELF through MCP.');
  }
} finally {
  if (opened) {
    await client.callTool({ name: 'close_binary', arguments: {} }, options);
  }
  await client.close();
}
