// Local MCP verification only: no model endpoint, credentials or paid inference.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { loadToolCatalog } from '../../../scripts/tool-catalog.mjs';

const root = fileURLToPath(new URL('../../../..', import.meta.url)).replace(/\/$/, '');
const suite = `${root}/benchmarks/suites/complex-native-v1`;
const configPath = `${suite}/config/pilot-data-tools-v2.json`;
const config = JSON.parse(await readFile(configPath, 'utf8'));
const { catalog } = await loadToolCatalog(root, config);
const hash = data => createHash('sha256').update(data).digest('hex');
const target = `${suite}/public/rust/optimized-stripped/target`;
const cachedEvidence = `${root}/benchmarks/runs/complex-pilot-common-budget-20261019/gpt-6.1-sol/rust/evidence-03-analyze_function.json`;
const original = JSON.parse(await readFile(cachedEvidence, 'utf8'));
const dossier = JSON.parse(original.content.find(c => c.type === 'text').text);
assert.equal(dossier.subject.digest.sha256, hash(await readFile(target)));
// Reuse the paid pilot's assembly rather than decompile the function again.
const instruction = dossier.normalized_result.assembly.find(line => /MOV qword ptr \[RSP \+ 0x8\], 0x206038/.test(line));
assert(instruction, 'Frozen P4 format-pointer instruction must still match');
const address = instruction.match(/, (0x[\da-f]+)$/i)[1];
const inputs = {};
for (const path of [target, cachedEvidence, configPath, `${suite}/config/rea-tools-data-v2.json`,
  `${root}/benchmarks/scripts/rea-gateway.mjs`, `${root}/benchmarks/scripts/tool-catalog.mjs`, fileURLToPath(import.meta.url)])
  inputs[path] = hash(await readFile(path));
const key = hash(JSON.stringify(inputs));
const directory = `${suite}/private/data-tool-validation/${key}`;
const receipt = `${directory}/validation.json`;
try {
  const prior = JSON.parse(await readFile(receipt, 'utf8'));
  assert(prior.passed && prior.fingerprint === key);
  for (const [name, digest] of Object.entries(prior.artifacts_sha256))
    assert.equal(hash(await readFile(`${directory}/${name}`)), digest);
  await writeFile(`${suite}/private/data-tool-validation.json`, JSON.stringify({ ...prior, cache_hit: true }, null, 2) + '\n');
  console.log('CACHE HIT: Rust data inspection already validated; no new Ghidra or model run');
  process.exit(0);
} catch (error) { if (error.code !== 'ENOENT') throw error; }
await mkdir(directory, { recursive: true });
const node = `${root}/.tools/node-v24.22.0-linux-x64/bin/node`;
const transport = new StdioClientTransport({ command: node, args: [`${root}/benchmarks/scripts/rea-gateway.mjs`, target, `${directory}/tool-calls.jsonl`, configPath], stderr: 'inherit' });
const client = new Client({ name: 'local-data-tools-validation', version: '2' });
const results = [];
let opened = false;
const call = async (name, args, expectError = false) => {
  const response = await client.callTool({ name, arguments: args }, { timeout: 450000 });
  results.push({ name, arguments: args, response });
  assert.equal(!!response.isError, expectError, `${name}: ${JSON.stringify(response).slice(0, 250)}`);
  return response;
};
try {
  await client.connect(transport, { timeout: 60000 });
  assert.deepEqual((await client.listTools()).tools.map(t => t.name).sort(), catalog.tools.map(t => t.name).sort());
  assert.equal(catalog.tools.length, 12);
  await call('open_binary', { path: `${suite}/authoring/rust/case.rs` }, true);
  await call('read_bytes', { address, length: 4097 }, true);
  await call('read_bytes', { address, length: 8, document: '/etc/passwd' }, true);
  await call('search_bytes', { pattern: '7c', limit: 201 }, true);
  await call('open_binary', { path: 'target' });
  opened = true;
  const bytes = await call('read_bytes', { address, length: 24 });
  const envelope = JSON.parse(bytes.content.find(c => c.type === 'text').text);
  assert.equal(envelope.subject.digest.sha256, inputs[target]);
  await call('list_strings', { address });
  await call('search_strings', { pattern: 'ERR', mode: 'literal', case_sensitive: true });
  await call('search_bytes', { pattern: '7c', start: address, end: `0x${(BigInt(address) + 24n).toString(16)}`, limit: 10 });
  await call('close_binary', {});
  opened = false;
  await writeFile(`${directory}/results.json`, JSON.stringify(results, null, 2) + '\n');
  const artifacts = {};
  for (const name of ['results.json', 'tool-calls.jsonl']) artifacts[name] = hash(await readFile(`${directory}/${name}`));
  const validation = { passed: true, fingerprint: key, inputs_sha256: inputs, artifacts_sha256: artifacts,
    verified_at: new Date().toISOString(), directory, cache_hit: false, tools: 12,
    live_model_calls: 0, additional_model_tokens: 0, additional_model_cost_usd: 0,
    reused_binary: true, reused_pilot_assembly: true, source_denied: true, document_override_denied: true,
    oversized_read_denied: true, excessive_search_hits_denied: true, session_closed: true,
    rust_format_pointer: address, pointer_instruction: instruction, raw_read_observation: envelope.normalized_result,
    interpretation: 'Byte-level observation only; literal inventories can omit short or packed Rust format data. Old pilot answers are unchanged.' };
  await writeFile(receipt, JSON.stringify(validation, null, 2) + '\n');
  await writeFile(`${suite}/private/data-tool-validation.json`, JSON.stringify(validation, null, 2) + '\n');
  console.log(`PASS: 12 tools, bounded Rust bytes/strings, isolation checks, explicit close; raw data ${JSON.stringify(envelope.normalized_result)}`);
} finally {
  if (opened) await client.callTool({ name: 'close_binary', arguments: {} }, { timeout: 60000 }).catch(() => {});
  await client.close();
}
