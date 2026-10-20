import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { createOpenAiModelFixture } from '../../../../upstream/rea/scripts/verify/clients/model-fixture.mjs';
import { root, node, runtime, config, prepareHome, environment, makePatch } from '../../../scripts/runtime.mjs';

await prepareHome();
const suite = `${root}/benchmarks/suites/complex-native-v1`;
const validationBase = `${suite}/private/runtime-validation`;
const directory = `${validationBase}/run-${Date.now()}`;
const publicDir = `${suite}/public/cpp/optimized-stripped`;
await mkdir(directory, { recursive: true });
let step = 0;
const fixture = await createOpenAiModelFixture({ directory, prefix: 'complex-isolation', onRequest: async ({ body, tools }) => {
  assert.equal(tools.length, 8);
  assert.equal(body.max_completion_tokens, config.max_output_tokens);
  assert(tools.every(tool => tool.name.startsWith('mcp__rea__')));
  const calls = [
    ['mcp__rea__open_binary', { path: `${suite}/authoring/cpp/case.cpp`, provider_id: 'ghidra' }],
    ['mcp__rea__open_binary', { path: 'target', provider_id: 'ghidra' }],
    ['mcp__rea__close_binary', {}],
  ];
  if (step < calls.length) {
    const [name, args] = calls[step++];
    return { role: 'assistant', content: null, tool_calls: [{ id: `isolation_${step}`, type: 'function', function: { name, arguments: JSON.stringify(args) } }] };
  }
  assert(body.messages.some(message => message.role === 'tool' && message.tool_call_id === 'isolation_3'));
  return { role: 'assistant', content: 'COMPLEX_ISOLATION_OK\n' + 'x'.repeat(12000) };
} });
try {
  const patch = await makePatch('cpp', config.models[0], directory, { fixtureBaseUrl: fixture.baseUrl, publicDir });
  const promise = promisify(execFile)(node, [runtime, '--profile', 'headless', '--patch', patch, '--json', 'Verify restricted access.'], {
    cwd: publicDir, env: { ...environment(), BENCHMARK_FIXTURE_KEY: 'local-fixture-only' }, timeout: 300000, maxBuffer: 16 * 1024 * 1024,
  });
  promise.child.stdin.end();
  const result = await promise;
  await writeFile(`${directory}/events.jsonl`, result.stdout);
  await writeFile(`${directory}/stderr.log`, result.stderr);
  assert.equal(fixture.failure, undefined);
  assert(result.stdout.includes('COMPLEX_ISOLATION_OK'));
  const committed = (await readFile(`${directory}/assistant-messages.jsonl`, 'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(committed.at(-1).text.length, 12021);
  assert(result.stdout.includes('"truncated":true'));
  const trace = (await readFile(`${directory}/tool-calls.jsonl`, 'utf8')).trim().split('\n').map(JSON.parse).slice(-3);
  assert(trace[0].is_error && !trace[1].is_error && !trace[2].is_error);
  const validation = { passed: true, live_model: false, tools: 8, source_denied: true, public_target_allowed: true, session_closed: true, max_tool_calls: config.max_tool_calls, requested_output_tokens: config.max_output_tokens, lossless_text_bytes_tested: 12021, trace_directory: directory };
  await writeFile(`${directory}/validation.json`, JSON.stringify(validation, null, 2));
  await writeFile(`${validationBase}/validation.json`, JSON.stringify(validation, null, 2));
  console.log('PASS: complex runtime exposes eight tools, denies source, and opens/closes the selected stripped ELF.');
} finally {
  await fixture.close();
}
