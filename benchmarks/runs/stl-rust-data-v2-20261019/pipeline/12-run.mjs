// Ten first attempts. Exact-receipt reuse and atomic claims prevent duplicate billing.
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, writeFile, mkdir, copyFile, readdir, unlink } from 'node:fs/promises';
import { unlinkSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { root, bench, node, runtime, config, configPath, catalogPath, home, prepareHome, environment, makePatch } from '../../../scripts/runtime.mjs';

const suite = `${bench}/suites/stl-native-v2`, core = `${bench}/suites/complex-native-v1`;
const runId = process.argv[2] ?? 'stl-rust-data-v2-20261019';
if (!/^[a-zA-Z0-9_-]+$/.test(runId) || config.suite_version !== 4) throw new Error('Use this suite configuration and a valid cohort ID');
const base = `${bench}/runs/${runId}`;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const json = async path => JSON.parse(await readFile(path, 'utf8'));
const exists = async path => { try { await readFile(path); return true; } catch (e) { if (e.code === 'ENOENT') return false; throw e; } };
const dump = (path, data, exclusive = false) => writeFile(path, JSON.stringify(data, null, 2) + '\n', exclusive ? { flag: 'wx' } : {});
const execute = promisify(execFile);
await mkdir(base, { recursive: true });
const lock = `${base}/runner.lock`;
await writeFile(lock, JSON.stringify({ pid: process.pid, started_at: new Date().toISOString() }), { flag: 'wx' });
process.on('exit', () => { try { unlinkSync(lock); } catch {} });
const build = await json(`${suite}/private/validation.json`);
for (const path of [`${suite}/private/grader-validation.json`, `${core}/private/data-tool-validation.json`, `${core}/private/build-validation.json`, `${core}/private/grader-validation/validation.json`, `${core}/private/runtime-validation/validation.json`]) {
  if (!(await json(path)).passed) throw new Error(`Invalid cached preflight: ${path}`);
}
const dataCheck = await json(`${core}/private/data-tool-validation.json`);
for (const [path, digest] of Object.entries(dataCheck.inputs_sha256)) if (hash(await readFile(path)) !== digest) throw new Error(`Stale data-tool check: ${path}`);
if (hash(await readFile(catalogPath)) !== dataCheck.inputs_sha256[catalogPath]) throw new Error('Catalog mismatch');
const version = (await json(`${root}/.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/dsh/package.json`)).version;
if (version !== config.harness_version) throw new Error('Harness version changed');

const protocolFiles = [configPath, catalogPath, `${bench}/scripts/runtime.mjs`, `${bench}/scripts/rea-gateway.mjs`,
  `${bench}/scripts/tool-catalog.mjs`, `${bench}/scripts/policy.mjs`, `${bench}/scripts/collect_answer.py`, `${bench}/scripts/grade.py`,
  `${bench}/scripts/pricing.py`, `${core}/scripts/lossless-output.mjs`, `${core}/scripts/grade.py`,
  `${suite}/scripts/grade.py`, `${suite}/scripts/run.mjs`, `${suite}/authoring/driver.cpp`, `${suite}/config/pricing-data-v2.json`,
  `${suite}/authoring/containers.cpp`, `${suite}/authoring/grammar14.inc`, `${suite}/scripts/oracle.py`, `${suite}/scripts/selftest.py`,
  `${core}/authoring/rust/case.rs`, `${core}/authoring/rust/abi_bridge.rs`, `${core}/scripts/oracle.py`,
  `${suite}/private/validation.json`, `${core}/private/vectors.json`, `${build.cache_directory}/vectors.json`];
const protocol = {};
for (const path of protocolFiles) protocol[path] = hash(await readFile(path));
const protocolDigest = hash(JSON.stringify(protocol));
const manifestPath = `${base}/run-manifest.json`;
if (await exists(manifestPath)) {
  const prior = await json(manifestPath);
  if (JSON.stringify(prior.configuration) !== JSON.stringify(config) || prior.protocol_sha256 !== protocolDigest) throw new Error('Existing cohort protocol changed; preserved attempts cannot be overwritten');
} else {
  const inputs = {};
  for (const item of config.cases) {
    const destination = `${base}/inputs/public/${item.id}`;
    await mkdir(destination, { recursive: true });
    inputs[item.id] = {};
    for (const name of ['target', 'manifest.json', 'prompt.txt']) {
      let bytes = await readFile(`${root}/${item.public_dir}/${name}`);
      if (name === 'prompt.txt') bytes = Buffer.from(bytes.toString() + '\nShared analysis policy: at most 60 tool calls and 30 minutes. Byte/string inspection is available. For short or packed Rust format data, inspect known-address bytes and references; a missing defined string does not prove absence. Preserve evidence and distinguish observation from inference. Submit exactly one native-language code block, with human-readable formatting, descriptive names, helpers and useful comments. No feedback or repair attempt.\n');
      await writeFile(`${destination}/${name}`, bytes, { flag: 'wx', mode: name === 'target' ? 0o755 : 0o644 });
      inputs[item.id][name] = hash(bytes);
    }
  }
  for (const [index, path] of protocolFiles.entries()) {
    const destination = `${base}/pipeline/${String(index).padStart(2, '0')}-${path.split('/').at(-1)}`;
    await mkdir(`${base}/pipeline`, { recursive: true });
    await copyFile(path, destination);
  }
  await dump(`${base}/input-provenance.json`, { frozen_at: new Date().toISOString(), protocol_files_sha256: protocol,
    public_inputs: inputs, protocol_sha256: protocolDigest, immutable_input_archive: true }, true);
  await dump(manifestPath, { started_at: new Date().toISOString(), configuration: config,
    protocol_sha256: protocolDigest, harness_version: version, runtime_home: home, scope: config.scope,
    full_suite_started: false, exact_attempt_count: config.run_order.length }, true);
}
const provenance = await json(`${base}/input-provenance.json`);
for (const item of config.cases) for (const [name, digest] of Object.entries(provenance.public_inputs[item.id]))
  if (hash(await readFile(`${base}/inputs/public/${item.id}/${name}`)) !== digest) throw new Error('Frozen public input changed');
const pending = [];
for (const [caseId, modelId] of config.run_order) {
  const directory = `${base}/${modelId}/${caseId}`;
  if (await exists(`${directory}/completed.json`)) {
    const completed = await json(`${directory}/completed.json`);
    for (const [name, digest] of Object.entries(completed.files_sha256)) if (hash(await readFile(`${directory}/${name}`)) !== digest) throw new Error(`Cached attempt changed: ${directory}/${name}`);
    console.log(`CACHE HIT ${modelId}/${caseId}: complete attempt preserved`);
  } else pending.push([caseId, modelId]);
}
if (!pending.length) { console.log('COHORT CACHED: no authentication or inference required'); process.exit(0); }
const auth = await prepareHome();
const registry = `${root}/.cache/benchmark-attempt-registry`;
await mkdir(registry, { recursive: true });
const children = new Set();
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => {
  for (const pid of children) try { process.kill(-pid, 'SIGTERM'); } catch {}
  process.exit(signal === 'SIGINT' ? 130 : 143);
});

async function attempt(caseId, id) {
  const item = config.cases.find(c => c.id === caseId);
  const directory = `${base}/${id}/${caseId}`;
  const publicDir = `${base}/inputs/public/${caseId}`;
  const manifest = await json(`${publicDir}/manifest.json`);
  const prompt = await readFile(`${publicDir}/prompt.txt`, 'utf8');
  const fingerprint = hash(JSON.stringify({ model: id, configuration: config, protocol: protocolDigest,
    binary: provenance.public_inputs[caseId].target, prompt: hash(prompt), caseId }));
  const claimPath = `${registry}/${fingerprint}.json`;
  await mkdir(directory, { recursive: true });
  if (!(await exists(`${directory}/inference.json`))) {
    if (await exists(claimPath)) throw new Error(`Attempt already claimed; do not duplicate inference. Inspect ${claimPath}`);
    if (await exists(`${directory}/events.jsonl`)) throw new Error(`Interrupted attempt preserved; automatic retry denied: ${directory}`);
    await dump(claimPath, { fingerprint, directory, status: 'started', started_at: new Date().toISOString() }, true);
    const model = { ...config.models.find(m => m.id === id) };
    if (id === 'gpt-6.1-sol') model.provider = auth.gpt_transport;
    const patch = await makePatch(item.language, model, directory, { publicDir });
    await writeFile(`${directory}/prompt.txt`, prompt, { flag: 'wx' });
    const startedAt = new Date().toISOString();
    console.log(`START ${id}/${caseId} ${startedAt}`);
    const start = performance.now(), events = [];
    let buffer = '', stderr = '', timedOut = false, turnStart = null;
    const output = await import('node:fs').then(fs => fs.createWriteStream(`${directory}/events.jsonl`, { flags: 'wx' }));
    const child = spawn(node, [runtime, '--profile', 'headless', '--patch', patch, '--json', prompt], {
      cwd: publicDir, env: environment(), stdio: ['pipe', 'pipe', 'pipe'], detached: true });
    children.add(child.pid);
    child.stdin.end();
    const parse = line => {
      try {
        const event = JSON.parse(line);
        if (event.type === 'status' && event.phase === 'step_end' && event.usage) event.recorded_at = new Date().toISOString();
        if (event.type === 'status' && event.phase === 'turn_start' && turnStart === null) turnStart = performance.now() - start;
        if (event.type === 'tool_call') console.log(`  ${id}/${caseId}: ${event.tool}`);
        events.push(event);
      } catch {}
    };
    child.stdout.on('data', chunk => {
      output.write(chunk);
      buffer += chunk.toString();
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) { parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1); }
    });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });
    const timer = setTimeout(() => {
      timedOut = true;
      try { process.kill(-child.pid, 'SIGTERM'); } catch {}
      setTimeout(() => { try { process.kill(-child.pid, 'SIGKILL'); } catch {} }, 10000).unref();
    }, config.task_timeout_seconds * 1000);
    const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve); });
    clearTimeout(timer); children.delete(child.pid);
    await new Promise((resolve, reject) => { output.on('error', reject); output.end(resolve); });
    if (buffer.trim()) parse(buffer);
    await writeFile(`${directory}/stderr.log`, stderr);
    let trace = [];
    if (await exists(`${directory}/tool-calls.jsonl`)) trace = (await readFile(`${directory}/tool-calls.jsonl`, 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);
    const observations = events.filter(e => e.type === 'status' && e.phase === 'step_end' && e.usage).map(e => ({ finished_at: e.recorded_at, usage: e.usage }));
    const usage = {};
    for (const observation of observations) for (const [key, value] of Object.entries(observation.usage)) if (typeof value === 'number') usage[key] = (usage[key] ?? 0) + value;
    const elapsed = (performance.now() - start) / 1000;
    const receipt = { attempt_fingerprint: fingerprint, protocol_sha256: protocolDigest, model: id, model_label: model.label,
      provider: model.provider, reasoning: model.reasoning, case_id: caseId, language: item.language, output_language: item.language,
      track: item.track, profile: config.profile, standard: manifest.standard ?? 'Rust 2021', string_abi: manifest.string_abi ?? null,
      binary_sha256: manifest.binary_sha256, prompt_sha256: hash(prompt), harness_version: version,
      started_at: startedAt, completed_at: new Date().toISOString(), elapsed_seconds: elapsed,
      harness_startup_seconds: turnStart === null ? null : turnStart / 1000, exit_code: code, timed_out: timedOut,
      tool_calls: trace.length, tool_errors: trace.filter(t => t.is_error).length, tool_ms: trace.reduce((s, t) => s + t.elapsed_ms, 0),
      data_tool_calls: trace.filter(t => ['read_bytes', 'search_bytes', 'list_strings', 'search_strings'].includes(t.tool)).length,
      explicit_close: trace.some(t => t.tool === 'close_binary' && !t.is_error), reported_usage: usage, usage_observations: observations,
      actual_cost_usd: null, cost_note: model.provider === 'openai-codex' ? 'Codex plan; API-equivalent estimate, actual credits unavailable' : 'Published-rate estimate; actual invoice unavailable',
      final_present: events.some(e => e.type === 'final' && e.text), turn_end: events.filter(e => e.type === 'status' && e.phase === 'turn_end').at(-1)?.reason ?? null,
      provider_models: [...new Set(events.flatMap(e => [e.model, e.modelId].filter(v => typeof v === 'string')))] };
    await dump(`${directory}/inference.json`, receipt, true);
    await dump(`${directory}/result.json`, receipt, true);
    console.log(`INFERENCE DONE ${id}/${caseId}: ${elapsed.toFixed(1)}s, tokens=${usage.totalTokens ?? 'unreported'}`);
  } else console.log(`CACHE HIT ${id}/${caseId}: reusing saved inference for offline grading`);
  await execute('/usr/bin/python3', [`${bench}/scripts/collect_answer.py`, directory], { maxBuffer: 16 * 1024 * 1024 });
  const args = item.track === 'stl' ? [`${suite}/scripts/grade.py`, directory, caseId] : [`${core}/scripts/grade.py`, directory, 'rust'];
  const graded = await execute('/usr/bin/python3', args, { maxBuffer: 16 * 1024 * 1024 });
  const score = JSON.parse(graded.stdout);
  const files = {};
  for (const name of ['result.json', 'inference.json', 'grade.json', 'answer.md', 'assistant-messages.jsonl', 'events.jsonl', 'tool-calls.jsonl', 'prompt.txt'])
    if (await exists(`${directory}/${name}`)) files[name] = hash(await readFile(`${directory}/${name}`));
  await dump(`${directory}/completed.json`, { attempt_fingerprint: fingerprint, completed_at: new Date().toISOString(), files_sha256: files }, true);
  await dump(claimPath, { fingerprint, directory, status: 'complete', completed_receipt_sha256: hash(await readFile(`${directory}/completed.json`)) });
  console.log(`GRADE ${id}/${caseId}: ${score.passed}/${score.total}; compiled=${score.compiled}; points=${score.active_points_passed ?? score.fully_correct_points}/${score.active_point_count ?? score.total_function_points}`);
}

try {
  const outcomes = await Promise.allSettled(config.models.map(async model => {
    for (const [caseId, id] of pending.filter(([, id]) => id === model.id)) await attempt(caseId, id);
  }));
  const failures = outcomes.filter(o => o.status === 'rejected');
  if (failures.length) throw new AggregateError(failures.map(o => o.reason), 'Cohort has preserved incomplete attempts; never auto-retry inference');
  await dump(`${base}/cohort-completed.json`, { complete: true, attempts: config.run_order.length, completed_at: new Date().toISOString() }, true);
  console.log(`COHORT COMPLETE ${base}`);
} finally {
  await unlink(`${home}/.credentials.yaml`).catch(() => {});
  await unlink(lock).catch(() => {});
}
