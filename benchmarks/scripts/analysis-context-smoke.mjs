// Real local MCP/Ghidra propagation check. No model client or provider is used.
import {readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Client} from '@modelcontextprotocol/client';
import {StdioClientTransport} from '@modelcontextprotocol/client/stdio';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const spec = JSON.parse(await readFile(process.argv[2], 'utf8'));
const client = new Client({name:'rea-analysis-context-control', version:'1'}, {capabilities:{}});
const transport = new StdioClientTransport({
  command: resolve(root, '.tools/node-v24.22.0-linux-x64/bin/node'),
  args: [resolve(root, 'benchmarks/scripts/rea-gateway.mjs'), spec.target, spec.trace, spec.gateway],
  env: {PATH:'/usr/bin:/bin', LANG:'C.UTF-8'}, stderr:'inherit', cwd:root,
});
let opened = false;
try {
  await client.connect(transport);
  const start = await client.callTool({name:'open_binary', arguments:{path:spec.target}}, {timeout:180000});
  if (start.isError) throw new Error('Public target could not be opened');
  opened = true;
  const result = await client.callTool({name:'procedure_info', arguments:{procedure:spec.address}}, {timeout:180000});
  if (result.isError) throw new Error('Supplied entry is unavailable through the benchmark gateway');
  const raw = await readFile(resolve(dirname(spec.trace), 'evidence-02-procedure_info.json'));
  const envelope = JSON.parse(raw);
  const evidence = envelope.structuredContent ?? JSON.parse(envelope.content[0].text);
  if (evidence.operation !== 'procedure_info' || !evidence.normalized_result || evidence.error)
    throw new Error('Procedure query did not return native analysis Evidence');
  if (evidence.analysis_profile?.parameters?.analysis_seeds?.sha256 !== spec.seed_sha256)
    throw new Error('Ghidra did not report the committed public-entry seeds');
  const serialized = JSON.stringify(evidence.normalized_result);
  if (!serialized.includes(spec.address)) throw new Error('Observed function differs from the supplied entry');
  const receipt = {passed:true, model_api_calls:0, provider:'ghidra',
    binary_sha256:evidence.subject.digest.sha256, seed_sha256:spec.seed_sha256,
    evidence_sha256:createHash('sha256').update(raw).digest('hex'),
    analysis_profile_digest:evidence.analysis_profile.digest};
  await writeFile(spec.output, JSON.stringify(receipt, null, 2)+'\n');
  process.stdout.write(JSON.stringify(receipt)+'\n');
} finally {
  if (opened) await client.callTool({name:'close_binary', arguments:{}}, {timeout:60000}).catch(()=>{});
  await client.close();
}
