// Test Harness's actual MCP client using a local HTTP model fixture.
// This does not call DeepSeek or evaluate a real model.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { createOpenAiModelFixture } from '../upstream/rea/scripts/verify/clients/model-fixture.mjs';

const exec = promisify(execFile);
const root = fileURLToPath(new URL('..', import.meta.url)).replace(/\/$/, '');
const directory = `${root}/.cache/harness-verification`;
await mkdir(directory, { recursive: true, mode: 0o700 });
let requested = false;
let returned = false;
let reaToolCount = 0;
const fixture = await createOpenAiModelFixture({
  directory,
  prefix: 'connection',
  onRequest: async ({ body, tools, requestIndex }) => {
    if (tools.length === 0) return { role: 'assistant', content: 'REA connection check' };
    assert(requestIndex < 8, 'Fixture workflow did not finish');
    reaToolCount = tools.filter(t => t.name.startsWith('mcp__rea__')).length;
    assert.equal(reaToolCount, 207, 'Harness must discover the complete REA tool catalog');
    assert(tools.some(t => t.name === 'mcp__rea__binary_session'));
    if (!requested) {
      requested = true;
      return {
        role: 'assistant', content: null,
        tool_calls: [{ id: 'call_rea_connection', type: 'function', function: {
          name: 'mcp__rea__binary_session', arguments: '{}',
        } }],
      };
    }
    const result = body.messages.find(m => m.role === 'tool' && m.tool_call_id === 'call_rea_connection');
    assert(result && typeof result.content === 'string' && result.content.length > 0, 'Missing REA tool response');
    // Harness presents tool output as text; the model-facing rendering need
    // not remain parseable as the server's original structured JSON.
    assert(result.content.includes('"open":false'), 'Expected a target-free session status');
    assert(result.content.includes('"provider_version":"12.1.4"'), 'Expected the configured Ghidra provider');
    returned = true;
    return { role: 'assistant', content: 'HARNESS_REA_CONNECTION_OK' };
  },
});
try {
  const patch = `${directory}/model.patch.yml`;
  await writeFile(patch, `- id: llm-pi-ai\n  config:\n    providers:\n      rea-local-check:\n        apiKeyEnv: REA_LOCAL_TEST_KEY\n        api: openai-completions\n        baseURL: ${fixture.baseUrl}\n        defaultMaxTokens: 4096\n        retryPolicy:\n          mode: normal\n          maxRetries: 0\n        models:\n          - id: rea-client-fixture\n            contextWindow: 1000000\n- id: agent-default-model\n  config:\n    provider: rea-local-check\n    model: rea-client-fixture\n`);
  const execution = exec(`${root}/bin/dsh`, ['--profile', 'headless', '--patch', patch, '--json', 'Check the REA binary session status without opening or executing any target.'], {
    cwd: root,
    env: { ...process.env, REA_LOCAL_TEST_KEY: 'local-fixture-only' },
    timeout: 300000,
    maxBuffer: 16 * 1024 * 1024,
  });
  execution.child.stdin.end();
  const result = await execution;
  await writeFile(`${directory}/stdout.jsonl`, result.stdout);
  await writeFile(`${directory}/stderr.log`, result.stderr);
  assert.equal(fixture.failure, undefined);
  assert(requested && returned && result.stdout.includes('HARNESS_REA_CONNECTION_OK'));
  const version = JSON.parse(await readFile(`${root}/.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/dsh/package.json`, 'utf8')).version;
  const receipt = { healthy: true, harness_version: version, rea_tool_count: reaToolCount, actual_mcp_call: 'binary_session', live_model_tested: false };
  await writeFile(`${root}/results/harness-verification.json`, JSON.stringify(receipt, null, 2) + '\n');
  console.log(JSON.stringify(receipt, null, 2));
} catch (error) {
  if (error.stdout) await writeFile(`${directory}/stdout.jsonl`, error.stdout);
  if (error.stderr) await writeFile(`${directory}/stderr.log`, error.stderr);
  throw new Error(error.message);
} finally {
  await fixture.close();
}
