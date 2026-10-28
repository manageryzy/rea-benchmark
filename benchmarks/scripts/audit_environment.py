#!/usr/bin/python3
"""Offline audit of DSH durable tool inputs against saved gateway evidence."""
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import records
import cache

ROOT = records.ROOT
OUTPUT = records.BENCH / 'config/environment-audit.json'
DSH = '.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/'
SPILL = ' Full formatted result stored at: '


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require_zstd():
    executable = shutil.which('zstd')
    if executable is None:
        raise RuntimeError('Historical session audit requires zstd inside the pinned runtime; '
                           'refresh the image with --refresh --reuse-runtime')
    return executable


def durable_session(raw, session_id):
    # Decode exactly the bytes whose digest the receipt records, not a second file read.
    decoded = subprocess.run([require_zstd(), '-dc'], input=raw, check=True,
                             capture_output=True, timeout=60).stdout
    events = [json.loads(line) for line in decoded.splitlines() if line.strip()]
    if not events or events[0].get('id') != session_id:
        raise ValueError('Durable session identity mismatch')
    return events


def text(message):
    return '\n'.join(b['text'] for b in message.get('content', []) if b.get('type') == 'text')


def receipt_current(audit, read_bytes):
    if not audit.get('inputs_sha256'):
        return False
    parents = {}
    for path, expected in audit['inputs_sha256'].items():
        try:
            source = cache.confined_path(ROOT, path, parents)
        except ValueError:
            return False
        if not source.is_relative_to(records.BENCH / 'runs') or not source.is_file():
            return False
        if digest(read_bytes(source)) != expected:
            return False
    return True


def audit_case(row, sessions, evidence_prefix='evidence-', verify_errors=False):
    directory = ROOT / row['directory']
    inputs = {}

    def read(path):
        raw = path.read_bytes()
        inputs[str(path.relative_to(ROOT))] = digest(raw)
        return raw

    result = {'experiment': row['experiment'], 'case': row['case']['id'], 'model': row['model'],
              'directory': row['directory'], 'verification_layer': 'durable model-input surface, not HTTP wire',
              'status': 'unverified', 'inputs_sha256': inputs}
    for name in ('result.json', 'tool-calls.jsonl', 'runtime.patch.yml'):
        if (directory / name).exists():
            read(directory / name)
    events = [json.loads(line) for line in read(directory / 'events.jsonl').splitlines() if line.strip()]
    session_id = next((x['sessionId'] for x in events if x['type'] == 'session'), None)
    paths = sessions.get(session_id, [])
    if len(paths) != 1:
        result['reason'] = 'Missing or ambiguous durable session; log previews cannot certify model inputs'
        return result
    path = paths[0]
    raw = path.read_bytes()
    # This explicit audit target may decompress saved files. Report builds only read its receipt.
    durable = durable_session(raw, session_id)
    result['session_source'] = {'path': str(path.relative_to(ROOT)), 'sha256': digest(raw), 'id': session_id}
    calls = [e for e in events if e['type'] == 'tool_call']
    call_order = {c['callId']: (i + 1, c) for i, c in enumerate(calls)}
    latest = {}
    for event in durable:
        if event['type'] == 'tool/result':
            latest[event['data']['message']['toolCallId']] = event
    header = next(e['data']['header'] for e in durable if e['type'] == 'request/header')
    tools = header.get('tools', [])
    names = {t['name'] for t in tools}
    system_messages = [text(e['data']['message']) for e in durable if e['type'] == 'system/message']
    tool_details = []
    for call_id, event in latest.items():
        sequence, call = call_order.get(call_id, (None, {}))
        message = event['data']['message']
        delivered = text(message)
        detail = {'sequence': sequence, 'tool': call.get('tool'), 'delivered_chars': len(delivered),
                  'delivered_sha256': digest(delivered.encode()), 'event_seq': event['seq'],
                  'status': 'unverified', 'recovery_tools_available': False}
        if message.get('isError'):
            detail['status'] = 'tool_error'
            if verify_errors and sequence is not None:
                evidence_paths = list(directory.glob(f'{evidence_prefix}{sequence:02d}-*.json'))
                primary = text(json.loads(read(evidence_paths[0]))) if len(evidence_paths) == 1 else ''
                detail['error_primary_complete'] = bool(primary) and primary in delivered
                detail['error_primary_sha256'] = digest(primary.encode())
        elif sequence is not None:
            evidence_paths = list(directory.glob(f'{evidence_prefix}{sequence:02d}-*.json'))
            if len(evidence_paths) == 1:
                evidence = json.loads(read(evidence_paths[0]))
                primary = text(evidence)
                detail.update(evidence_path=str(evidence_paths[0].relative_to(ROOT)),
                              original_chars=len(primary), original_sha256=digest(primary.encode()))
                if delivered == primary:
                    detail['status'] = 'primary_complete'
                elif SPILL in delivered and '[...]' in delivered and len(delivered) < len(primary):
                    detail['status'] = 'spilled'
                    detail['recovery_tools_available'] = 'read' in names or 'grep' in names
                else:
                    detail['status'] = 'changed_or_unverified'
        tool_details.append(detail)
    types = Counter(e['type'] for e in durable)
    pruned = sum(n for t, n in types.items() if t.startswith('compaction/') or t.startswith('condensation/'))
    statuses = Counter(t['status'] for t in tool_details)
    unverified_errors = sum(t['status'] == 'tool_error' and not t.get('error_primary_complete') for t in tool_details) if verify_errors else 0
    complete_trace = len(latest) == len(calls) == (row['receipt'] or {}).get('tool_calls')
    result.update(status='delivery_limited' if statuses['spilled'] or pruned else
                  'primary_complete' if complete_trace and not statuses['changed_or_unverified'] and not statuses['unverified'] and not unverified_errors else 'unverified',
                  complete_trace=complete_trace, canonical_tool_results=len(latest),
                  log_shortened_results=sum(bool(e.get('truncated')) for e in events if e['type'] == 'tool_result'),
                  compaction_events=pruned, counts=dict(statuses), tools=tool_details,
                  error_delivery_unverified=unverified_errors,
                  blocked_spill_recovery=sum(t['status'] == 'spilled' and not t['recovery_tools_available'] for t in tool_details),
                  system_sha256=digest('\n'.join(system_messages).encode()),
                  system_chars=sum(map(len, system_messages)),
                  task_prompt_sha256=(row['receipt'] or {}).get('prompt_sha256'),
                  target_sha256=(row['receipt'] or {}).get('binary_sha256'),
                  tool_names=sorted(names), tool_schemas_sha256=digest(json.dumps(tools, sort_keys=True).encode()),
                  tool_schemas=tools, request_config={k: v for k, v in header.get('config', {}).items()
                                                      if k in ('provider', 'model', 'reasoningEffort', 'maxTokens')})
    return result


def for_report(data, read_bytes):
    saved = json.loads(read_bytes(OUTPUT)) if OUTPUT.exists() else {'cases': [], 'limits': {}}
    indexed = {(c['experiment'], c['case'], c['model']): c for c in saved['cases']}
    glm = {(c['experiment'], c['case']): c for c in data['glm_diagnosis']['cases']}
    cases = []
    for row in data['records']:
        if row['role'] != 'primary':
            continue
        key = (row['experiment'], row['case']['id'], row['model'])
        entry = {'experiment': key[0], 'case': key[1], 'model': key[2],
                 'label': row['case']['label'], 'status': 'unverified', 'counts': {}}
        if row['harness'] == 'deepseek-harness' and key in indexed:
            audit = indexed[key]
            verified = receipt_current(audit, read_bytes)
            entry.update(audit, inputs_current=verified)
            if not verified:
                entry['status'] = 'unverified'
                entry['reason'] = 'Audit receipt does not match current saved inputs'
                entry['recorded_counts'] = entry.get('counts', {})
                entry['counts'] = {}
                entry['blocked_spill_recovery'] = 0
        elif key[:2] in glm:
            diagnostic = glm[key[:2]]
            cuts = sum(x.get('primary_cut') is True for x in diagnostic['truncations'])
            entry.update(status='delivery_limited' if cuts else 'wrapper_differs',
                         primary_cuts=cuts, clipped_results=len(diagnostic['truncations']),
                         verification_layer='cached HTTP request body')
        cases.append(entry)
    checks = []
    for experiment, case in sorted({(c['experiment'], c['case']) for c in cases}):
        selected = [r for r in data['records'] if r['role'] == 'primary' and
                    r['experiment'] == experiment and r['case']['id'] == case]
        targets = [(r['receipt'] or {}).get('binary_sha256') for r in selected]
        prompts = [(r['receipt'] or {}).get('prompt_sha256') for r in selected]
        dsh = [next((c for c in cases if (c['experiment'], c['case'], c['model']) ==
                     (experiment, case, model) and c.get('inputs_current')), None)
               for model in ['deepseek-flash', 'gpt-6.1-sol']]
        checks.append({'experiment': experiment, 'case': case,
                       'target_match': len(set(targets)) == 1 if all(targets) else None,
                       'task_prompt_match': len(set(prompts)) == 1 if all(prompts) else None,
                       'dsh_system_match': dsh[0].get('system_sha256') == dsh[1].get('system_sha256') if all(dsh) else None,
                       'dsh_tool_schemas_match': dsh[0].get('tool_schemas_sha256') == dsh[1].get('tool_schemas_sha256') if all(dsh) else None})
    return {'certified_model_only_comparison': False,
            'verdict': 'Environment-confounded setup comparison; original behavioral grades retained',
            'reviewed_on': saved.get('reviewed_on'), 'limits': saved['limits'],
            'implementation_sha256': saved.get('implementation_sha256', {}),
            'cases': cases, 'matched_input_checks': checks}


def main():
    require_zstd()
    data = records.collect()
    if not data['execution']['experiments_stopped']:
        raise SystemExit('Stop experiments before auditing saved inputs')
    sessions = {}
    for path in sorted((ROOT / '.cache/benchmark-runtime').glob('home-*/sessions/*/*/session.v4.jsonl.zstd')):
        sessions.setdefault(path.parent.name, []).append(path)
    cases = [audit_case(row, sessions) for row in data['records']
             if row['role'] == 'primary' and row['harness'] == 'deepseek-harness']
    files = ['benchmarks/harnesses/deepseek-harness.mjs', 'benchmarks/scripts/policy.mjs',
             DSH + 'dsh-headless/lib/json-stream-BA-F3lfb.js', DSH + 'dsh-mcp-client/lib/index.js',
             DSH + 'dsh-spill-policy/lib/index.js', DSH + 'dsh-compaction-tool-result-pruner/lib/index.js',
             DSH + 'dsh-agent-loop/lib/index.js', DSH + 'dsh-base/cordis.patch.yml']
    review = {'schema_version': 1, 'reviewed_on': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat(),
              'scope': 'Existing primary DSH cases; offline saved-session and evidence audit; zero model calls',
              'runtime': 'DeepSeek Harness 0.2.1-rc.2',
              'implementation_sha256': {p: digest((ROOT / p).read_bytes()) for p in files},
              'limits': {'headless_log_string_bytes': 8192, 'headless_log_event_bytes': 32768,
                         'spill_estimated_tokens': 12500, 'pruner_threshold_codepoints': 8192,
                         'pruner_head_codepoints': 4096, 'pruner_tail_codepoints': 1024,
                         'pruner_requires_context_pressure': True},
              'assurance': 'Environment-independent model ranking is not certified. Behavioral grades describe the recorded setups.',
              'cases': cases}
    OUTPUT.write_text(json.dumps(review, indent=2, sort_keys=True) + '\n')
    print(f'OFFLINE ENVIRONMENT AUDIT: {len(cases)} cases; '
          f'{sum(c.get("counts", {}).get("spilled", 0) for c in cases)} spilled tool results; '
          f'{sum(c.get("blocked_spill_recovery", 0) for c in cases)} blocked recovery notices; zero model calls')


if __name__ == '__main__':
    main()
