// Capture the Codex request before any network call using a local dummy grant.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { streamSimple } from '../../../../.tools/harness-0.2.1-rc.2/node_modules/@earendil-works/pi-ai/dist/api/openai-codex-responses.js';

const root = fileURLToPath(new URL('../../../..', import.meta.url)).replace(/\/$/, '');
const adapter = `${root}/.tools/harness-0.2.1-rc.2/node_modules/@earendil-works/pi-ai/dist/api/openai-codex-responses.js`;
const token = `${Buffer.from('{}').toString('base64url')}.${Buffer.from(JSON.stringify({ 'https://api.openai.com/auth': { chatgpt_account_id: 'local-cap-validation' } })).toString('base64url')}.LOCAL_NOT_A_CREDENTIAL`;
let captured;
const model = { id: 'gpt-6.1-sol', name: 'GPT-6.1 Sol', api: 'openai-codex-responses', provider: 'openai-codex',
  baseUrl: 'http://127.0.0.1:9', reasoning: true, input: ['text'], contextWindow: 1050000, maxTokens: 16384,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
const response = streamSimple(model, { messages: [{ role: 'user', content: 'Local cap validation', timestamp: Date.now() }] }, {
  apiKey: token, maxTokens: 16384, reasoning: 'high', transport: 'sse',
  onPayload(body) { captured = body; throw new Error('LOCAL_CAPTURE_BEFORE_NETWORK'); },
});
for await (const event of response) { /* No network or provider billing occurs. */ }
assert(captured, 'Request interception must run');
assert(!Object.hasOwn(captured, 'max_output_tokens'));
assert(!Object.hasOwn(captured, 'max_tokens'));
const result = {
  passed: true, live_model_calls: 0, dummy_grant_only: true, requested_metadata_max_tokens: 16384,
  codex_wire_output_cap_omitted: true, captured_body_keys: Object.keys(captured),
  adapter_sha256: createHash('sha256').update(await readFile(adapter)).digest('hex'),
  documented_gpt_model_maximum: 128000,
  official_source: 'https://developers.openai.com/api/docs/models/gpt-6.1-sol',
  correction: 'Request 128000 for DeepSeek and use the documented 128000 GPT model ceiling; disclose that the Codex adapter omits the wire parameter.',
  verified_at: new Date().toISOString(),
};
await writeFile(`${root}/benchmarks/suites/complex-native-v1/config/transport-budget-validation.json`, JSON.stringify(result, null, 2) + '\n');
console.log('PASS: local-only request capture proves Codex omits the configured output cap; no network call made.');
