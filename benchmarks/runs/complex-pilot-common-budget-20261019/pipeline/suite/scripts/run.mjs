import { spawn } from 'node:child_process';
import { readFile, writeFile, mkdir, appendFile, readdir, copyFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { root, bench, node, runtime, config, configPath, home, prepareHome, environment, makePatch } from '../../../scripts/runtime.mjs';

const suite = `${bench}/suites/complex-native-v1`;
const runId = process.argv[2] ?? 'complex-pilot-common-budget-20261019';
if (!/^[a-zA-Z0-9_-]+$/.test(runId)) throw new Error('Invalid cohort ID');
if (config.schema_version !== 3) throw new Error('Set REA_BENCHMARK_CONFIG to this suite configuration');
const hash = data => createHash('sha256').update(data).digest('hex');
const readJson = async path => JSON.parse(await readFile(path, 'utf8'));
for (const path of ['private/build-validation.json', 'private/grader-validation/validation.json', 'private/runtime-validation/validation.json']) {
  if (!(await readJson(`${suite}/${path}`)).passed) throw new Error(`Preflight failed: ${path}`);
}
if ((await readJson(`${suite}/private/runtime-validation/validation.json`)).requested_output_tokens !== config.max_output_tokens) throw new Error('Output-budget preflight is stale');
if (!(await readJson(`${suite}/config/transport-budget-validation.json`)).passed) throw new Error('Transport budget inspection failed');
const version = (await readJson(`${root}/.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/dsh/package.json`)).version;
if (version !== config.harness_version) throw new Error('Harness version mismatch');

const base = `${bench}/runs/${runId}`;
await mkdir(base, { recursive: true });
const manifestPath = `${base}/run-manifest.json`;
let existing = false;
try {
  if (JSON.stringify((await readJson(manifestPath)).configuration) !== JSON.stringify(config)) throw new Error('Existing cohort configuration differs');
  existing = true;
} catch (error) { if (error.code !== 'ENOENT') throw error; }
const provenance = { files_sha256: {}, public_inputs: {}, private_inputs: {}, frozen_at: new Date().toISOString() };
for (const language of config.languages) {
  const directory = `${suite}/public/${language}/${config.profile}`;
  provenance.public_inputs[language] = {};
  for (const name of ['target', 'prompt.txt', 'manifest.json']) provenance.public_inputs[language][name] = hash(await readFile(`${directory}/${name}`));
}
for (const path of ['private/vectors.json', 'private/build-validation.json', 'private/abi-boundaries.json', 'private/grader-validation/validation.json', 'private/runtime-validation/validation.json', ...config.languages.map(language => `private/build/${language}/build-manifest.json`)]) {
  provenance.private_inputs[path] = hash(await readFile(`${suite}/${path}`));
}
if (!existing) {
  const pipeline = `${base}/pipeline`;
  await mkdir(pipeline, { recursive: true });
  const paths = [
    ['shared/runtime.mjs', `${bench}/scripts/runtime.mjs`], ['shared/rea-gateway.mjs', `${bench}/scripts/rea-gateway.mjs`],
    ['shared/policy.mjs', `${bench}/scripts/policy.mjs`], ['shared/grade.py', `${bench}/scripts/grade.py`],
    ['shared/collect_answer.py', `${bench}/scripts/collect_answer.py`], ['shared/pricing.py', `${bench}/scripts/pricing.py`],
    ['shared/rea-tools.json', `${bench}/config/rea-tools.json`],
    ['suite/README.md', `${suite}/README.md`],
  ];
  for (const directory of ['config', 'scripts', 'authoring/cpp', 'authoring/rust', 'authoring/go']) {
    for (const entry of await readdir(`${suite}/${directory}`, { withFileTypes: true })) {
      if (entry.isFile()) paths.push([`suite/${directory}/${entry.name}`, `${suite}/${directory}/${entry.name}`]);
    }
  }
  for (const [relative, source] of paths) {
    const destination = `${pipeline}/${relative}`;
    await mkdir(destination.substring(0, destination.lastIndexOf('/')), { recursive: true });
    await copyFile(source, destination);
    provenance.files_sha256[relative] = hash(await readFile(destination));
  }
  await writeFile(`${base}/input-provenance.json`, JSON.stringify(provenance, null, 2) + '\n', { flag: 'wx' });
} else {
  const prior = await readJson(`${base}/input-provenance.json`);
  if (JSON.stringify(prior.public_inputs) !== JSON.stringify(provenance.public_inputs) || JSON.stringify(prior.private_inputs) !== JSON.stringify(provenance.private_inputs)) throw new Error('Frozen input changed');
}

const auth = await prepareHome();
if (!existing) await writeFile(manifestPath, JSON.stringify({ started_at: new Date().toISOString(), configuration: config,
  harness_version: version, runtime_home: home, config_path: configPath,
  auth_modes: { deepseek: 'saved API key', gpt: auth.gpt_transport === 'openai-codex' ? 'Codex subscription OAuth' : 'API key' },
  scope: 'Six first attempts on optimized stripped multipoint programs; full suite not launched',
}, null, 2) + '\n', { flag: 'wx' });

const progress = [];
for (const [language, id] of config.run_order) {
  const directory = `${base}/${id}/${language}`;
  await mkdir(directory, { recursive: true });
  try {
    progress.push(await readJson(`${directory}/result.json`));
    console.log(`SKIP preserved attempt ${id}/${language}`);
    continue;
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  // An interrupted attempt is not silently retried or appended to an old event stream.
  try { await readFile(`${directory}/events.jsonl`); throw new Error(`Incomplete attempt exists: ${directory}`); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const publicDir = `${suite}/public/${language}/${config.profile}`;
  const manifest = await readJson(`${publicDir}/manifest.json`);
  if (hash(await readFile(`${publicDir}/target`)) !== manifest.binary_sha256) throw new Error('Target changed');
  const model = { ...config.models.find(item => item.id === id) };
  if (id === 'gpt-6.1-sol') model.provider = auth.gpt_transport;
  const patch = await makePatch(language, model, directory, { publicDir });
  const prompt = await readFile(`${publicDir}/prompt.txt`, 'utf8');
  await writeFile(`${directory}/prompt.txt`, prompt, { flag: 'wx' });
  console.log(`START ${id}/${language} ${new Date().toISOString()}`);
  const start = performance.now();
  const events = [];
  let buffer = '', stderr = '', timedOut = false, turnStartedMs = null, writes = Promise.resolve();
  const child = spawn(node, [runtime, '--profile', 'headless', '--patch', patch, '--json', prompt], {
    cwd: publicDir, env: environment(), stdio: ['pipe', 'pipe', 'pipe'], detached: true,
  });
  child.stdin.end();
  const parse = line => {
    if (!line.trim()) return;
    try {
      const event = JSON.parse(line);
      if (event.type === 'status' && event.phase === 'step_end' && event.usage) event.recorded_at = new Date().toISOString();
      if (event.type === 'status' && event.phase === 'turn_start' && turnStartedMs === null) turnStartedMs = performance.now() - start;
      if (event.type === 'tool_call') console.log(`  ${id}/${language}: ${event.tool}`);
      if (event.type === 'status' && event.phase === 'step_end') console.log(`  ${id}/${language}: step completed`);
      events.push(event);
    } catch { /* Preserve raw stdout even if a non-JSON diagnostic appears. */ }
  };
  child.stdout.on('data', chunk => {
    const text = chunk.toString();
    writes = writes.then(() => appendFile(`${directory}/events.jsonl`, text));
    buffer += text;
    let end;
    while ((end = buffer.indexOf('\n')) >= 0) { parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1); }
  });
  child.stderr.on('data', chunk => { stderr += chunk.toString(); });
  const timeout = setTimeout(() => {
    timedOut = true;
    try { process.kill(-child.pid, 'SIGTERM'); } catch {}
    setTimeout(() => { try { process.kill(-child.pid, 'SIGKILL'); } catch {} }, 10000).unref();
  }, config.task_timeout_seconds * 1000);
  const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve); });
  clearTimeout(timeout);
  await writes;
  if (buffer) parse(buffer);
  await writeFile(`${directory}/stderr.log`, stderr);
  const final = events.filter(event => event.type === 'final').at(-1)?.text ?? '';
  await writeFile(`${directory}/answer.md`, final);
  let trace = [];
  try { trace = (await readFile(`${directory}/tool-calls.jsonl`, 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse); } catch {}
  const observations = events.filter(event => event.type === 'status' && event.phase === 'step_end' && event.usage)
    .map(event => ({ finished_at: event.recorded_at, usage: event.usage }));
  const usage = {};
  for (const observation of observations) for (const [key, value] of Object.entries(observation.usage)) if (typeof value === 'number') usage[key] = (usage[key] ?? 0) + value;
  const elapsed = (performance.now() - start) / 1000;
  const receipt = {
    model: id, model_label: model.label, provider: model.provider, reasoning: model.reasoning,
    language, output_language: language, output_mode: 'original-language', case_id: manifest.case_id, profile: config.profile,
    binary_sha256: manifest.binary_sha256, prompt_sha256: hash(prompt), harness_version: version,
    started_at: new Date(Date.now() - elapsed * 1000).toISOString(), elapsed_seconds: elapsed,
    harness_startup_seconds: turnStartedMs === null ? null : turnStartedMs / 1000,
    active_task_seconds: turnStartedMs === null ? null : elapsed - turnStartedMs / 1000,
    exit_code: code, timed_out: timedOut, tool_calls: trace.length, tool_errors: trace.filter(call => call.is_error).length,
    tool_ms: trace.reduce((total, call) => total + call.elapsed_ms, 0),
    first_native_query_ms: trace.find(call => !['open_binary', 'close_binary'].includes(call.tool))?.elapsed_ms ?? null,
    reported_usage: usage, usage_observations: observations, actual_cost_usd: null,
    cost_note: model.provider === 'openai-codex' ? 'Codex plan; actual per-task charge unavailable' : 'Published-rate estimate; actual invoice unavailable',
    final_present: Boolean(final), turn_end: events.filter(event => event.type === 'status' && event.phase === 'turn_end').at(-1)?.reason ?? null,
    provider_models: [...new Set(events.flatMap(event => [event.model, event.modelId].filter(value => typeof value === 'string')))],
  };
  await writeFile(`${directory}/result.json`, JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' });
  const grade = spawn('/usr/bin/python3', [`${suite}/scripts/grade.py`, directory], { env: process.env, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '', errors = '';
  grade.stdout.on('data', chunk => { output += chunk.toString(); });
  grade.stderr.on('data', chunk => { errors += chunk.toString(); });
  const gradeCode = await new Promise(resolve => grade.on('close', resolve));
  if (gradeCode) await writeFile(`${directory}/grading-error.log`, errors);
  else { const score = JSON.parse(output); console.log(`GRADE ${id}/${language}: ${score.passed}/${score.total}; points=${score.fully_correct_points}/6; compile=${score.compiled}`); }
  progress.push(receipt);
  await writeFile(`${base}/progress.json`, JSON.stringify(progress, null, 2) + '\n');
  console.log(`FINISH ${id}/${language}: ${elapsed.toFixed(1)}s, ${trace.length} tools, tokens=${usage.totalTokens ?? 'unreported'}`);
}
console.log(`COHORT COMPLETE: ${base}`);
