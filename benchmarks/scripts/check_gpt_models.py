#!/usr/bin/python3
"""Check GPT model selection, equal harness controls and price bounds offline."""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch as mock_patch

import records
import pricing


def main():
    root = records.ROOT
    assert records.read(records.BENCH / 'config/execution.json')['experiments_stopped']
    catalog = records.models()
    ids = ['gpt-6.1-sol', 'gpt-6-luna', 'gpt-5.6-sol']
    with tempfile.TemporaryDirectory(prefix='rea-gpt-models-', dir='/tmp') as temporary:
        script = """
import {readFile} from 'node:fs/promises';
import {parse} from 'yaml';
import {makePatch,home} from './benchmarks/harnesses/deepseek-harness.mjs';
const ids=JSON.parse(process.env.REA_GPT_IDS);
const results=[];
for (const id of ids) for (const provider of ['openai-codex','openai']) {
 const path=await makePatch('cpp',{id,label:id,reasoning:'high',provider},process.env.REA_GPT_TEMP,{publicDir:'/frozen/target-only'});
 const patches=parse(await readFile(path,'utf8'));
 const selected=patches.find(p=>p.id==='agent-default-model').config;
 const definition=patches.find(p=>p.id==='llm-pi-ai').config.providers[provider];
 results.push({id,provider,selected,definition,patches});
}
process.stdout.write(JSON.stringify(results));
"""
        command = [str(root / '.tools/node-v24.22.0-linux-x64/bin/node'), '--input-type=module', '-e', script]
        result = subprocess.run(command, cwd=root, env={**os.environ, 'REA_GPT_IDS': json.dumps(ids),
            'REA_GPT_TEMP': temporary}, capture_output=True, text=True, check=True)
        rows = json.loads(result.stdout)
        for provider in ['openai-codex', 'openai']:
            baseline = None
            for row in [r for r in rows if r['provider'] == provider]:
                assert row['selected'] == {'provider': provider, 'model': row['id'], 'reasoningEffort': 'high'}
                definition = row['definition']['models'][0]
                assert definition['id'] == row['id'] and definition['contextWindow'] == 1050000
                assert definition['maxTokens'] == 8192
                assert row['definition'].get('retryPolicy', {}).get('maxRetries', 0) == 0
                normalized = copy.deepcopy(row['patches'])
                for patch in normalized:
                    if patch.get('id') == 'agent-default-model':
                        patch['config']['model'] = '<model>'
                    if patch.get('id') == 'llm-pi-ai':
                        for spec in patch['config']['providers'][provider]['models']:
                            spec['id'] = '<model>'
                            if 'name' in spec:
                                spec['name'] = '<model>'
                if baseline is None:
                    baseline = normalized
                assert normalized == baseline, 'GPT model selection changed tools, prompts or harness controls'
    prices = records.read(root / catalog['gpt-6-luna']['pricing_file'])
    for mid in ids[1:]:
        for tokens in [10000, 300000]:
            usage = {'inputTokens': tokens, 'cacheReadTokens': 1000, 'outputTokens': 2000}
            receipt = {'model': mid, 'provider': 'openai-codex', 'started_at': '2026-10-20T00:00:00Z', 'reported_usage': usage}
            cost = pricing.estimate(receipt, prices)
            rates = prices[mid]['short']
            multiplier = 2 if tokens + 1000 > 272000 else 1
            expected_low = (tokens * rates['input'] * multiplier + 1000 * rates['cache_read'] * multiplier
                + 2000 * rates['output'] * (1.5 if multiplier == 2 else 1)) / 1000000
            expected_high = expected_low + tokens * (rates['cache_write'] - rates['input']) * multiplier / 1000000
            assert math.isclose(cost['low_usd'], expected_low)
            assert math.isclose(cost['high_usd'], expected_high) and cost['cache_write_count_unknown']
            receipt['reported_usage'] = {**usage, 'cacheWriteTokens': 0}
            exact = pricing.estimate(receipt, prices)
            assert exact['low_usd'] == exact['high_usd'] and not exact['cache_write_count_unknown']
    data = records.collect()
    original = [r for r in data['records'] if r['model'] in ids[1:]]
    observed = {r['model'] for r in original if any((root / r['directory'] / name).exists() for name in ['task.json', 'result.json', 'events.jsonl'])}
    assert {r['model'] for r in original} == observed, 'Invented historical model measurements'
    active = records.active_condition_rows(data)
    for mid in ids[1:]:
        assert len([r for r in active if r['model'] == mid]) == 11
    import run
    listed = subprocess.run([str(root / 'bin/benchmark'), 'run', '--models', ids[1], ids[2], ids[1],
        '--condition', 'paged-evidence-v2', '--report-every', '0', '--dry-run'],
        cwd=root, capture_output=True, text=True, check=True)
    planned = json.loads(listed.stdout)
    assert planned['model_calls'] == 0 and len(planned['tasks']) == 16
    assert {entry['model'] for entry in planned['tasks']} == set(ids[1:])
    assert len({(entry['experiment'], entry['case'], entry['model']) for entry in planned['tasks']}) == 16
    for extra in [['--report-every', '-1'], ['--model', ids[0]]]:
        invalid = subprocess.run([str(root / 'bin/benchmark'), 'run', '--models', ids[1],
            '--dry-run', *extra], cwd=root, capture_output=True, text=True)
        assert invalid.returncode != 0, 'Invalid batch options were accepted'
    verified = 0
    for mid in ids[1:]:
        entries = run.plan(suite='record-parser', model=mid, condition='lossless-evidence-v1')
        entries += run.plan(model=mid, condition='paged-evidence-v2')
        for entry in entries:
            if entry['status'] != 'cached':
                continue
            saved = records.read(root / entry['directory'] / 'task.json')
            profile = saved['environment_profile']
            archive = records.read(records.BENCH / 'config/environment-profile-archives.json')['profiles'][profile['fingerprint']]
            assert archive['profile'] == profile
            for name, digest in archive['files_sha256'].items():
                assert records.sha(root / name) == digest, 'Saved harness source archive changed'
            import cache
            expected = run.prepare_task(entry, catalog[mid], materialize=False)
            assert cache.legacy_fingerprint(entry, catalog[mid], saved, expected) == saved['fingerprint']
            reference = records.read(records.attempt_directory(entry['experiment'], entry['case'],
                'gpt-6.1-sol', entry['condition']) / 'task.json')
            for key in ['binary_sha256', 'prompt_sha256', 'tool_count', 'max_tool_calls',
                        'timeout_seconds', 'max_output_tokens', 'standard', 'string_abi', 'reasoning']:
                assert saved.get(key) == reference.get(key), 'GPT pilot changed a frozen comparison input: ' + key
            actual_tools = Path(saved['input_directory']) / 'tools.json'
            reference_tools = Path(reference['input_directory']) / 'tools.json'
            assert actual_tools.read_bytes() == reference_tools.read_bytes()
            verified += 1
    print(f'PASS: exact GPT IDs on both routes; equal prompts/tools/budgets; short/long price bounds; '
          f'explicit deduplicated batch selection; no invented history; '
          f'{verified} saved caches match GPT-6.1 inputs and archived source profiles; zero model calls')


if __name__ == '__main__':
    main()
