"""Read-only GLM cache diagnostics. Export counts and hashes, never thinking text."""
from collections import Counter
import datetime
import hashlib
import json
import re


TRUNCATION = '[Tool output truncated by resultBudget:'
SOURCES = [
    {'title': 'BigModel GLM-5.3 reasoning controls',
     'url': 'https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3'},
    {'title': 'Z.ai interleaved and preserved thinking',
     'url': 'https://docs.z.ai/guides/capabilities/thinking-mode'},
]


def stream_summary(raw):
    reasoning_hash = hashlib.sha256()
    reasoning_chars, content, tool_ids, finishes, usage_events, done = 0, [], {}, [], 0, False
    for line in raw.splitlines():
        if not line.startswith(b'data:'):
            continue
        if line[5:].strip() == b'[DONE]':
            done = True
            continue
        try:
            event = json.loads(line[5:])
        except (ValueError, UnicodeError):
            continue
        usage_events += bool(event.get('usage'))
        for choice in event.get('choices', []):
            delta = choice.get('delta') or {}
            reasoning = delta.get('reasoning_content') or ''
            if isinstance(reasoning, str):
                reasoning_chars += len(reasoning)
                reasoning_hash.update(reasoning.encode())
            text = delta.get('content')
            if isinstance(text, str):
                content.append(text)
            for call in delta.get('tool_calls') or []:
                if call.get('id'):
                    tool_ids.setdefault(call.get('index', 0), call['id'])
            if choice.get('finish_reason'):
                finishes.append(choice['finish_reason'])
    return {'reasoning_chars': reasoning_chars, 'reasoning_sha256': reasoning_hash.hexdigest(),
            'content_chars': sum(map(len, content)), 'has_content_delta': bool(content),
            'tool_ids': sorted(tool_ids.values()),
            'finish_reasons': finishes, 'usage_events': usage_events, 'done': done}, ''.join(content)


def seconds(start, end):
    if not start or not end:
        return None
    return max(0, (datetime.datetime.fromisoformat(end.replace('Z', '+00:00')) -
                   datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds())


def collect(rows, root, read_bytes):
    def read(path, default=None):
        return json.loads(read_bytes(path)) if path.exists() else default

    cases = []
    for row in rows:
        if row['model'] != 'glm-5.3':
            continue
        directory = root / row['directory']
        request_paths = sorted((directory / 'wire').glob('request-*.json'))
        if not request_paths:
            continue
        first = read(request_paths[0])
        last = first if len(request_paths) == 1 else read(request_paths[-1])
        receipt = row['receipt'] or {}
        index = read(directory / 'wire-index.json', {})
        metadata = receipt.get('requests') or index.get('requests') or []
        request_metadata = {r['sequence']: r for r in metadata}
        assistants = [m for m in last['messages'] if m['role'] == 'assistant']
        historical = {tuple(sorted(c['id'] for c in m.get('tool_calls', []))): m
                      for m in assistants if m.get('tool_calls')}
        calls = {}
        for message in assistants:
            for call in message.get('tool_calls', []):
                calls[call['id']] = (len(calls) + 1, call['function'])
        traces = []
        trace_path = directory / 'tool-calls.jsonl'
        if trace_path.exists():
            traces = [json.loads(line) for line in read_bytes(trace_path).splitlines() if line.strip()]
        streams, texts, continuity = [], [], Counter()
        for path in sorted((directory / 'wire').glob('response-*.sse')):
            sequence = int(re.search(r'(\d+)', path.stem)[1])
            item, text = stream_summary(read_bytes(path))
            meta = request_metadata.get(sequence, {})
            item.update(sequence=sequence, seconds=seconds(meta.get('started_at'), meta.get('finished_at')),
                        completed_http=bool(meta.get('finished_at')))
            if item['seconds'] is None and receipt.get('timed_out'):
                item['cutoff_window_seconds'] = seconds(meta.get('started_at'), receipt.get('completed_at'))
            previous = historical.get(tuple(item['tool_ids'])) if item['tool_ids'] else None
            if previous is not None and item['reasoning_chars']:
                previous_reasoning = previous.get('reasoning_content') or ''
                matches = hashlib.sha256(previous_reasoning.encode()).hexdigest() == item['reasoning_sha256']
                continuity['matched' if matches else 'different'] += 1
            streams.append(item)
            if item['has_content_delta']:
                texts.append(text)
        joined = '\n\n'.join(texts)
        answer_path = directory / 'answer.md'
        answer = read_bytes(answer_path).decode() if answer_path.exists() else None
        answer_matches = answer == joined if answer is not None else None
        code_pattern = r'```(?:cpp|c\+\+|rust|go)\s*\n.*?\n```'
        saved_blocks = re.findall(code_pattern, answer, re.S) if answer is not None else None
        wire_blocks = re.findall(code_pattern, joined, re.S)
        tools = [m for m in last['messages'] if m['role'] == 'tool']
        duplicate_chars, duplicate_count = 0, 0
        for message in tools:
            content = message.get('content') or ''
            if 'Structured content:\n' not in content:
                continue
            primary, secondary = content.split('Structured content:\n', 1)
            try:
                identical = json.loads(primary) == json.loads(secondary)
            except ValueError:
                identical = False
            if identical:
                duplicate_count += 1
                duplicate_chars += len(secondary) + len('Structured content:\n')
        clipped = []
        for message in tools:
            content = message.get('content') or ''
            if TRUNCATION not in content:
                continue
            sequence, function = calls.get(message['tool_call_id'], (None, {}))
            entry = {'sequence': sequence, 'tool': function.get('name', '').removeprefix('mcp__rea__'),
                     'arguments': function.get('arguments'), 'model_chars': len(content)}
            original = re.search(r'originalBytes=(\d+)', content)
            entry['original_model_bytes'] = int(original[1]) if original else None
            evidence_paths = list(directory.glob(f'evidence-{sequence:02d}-*.json')) if sequence else []
            if evidence_paths:
                evidence = read(evidence_paths[0])
                primary = '\n'.join(c['text'] for c in evidence.get('content', []) if c.get('type') == 'text')
                prefix = content.rsplit('\n\n' + TRUNCATION, 1)[0]
                entry['primary_chars'] = len(primary)
                entry['primary_cut'] = not prefix.startswith(primary) if primary else None
                entry['evidence_path'] = str(evidence_paths[0].relative_to(root))
            clipped.append(entry)
        successful_queries = Counter((x['tool'], json.dumps(x['arguments'], sort_keys=True))
                                     for x in traces if not x['is_error'])
        hashes = [m['request_sha256'] for m in metadata if m.get('request_sha256')]
        system = '\n'.join(m['content'] for m in first['messages'] if m['role'] == 'system')
        task = next((m['content'] for m in reversed(first['messages']) if m['role'] == 'user'), '')
        runtime = read(directory / 'runtime-config.json', {})
        cases.append({'experiment': row['experiment'], 'experiment_label': row['experiment_label'],
            'case': row['case']['id'], 'label': row['case']['label'],
            'directory': row['directory'], 'status': row['status'], 'timed_out': receipt.get('timed_out', False),
            'elapsed_seconds': receipt.get('elapsed_seconds'), 'tool_seconds_sum': sum(x['elapsed_ms'] for x in traces) / 1000,
            'tool_calls': len(traces), 'tool_errors': sum(x['is_error'] for x in traces),
            'data_tool_calls': sum(x['tool'] in ('read_bytes', 'search_bytes', 'search_strings', 'list_strings') for x in traces),
            'repeated_successful_queries': sum(n - 1 for n in successful_queries.values()),
            'request_count': len(request_paths), 'identical_request_retries': len(hashes) - len(set(hashes)),
            'http_errors': sum(x.get('http_status', 200) >= 400 for x in metadata),
            'upstream_error': receipt.get('upstream_error') or index.get('upstream_error'),
            'first_request_bytes': len(read_bytes(request_paths[0])),
            'last_request_bytes': len(read_bytes(request_paths[-1])),
            'last_tool_chars': sum(len(m.get('content') or '') for m in tools),
            'last_history_reasoning_chars': sum(len(m.get('reasoning_content') or '') for m in assistants),
            'last_structured_duplicates': sum('Structured content:\n' in (m.get('content') or '') for m in tools),
            'verified_duplicate_count': duplicate_count, 'verified_duplicate_chars': duplicate_chars,
            'system_chars': len(system), 'system_completion_pressure': 'Do not stop because the context or session is long' in system,
            'system_final_no_tools_after': 'with no tool calls after it' in system,
            'task_code_before_close': bool(re.search(r'code block.*?then close the binary', task, re.I | re.S)),
            'reasoning_effort': first.get('reasoning_effort'), 'thinking': first.get('thinking'),
            'max_tokens_per_request': first.get('max_tokens', first.get('max_completion_tokens')),
            'compaction_enabled': runtime.get('features', {}).get('compact'),
            'reasoning_history_matches': continuity['matched'], 'reasoning_history_differences': continuity['different'],
            'reasoning_chars': sum(s['reasoning_chars'] for s in streams),
            'content_chars': sum(s['content_chars'] for s in streams),
            'native_code_blocks': len(wire_blocks), 'saved_native_blocks': len(saved_blocks) if saved_blocks is not None else None,
            'answer_matches_wire': answer_matches,
            'native_blocks_match_wire': saved_blocks == wire_blocks if saved_blocks is not None else None,
            'finish_reasons': dict(Counter(reason for s in streams for reason in s['finish_reasons'])),
            'truncations': clipped, 'streams': streams})
    review = read(root / 'benchmarks/config/glm-diagnostic-review.json', {})
    return {'scope': 'All cached GLM attempts mapped to existing benchmark cases; no inference or grading',
            'count_unit': 'Unicode characters, not tokenizer tokens; partial streams are included',
            'causal_limit': 'Cached observations identify mechanisms and configuration issues, not the gain from untested fixes.',
            'sources_verified_on': '2026-10-19', 'sources': SOURCES,
            'implementation_review': review, 'cases': cases}
