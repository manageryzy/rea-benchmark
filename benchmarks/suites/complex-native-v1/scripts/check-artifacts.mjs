// Verify exported text contains no actual saved provider credential values.
import { readFile, readdir, writeFile, access, mkdir } from 'node:fs/promises';
import { parse } from 'yaml';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../../..', import.meta.url)).replace(/\/$/, '');
const runId = process.argv[2] ?? 'complex-pilot-common-budget-20261019';
const secrets = new Set();
function collect(value) {
  if (!value || typeof value !== 'object') return;
  for (const [key, item] of Object.entries(value)) {
    if (/^(?:(?:DEEPSEEK|OPENAI)_API_KEY|api[_-]?key|access[_-]?token|refresh[_-]?token|id_token|access|refresh)$/i.test(key) && typeof item === 'string' && item.length >= 20) secrets.add(item);
    else if (item && typeof item === 'object') collect(item);
  }
}
collect(parse(await readFile(`${root}/.harness/.credentials.yaml`, 'utf8')));
collect(JSON.parse(await readFile('/mnt/c/Users/mana/.codex/auth.json', 'utf8')));
collect({ OPENAI_API_KEY: process.env.OPENAI_API_KEY, DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY });
let count = 0;
const hits = [];
async function inspect(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) await inspect(path);
    else if (/\.(?:html|json|jsonl|csv|md|cpp|hpp|rs|go|py|mjs|yml|yaml|log|txt)$/.test(entry.name)) {
      const text = await readFile(path, 'utf8');
      count += 1;
      if ([...secrets].some(secret => text.includes(secret))) hits.push(path);
    }
  }
}
for (const directory of [`${root}/benchmarks/runs/${runId}`, `${root}/benchmarks/reports/${runId}`, `${root}/benchmarks/runs/complex-pilot-20261019`]) await inspect(directory);
if (hits.length) throw new Error(`Credential values found in ${hits.length} artifact files`);
for (const cohort of [runId, 'complex-pilot-20261019']) {
  const manifest = JSON.parse(await readFile(`${root}/benchmarks/runs/${cohort}/run-manifest.json`, 'utf8'));
  try { await access(`${manifest.runtime_home}/.credentials.yaml`); throw new Error('Temporary credentials remain'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
}
await mkdir(root + '/.cache/benchmark-validation/' + runId, { recursive: true });
await writeFile(`${root}/.cache/benchmark-validation/${runId}/artifact-safety.json`, JSON.stringify({
  passed: true, text_files_checked: count, known_secret_values_checked: secrets.size,
  credential_value_hits: 0, temporary_credentials_absent: true,
}, null, 2) + '\n');
console.log(`PASS: ${count} text artifacts contain no saved provider credential values; temporary credentials removed.`);
